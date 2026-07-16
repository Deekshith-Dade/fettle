"""Settings and the data-type registry.

The registry is the single source of truth for *what* fettle knows how to sync.
Each entry maps a Google Health API data type to:
  - the OAuth scope it lives under,
  - how its timestamps are shaped (sample / interval / session),
  - which read style we use (intraday `list` vs `dailyRollUp`),
  - the value field(s) we pull out of each data point for the tidy tables.

Docs: https://developers.google.com/health/data-types
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # the backend/ dir

API_BASE = "https://health.googleapis.com/v4/users/me"


class Settings(BaseSettings):
    """Runtime settings, overridable via env vars or backend/.env."""

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    credentials_file: Path = BASE_DIR / "credentials.json"
    token_file: Path = BASE_DIR / "token.json"
    db_file: Path = BASE_DIR / "health.db"

    # Must match a redirect URI registered on the OAuth client in Cloud Console.
    oauth_redirect_uri: str = "http://localhost:8400/auth/callback"

    # How far back to reach on the very first sync of a data type.
    # Daily rollups are cheap (one point/day); intraday can be thousands of points/day
    # (heart-rate is sub-minute), so its first backfill is deliberately short — enough for
    # the "today"/recent detail graphs, not a deep archive.
    initial_backfill_days: int = 90
    initial_intraday_days: int = 3

    # Allow the Next.js dev server to call the API. Both localhost and 127.0.0.1 so the
    # dashboard works regardless of which the browser resolves to.
    cors_origins: list[str] = ["http://localhost:3400", "http://127.0.0.1:3400"]

    # Private-network origins may also call the API: the dashboard loaded over Tailscale
    # (100.64/10 CGNAT IPs, MagicDNS *.ts.net names) or the home LAN derives its API base
    # from the same host it was served from. Never widen this to a public pattern.
    cors_origin_regex: str = (
        r"^https?://("
        r"localhost|127\.0\.0\.1|\[::1\]"
        r"|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"  # Tailscale 100.64/10
        r"|[A-Za-z0-9-]+\.[A-Za-z0-9-]+\.ts\.net"                     # MagicDNS names
        r"|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"  # home LAN
        r")(:\d+)?$"
    )

    # Where the OAuth callback sends the browser after a successful connect.
    frontend_url: str = "http://localhost:3400"

    # Owner profile — used by age/sex-referenced analytics (Vital Age, benchmarks).
    # Google's `profile` scope isn't reliably populated, so set your real values in
    # backend/.env (birth_date=YYYY-MM-DD, sex=male|female) — these are placeholders,
    # kept out of config so no birth date lives in the repo.
    birth_date: str = "1995-01-01"
    sex: str = "male"


settings = Settings()


# OAuth scope prefix shared by all Health API scopes.
_SCOPE_PREFIX = "https://www.googleapis.com/auth/googlehealth."


class Scope(str, Enum):
    ACTIVITY = "activity_and_fitness"
    HEALTH_METRICS = "health_metrics_and_measurements"
    SLEEP = "sleep"
    NUTRITION = "nutrition"
    PROFILE = "profile"

    @property
    def readonly(self) -> str:
        return f"{_SCOPE_PREFIX}{self.value}.readonly"


class TimeKind(str, Enum):
    """Shape of a data point's timestamp, which drives the `list` filter field."""

    SAMPLE = "sample"      # point-in-time reading  -> {type}.sample_time.physical_time
    INTERVAL = "interval"  # spans a window         -> {type}.time_interval.start_time
    SESSION = "session"    # named session          -> (session member TBD; see roadmap)

    @property
    def filter_field(self) -> str:
        # The `list` filter targets the RFC3339 physical (UTC) instant on each point's
        # time member. Member names come from the API response shape, e.g. heart-rate is
        # `heartRate.sampleTime.physicalTime` -> filter `heart_rate.sample_time.physical_time`.
        return {
            TimeKind.SAMPLE: "sample_time.physical_time",
            TimeKind.INTERVAL: "time_interval.start_time",
            TimeKind.SESSION: "session_time.start_time",  # not yet verified against the API
        }[self]


