"""Thin client over the Google Health API read methods.

We use two of the four read methods:
  - `list`        -> intraday / detailed data points (minute-level steps, HR, ...)
  - `dailyRollUp` -> per-day aggregates (the daily summary tiles)

Both paginate via nextPageToken. A 401 means the token died -> TokenExpiredError.

Docs: https://developers.google.com/health/endpoints
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterator

import requests
from google.oauth2.credentials import Credentials

from .auth import TokenExpiredError
from .config import API_BASE, DataType

_TIMEOUT = 30


def _civil_date(d: date) -> dict[str, int]:
    """A Google Health CivilDate object ({year, month, day})."""
    return {"year": d.year, "month": d.month, "day": d.day}


def _rollup_max_days(resp: requests.Response) -> int | None:
    """If `resp` is an INVALID_ROLLUP_QUERY_DURATION rejection, return the per-type
    maxDurationDays the API reported; otherwise None."""
    try:
        for detail in resp.json().get("error", {}).get("details", []):
            cap = detail.get("metadata", {}).get("maxDurationDays")
            if cap is not None:
                return int(cap)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


class HealthClient:
    def __init__(self, creds: Credentials):
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {creds.token}",
                "Accept": "application/json",
            }
        )

    def _request(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        resp = self._session.request(method, url, timeout=_TIMEOUT, **kwargs)
        if resp.status_code == 401:
            raise TokenExpiredError("API returned 401 — re-run `python cli.py auth`.")
        if resp.status_code == 429:
            raise RuntimeError("Rate limited by the Health API (429). Retry later.")
        resp.raise_for_status()
        return resp.json()

    def list_intraday(
        self, dt: DataType, start: date, end: date, page_size: int = 1000
    ) -> Iterator[dict[str, Any]]:
        """Yield raw dataPoints for [start, end) using the `list` method + a time filter.

        The filter field depends on the data type's time shape (sample/interval/session).
        """
        url = f"{API_BASE}/dataTypes/{dt.api_name}/dataPoints"
        field = f"{dt.field_name}.{dt.time_kind.filter_field}"
        # physical_time is an RFC3339 UTC instant, so the bounds need the trailing Z.
        flt = (
            f'{field} >= "{start.isoformat()}T00:00:00Z" '
            f'AND {field} < "{end.isoformat()}T00:00:00Z"'
        )
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"filter": flt, "pageSize": page_size}
            if page_token:
                params["pageToken"] = page_token
            body = self._request("GET", url, params=params)
            yield from body.get("dataPoints", [])
            page_token = body.get("nextPageToken")
            if not page_token:
                break

    # dailyRollUp caps how much a single query may cover: window_size_days * page_size
    # (and the range span) must not exceed a per-type maxDurationDays. 90 is the largest
    # cap seen; some types (heart-rate, total-calories) are lower (14). We start here and
    # shrink adaptively when the API tells us the real cap, so we never hardcode per-type
    # limits and long backfills / post-gap catch-ups keep working.
    _MAX_ROLLUP_DAYS = 90

    def list_all(self, api_name: str, page_size: int = 1000) -> Iterator[dict[str, Any]]:
        """Yield every dataPoint for a type via `list` with no filter (paginated).

        Used for low-volume types (daily-* summaries, sleep/exercise sessions): the whole
        history is small, so a date filter isn't worth it and idempotent upserts make
        re-fetching cheap. NOT for high-frequency types like heart-rate."""
        url = f"{API_BASE}/dataTypes/{api_name}/dataPoints"
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"pageSize": page_size}
            if page_token:
                params["pageToken"] = page_token
            body = self._request("GET", url, params=params)
            yield from body.get("dataPoints", [])
            page_token = body.get("nextPageToken")
            if not page_token:
                break

    def daily_rollup(
        self, dt: DataType, start: date, end: date, page_size: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield per-day rollup points for [start, end) via the `dailyRollUp` method,
        splitting the request into windows small enough for the type's duration cap."""
        url = f"{API_BASE}/dataTypes/{dt.api_name}/dataPoints:dailyRollUp"
        max_days = self._MAX_ROLLUP_DAYS
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=max_days), end)
            try:
                # windowSizeDays * pageSize must be <= max_days; with windowSizeDays=1 the
                # window count equals the day span, so pageSize=max_days always fits.
                yield from self._rollup_window(url, chunk_start, chunk_end, max_days)
            except requests.HTTPError as exc:
                cap = _rollup_max_days(exc.response)
                if cap is not None and cap < max_days:
                    max_days = cap  # retry this same chunk with the type's real cap
                    continue
                raise
            chunk_start = chunk_end

    def _rollup_window(
        self, url: str, start: date, end: date, max_days: int
    ) -> Iterator[dict[str, Any]]:
        page_token: str | None = None
        while True:
            payload: dict[str, Any] = {
                # range.start/end are CivilDateTime values: the y/m/d live in a nested
                # `date` (CivilDate), not flat on the object.
                "range": {
                    "start": {"date": _civil_date(start)},
                    "end": {"date": _civil_date(end)},
                },
                "windowSizeDays": 1,
                "pageSize": max_days,
            }
            if page_token:
                payload["pageToken"] = page_token
            body = self._request("POST", url, json=payload)
            yield from body.get("rollupDataPoints", [])
            page_token = body.get("nextPageToken")
            if not page_token:
                break
