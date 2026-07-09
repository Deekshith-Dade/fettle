"""Google OAuth 2.0 for the Health API.

Personal-use flow: the OAuth consent screen stays in *Testing* mode with the developer
as the only test user, so no security review is needed. The price is that refresh
tokens expire after ~7 days — `TokenExpiredError` signals that a fresh `auth` run is
needed.

Docs: https://developers.google.com/health/setup
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

# The OAuth callback is served over http://localhost, but oauthlib refuses to complete
# a token exchange over plain HTTP unless this is set (it raises InsecureTransportError,
# which surfaces as a 500). Safe for local, single-user use; do NOT set in production.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
# Google may grant a superset of the requested scopes (e.g. it folds in granted scopes),
# which otherwise trips a "scope changed" warning-to-error in oauthlib.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from .config import AUTH_SCOPES, settings

# Testing-mode consent screens hard-expire refresh tokens after 7 days.
TOKEN_LIFETIME_DAYS = 7


class AuthError(RuntimeError):
    """Generic auth/setup problem (e.g. missing credentials.json)."""


class TokenExpiredError(AuthError):
    """Stored token is gone/expired and cannot be refreshed — re-run `auth`."""


def _flow() -> Flow:
    if not settings.credentials_file.exists():
        raise AuthError(
            f"Missing {settings.credentials_file}. Download your OAuth client JSON "
            "from Google Cloud Console and save it there (see README)."
        )
    return Flow.from_client_secrets_file(
        str(settings.credentials_file),
        scopes=AUTH_SCOPES,
        redirect_uri=settings.oauth_redirect_uri,
    )


def build_authorization_url() -> tuple[str, str]:
    """Return (auth_url, state) to send the user to Google's consent screen."""
    flow = _flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",      # request a refresh token
        include_granted_scopes="true",
        prompt="consent",           # force a refresh token even on re-auth
    )
    return auth_url, state


def _meta_file():
    return settings.token_file.with_name("token_meta.json")


def exchange_code(authorization_response_url: str) -> Credentials:
    """Exchange the redirect callback URL for credentials, then persist them."""
    flow = _flow()
    flow.fetch_token(authorization_response=authorization_response_url)
    creds = flow.credentials
    save_credentials(creds)
    # Refreshes rewrite token.json, so the consent moment (which starts the 7-day
    # Testing-mode clock) has to be recorded separately, here at exchange time.
    _meta_file().write_text(
        json.dumps({"consented_at": datetime.now(timezone.utc).isoformat()})
    )
    return creds


def token_days_left() -> float | None:
    """Days until the Testing-mode refresh token dies, or None if unknown
    (no consent recorded yet — populated by the next auth run)."""
    try:
        meta = json.loads(_meta_file().read_text())
        consented = datetime.fromisoformat(meta["consented_at"])
    except (FileNotFoundError, ValueError, KeyError):
        return None
    age_days = (datetime.now(timezone.utc) - consented).total_seconds() / 86400
    return round(TOKEN_LIFETIME_DAYS - age_days, 1)


def save_credentials(creds: Credentials) -> None:
    settings.token_file.write_text(creds.to_json())


def load_credentials() -> Credentials:
    """Load stored credentials, refreshing the access token if needed.

    Raises TokenExpiredError when there is nothing usable left (the 7-day case).
    """
    if not settings.token_file.exists():
        raise TokenExpiredError("No token stored yet — run `python cli.py auth`.")

    # No scopes arg: the token's own granted scopes apply, so requesting new scopes at
    # the next consent (AUTH_SCOPES) can't invalidate the currently stored token.
    creds = Credentials.from_authorized_user_file(str(settings.token_file))

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # refresh token itself expired/revoked
            raise TokenExpiredError(
                "Refresh failed (Testing-mode tokens expire after 7 days). "
                "Re-run `python cli.py auth`."
            ) from exc
        save_credentials(creds)
        return creds

    raise TokenExpiredError("Stored token is unusable — run `python cli.py auth`.")


def has_valid_token() -> bool:
    try:
        load_credentials()
        return True
    except AuthError:
        return False