@dataclass(frozen=True)
class DataType:
    """One Health API data type we know how to sync."""

    api_name: str          # kebab-case, used in the URL path: .../dataTypes/{api_name}/...
    scope: Scope
    time_kind: TimeKind
    label: str             # human label for the dashboard
    unit: str = ""
    supports_daily_rollup: bool = True
    supports_intraday: bool = True  # whether the intraday `list` call is worthwhile
    # `daily-*` summary types: one point/day, delivered via `list` (no dailyRollUp) and
    # shaped as `{camelType}.date` (a CivilDate). Fetched whole and stored as daily
    # metrics keyed by that date. When True the two flags above are ignored.
    daily_via_list: bool = False
    # Value handling: multiply the extracted number by `value_scale` (unit conversions,
    # e.g. grams->kg, mm->km); `sum_fields` sums *all* numeric leaves instead of picking
    # one (e.g. active-zone-minutes = fat-burn + cardio + peak zone sums).
    value_scale: float = 1.0
    sum_fields: bool = False
    # value = first field minus second (e.g. skin-temp variation = nightly − baseline),
    # each located anywhere in the point. Overrides the normal single-value extraction.
    diff_fields: tuple[str, str] | None = None
    # Derived types are computed from session sources (sleep, exercise) by dedicated
    # processors in sync.py, not fetched from the API. They still show on the dashboard.
    derived: bool = False

    @property
    def field_name(self) -> str:
        """The snake_case key under which values appear in API responses & filters."""
        return self.api_name.replace("-", "_")


