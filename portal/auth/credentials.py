"""Read the OAuth secrets and turn the stored refresh token into Forms API access.

Operators never see any of this. Secrets come from Streamlit's secrets manager
(or `.streamlit/secrets.toml` when running scripts locally).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import (
    SETUP_HINT,
    TOKEN_EXPIRED_MESSAGE,
    AuthSetupError,
    AuthTokenError,
)

# Read-only scopes only: the app can never edit forms or delete responses.
SCOPES = [
    "https://www.googleapis.com/auth/forms.body.readonly",
    "https://www.googleapis.com/auth/forms.responses.readonly",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"
SECRETS_SECTION = "google_oauth"
LOCAL_SECRETS_PATH = Path(__file__).resolve().parents[2] / ".streamlit" / "secrets.toml"

_PLACEHOLDER_MARKERS = ("your-client", "filled in by", "paste", "changeme")


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    refresh_token: str

    def __repr__(self) -> str:  # never leak secrets into logs or tracebacks
        return f"OAuthConfig(client_id={self.client_id!r}, client_secret='***', refresh_token='***')"


def _looks_like_placeholder(value: str) -> bool:
    low = value.strip().lower()
    return not low or any(marker in low for marker in _PLACEHOLDER_MARKERS)


def parse_oauth_config(secrets: Mapping[str, Any]) -> OAuthConfig:
    """Build an OAuthConfig from a secrets mapping, with plain-language errors."""
    section = secrets.get(SECRETS_SECTION) if hasattr(secrets, "get") else None
    if not section:
        raise AuthSetupError(
            f"The Google sign-in settings (the [{SECRETS_SECTION}] section) are missing from the app's secrets. "
            + SETUP_HINT
        )
    labels = {
        "client_id": "client ID",
        "client_secret": "client secret",
        "refresh_token": "refresh token",
    }
    values = {}
    for key, label in labels.items():
        raw = section.get(key, "")
        value = str(raw).strip() if raw is not None else ""
        if _looks_like_placeholder(value):
            raise AuthSetupError(f"The {label} is missing or still a placeholder in the app's secrets. " + SETUP_HINT)
        values[key] = value
    return OAuthConfig(**values)


def load_secrets(path: str | Path | None = None) -> Mapping[str, Any]:
    """Return the secrets mapping: Streamlit's when running in the app, else the local TOML file."""
    if path is None:
        try:
            import streamlit as st  # imported lazily so scripts and tests work without it

            if len(st.secrets) > 0:
                return st.secrets
        except Exception:
            pass
        path = LOCAL_SECRETS_PATH
    path = Path(path)
    if not path.exists():
        raise AuthSetupError(f"No secrets were found (looked for {path}). " + SETUP_HINT)
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def translate_refresh_error(exc: Exception) -> Exception:
    """Map a Google refresh failure to a clear admin-facing error."""
    text = str(exc).lower()
    if "invalid_grant" in text or "expired" in text or "revoked" in text:
        return AuthTokenError(TOKEN_EXPIRED_MESSAGE)
    if "invalid_client" in text or "unauthorized_client" in text:
        return AuthTokenError(
            "Google rejected the client ID or client secret saved in the app's secrets. "
            "Check that they match the OAuth client in your Google Cloud project. " + SETUP_HINT
        )
    return AuthTokenError(f"Could not get access from Google ({exc}). " + SETUP_HINT)


def build_credentials(config: OAuthConfig):
    """Exchange the refresh token for a fresh short-lived access token."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token=None,
        refresh_token=config.refresh_token,
        client_id=config.client_id,
        client_secret=config.client_secret,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        raise translate_refresh_error(exc) from exc
    return creds


def get_forms_service(secrets: Mapping[str, Any] | None = None):
    """Return an authorised Google Forms API client (refreshes the token every call)."""
    from googleapiclient.discovery import build

    config = parse_oauth_config(secrets if secrets is not None else load_secrets())
    creds = build_credentials(config)
    return build("forms", "v1", credentials=creds, cache_discovery=False)
