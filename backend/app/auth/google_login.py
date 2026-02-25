from __future__ import annotations

import os

from google_auth_oauthlib.flow import Flow

_LOGIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def _login_client_id() -> str:
    return os.getenv("GOOGLE_LOGIN_CLIENT_ID") or os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")


def _login_client_secret() -> str:
    return os.getenv("GOOGLE_LOGIN_CLIENT_SECRET") or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")


def _login_redirect_uri() -> str:
    return os.getenv("GOOGLE_LOGIN_REDIRECT_URI", "http://localhost:8000/api/auth/callback")


def is_login_configured() -> bool:
    """Return True if the OAuth credentials needed for login are available."""
    return bool(_login_client_id() and _login_client_secret())


def build_login_flow() -> Flow:
    # Login OAuth uses openid + profile + email to identify the user.
    # Falls back to the Tasks OAuth client if no separate login client is configured —
    # just register both redirect URIs on the same Google Cloud Console client.
    return Flow.from_client_config(
        {
            "web": {
                "client_id": _login_client_id(),
                "client_secret": _login_client_secret(),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=_LOGIN_SCOPES,
        redirect_uri=_login_redirect_uri(),
    )