# The starting registry. Add rows here to teach fettle a new metric.
REGISTRY: list[DataType] = [
    # --- Activity & fitness (interval types are dailyRollUp-only; no `list` support) ---
    DataType("steps", Scope.ACTIVITY, TimeKind.INTERVAL, "Steps", "steps", supports_intraday=False),
    DataType("distance", Scope.ACTIVITY, TimeKind.INTERVAL, "Distance", "km",
             supports_intraday=False, value_scale=1e-6),  # millimetersSum -> km
    DataType("floors", Scope.ACTIVITY, TimeKind.INTERVAL, "Floors", "floors", supports_intraday=False),
    DataType("active-zone-minutes", Scope.ACTIVITY, TimeKind.INTERVAL, "Active Zone Minutes", "min",
             supports_intraday=False, sum_fields=True),  # fat-burn + cardio + peak
    DataType("total-calories", Scope.ACTIVITY, TimeKind.INTERVAL, "Total Calories", "kcal", supports_intraday=False),
    DataType("active-energy-burned", Scope.ACTIVITY, TimeKind.INTERVAL, "Active Calories", "kcal", supports_intraday=False),
    # --- Heart & body metrics ---
    DataType("heart-rate", Scope.HEALTH_METRICS, TimeKind.SAMPLE, "Heart Rate", "bpm"),
    # Raw sample streams (stored intraday); `list`-only, no dailyRollUp.
    DataType("oxygen-saturation", Scope.HEALTH_METRICS, TimeKind.SAMPLE, "SpO2 (samples)", "%",
             supports_daily_rollup=False),
    DataType("heart-rate-variability", Scope.HEALTH_METRICS, TimeKind.SAMPLE, "HRV (samples)", "ms",
             supports_daily_rollup=False),
    DataType("weight", Scope.HEALTH_METRICS, TimeKind.SAMPLE, "Weight", "kg",
             supports_intraday=False, value_scale=0.001),  # weightGramsAvg -> kg
    # --- Daily summary cards (one value/day via `list`; match the Google Health app) ---
    DataType("daily-resting-heart-rate", Scope.HEALTH_METRICS, TimeKind.SAMPLE,
             "Resting Heart Rate", "bpm", daily_via_list=True),
    DataType("daily-heart-rate-variability", Scope.HEALTH_METRICS, TimeKind.SAMPLE,
             "Heart Rate Variability", "ms", daily_via_list=True),
    DataType("daily-oxygen-saturation", Scope.HEALTH_METRICS, TimeKind.SAMPLE,
             "Blood Oxygen (SpO2)", "%", daily_via_list=True),
    DataType("daily-respiratory-rate", Scope.HEALTH_METRICS, TimeKind.SAMPLE,
             "Breathing Rate", "brpm", daily_via_list=True),
    # The app shows nightly deviation from personal baseline, not the absolute reading.
    DataType("daily-sleep-temperature-derivations", Scope.SLEEP, TimeKind.SAMPLE,
             "Skin Temp Variation", "°C", daily_via_list=True,
             diff_fields=("nightlyTemperatureCelsius", "baselineTemperatureCelsius")),
    DataType("daily-vo2-max", Scope.ACTIVITY, TimeKind.SAMPLE,
             "VO2 Max", "mL/kg/min", daily_via_list=True),
    # --- More activity & fitness (dailyRollUp) ---
    DataType("active-minutes", Scope.ACTIVITY, TimeKind.INTERVAL, "Active Minutes", "min",
             supports_intraday=False),
    DataType("altitude", Scope.ACTIVITY, TimeKind.SAMPLE, "Altitude", "m",
             supports_intraday=False),
    DataType("sedentary-period", Scope.ACTIVITY, TimeKind.INTERVAL, "Sedentary Time", "h",
             supports_intraday=False, value_scale=1 / 3600),  # durationSum seconds -> hours
    DataType("swim-lengths-data", Scope.ACTIVITY, TimeKind.INTERVAL, "Swim Strokes", "strokes",
             supports_intraday=False),
    # --- Heart-rate zones (dailyRollUp; per-zone arrays summed to a daily total) ---
    DataType("time-in-heart-rate-zone", Scope.ACTIVITY, TimeKind.INTERVAL, "Time in HR Zones", "min",
             supports_intraday=False, sum_fields=True, value_scale=1 / 60),  # zone seconds -> min
    DataType("calories-in-heart-rate-zone", Scope.ACTIVITY, TimeKind.INTERVAL, "Calories in HR Zones",
             "kcal", supports_intraday=False, sum_fields=True),
    # (daily-heart-rate-zones omitted: `list` returns thousands of granular points, not one
    #  per day; time-in-heart-rate-zone already gives the daily zone breakdown via rollup.)
    # --- More vitals / metabolic (dailyRollUp; no data for this user yet) ---
    DataType("blood-glucose", Scope.HEALTH_METRICS, TimeKind.SAMPLE, "Blood Glucose", "mg/dL",
             supports_intraday=False),
    DataType("core-body-temperature", Scope.HEALTH_METRICS, TimeKind.SAMPLE, "Core Body Temp", "°C",
             supports_intraday=False),
    # --- More body metrics ---
    DataType("height", Scope.HEALTH_METRICS, TimeKind.SAMPLE, "Height", "cm",
             daily_via_list=True, value_scale=0.1),  # heightMillimeters -> cm
    DataType("body-fat", Scope.HEALTH_METRICS, TimeKind.SAMPLE, "Body Fat", "%",
             supports_intraday=False),
    DataType("run-vo2-max", Scope.ACTIVITY, TimeKind.SAMPLE, "Running VO2 Max", "mL/kg/min",
             supports_intraday=False),
    # --- Recovery: computed by readiness.py from the metrics below. ---
    DataType("readiness", Scope.HEALTH_METRICS, TimeKind.SESSION, "Readiness", "",
             derived=True),
    # TRIMP-style training load from the AZM per-zone raw (1·fat-burn + 2·cardio + 3·peak),
    # derived by sync._derive_cardio_load — our stand-in for Fitbit Premium's Cardio Load.
    DataType("cardio-load", Scope.ACTIVITY, TimeKind.SESSION, "Cardio Load", "",
             derived=True),
    # --- Sleep: derived from `sleep` session points (stages) by sync._sync_sleep. ---
    DataType("sleep-duration", Scope.SLEEP, TimeKind.SESSION, "Sleep Duration", "h", derived=True),
    DataType("sleep-rem", Scope.SLEEP, TimeKind.SESSION, "REM Sleep", "h", derived=True),
    DataType("sleep-deep", Scope.SLEEP, TimeKind.SESSION, "Deep Sleep", "h", derived=True),
    DataType("sleep-light", Scope.SLEEP, TimeKind.SESSION, "Light Sleep", "h", derived=True),
    DataType("sleep-awake", Scope.SLEEP, TimeKind.SESSION, "Awake Time", "h", derived=True),
    DataType("sleep-efficiency", Scope.SLEEP, TimeKind.SESSION, "Sleep Efficiency", "%", derived=True),
    # 0-100 nightly score (duration 50 / stage quality 25 / restoration 25) — our stand-in
    # for the app's Sleep Score, computed in sync._sync_sleep.
    DataType("sleep-score", Scope.SLEEP, TimeKind.SESSION, "Sleep Score", "", derived=True),
    # --- Workouts: derived from `exercise` session points by sync._sync_exercise. ---
    DataType("exercise-minutes", Scope.ACTIVITY, TimeKind.SESSION, "Workout Time", "min", derived=True),
    DataType("exercise-count", Scope.ACTIVITY, TimeKind.SESSION, "Workouts", "sessions", derived=True),
    DataType("exercise-distance", Scope.ACTIVITY, TimeKind.SESSION, "Workout Distance", "km", derived=True),
    DataType("exercise-calories", Scope.ACTIVITY, TimeKind.SESSION, "Workout Calories", "kcal", derived=True),
]

REGISTRY_BY_NAME: dict[str, DataType] = {dt.api_name: dt for dt in REGISTRY}


# Focus-area groupings for the dashboard, mirroring the Google Health app's sections.
GROUP_ORDER = ["Recovery", "Activity", "Workouts", "Heart", "Vitals", "Body", "Sleep", "Other"]
_GROUP_BY_NAME = {
    "readiness": "Recovery",
    "steps": "Activity", "distance": "Activity", "floors": "Activity",
    "active-zone-minutes": "Activity", "total-calories": "Activity",
    "active-energy-burned": "Activity", "active-minutes": "Activity", "altitude": "Activity",
    "sedentary-period": "Activity", "swim-lengths-data": "Activity", "cardio-load": "Activity",
    "heart-rate": "Heart", "daily-resting-heart-rate": "Heart",
    "daily-heart-rate-variability": "Heart", "heart-rate-variability": "Heart",
    "time-in-heart-rate-zone": "Heart", "calories-in-heart-rate-zone": "Heart",
    "daily-oxygen-saturation": "Vitals", "daily-respiratory-rate": "Vitals",
    "oxygen-saturation": "Vitals", "blood-glucose": "Vitals", "core-body-temperature": "Vitals",
    "weight": "Body", "daily-sleep-temperature-derivations": "Body", "daily-vo2-max": "Body",
    "height": "Body", "body-fat": "Body", "run-vo2-max": "Body",
    "sleep-duration": "Sleep", "sleep-rem": "Sleep", "sleep-deep": "Sleep",
    "sleep-light": "Sleep", "sleep-awake": "Sleep", "sleep-efficiency": "Sleep",
    "sleep-score": "Sleep",
    "exercise-minutes": "Workouts", "exercise-count": "Workouts",
    "exercise-distance": "Workouts", "exercise-calories": "Workouts",
}


def group_for(name: str) -> str:
    return _GROUP_BY_NAME.get(name, "Other")


def accumulates_today(dt: DataType) -> bool:
    """Whether the metric's value for *today* is still accumulating (partial until the
    day ends). INTERVAL metrics sum through the day — steps at 9 AM are not the day's
    steps — and cardio-load builds session by session. SAMPLE metrics (RHR, HRV, weight)
    and completed sleep sessions are real values the moment they exist. Analytics that
    compare days must exclude today's row for these, or a half-day reads as a record low."""
    return dt.time_kind is TimeKind.INTERVAL or dt.api_name == "cardio-load"


def scopes_for(data_types: list[DataType]) -> list[str]:
    """The minimal set of OAuth readonly scopes covering the given data types."""
    return sorted({dt.scope.readonly for dt in data_types})


ALL_SCOPES: list[str] = scopes_for(REGISTRY)

# Requested at consent time: everything the registry needs plus scopes we want granted
# on the next (7-day) re-auth anyway — nutrition types get registered once it's granted.
AUTH_SCOPES: list[str] = sorted({*ALL_SCOPES, Scope.NUTRITION.readonly})
