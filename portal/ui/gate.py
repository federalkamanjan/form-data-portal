"""Optional password gate. The app is only locked when a password is set in the secrets:

    [app]
    password = "choose-something-long"
"""
from __future__ import annotations

import hmac
from typing import Any


def configured_password(secrets: Any) -> str | None:
    try:
        section = secrets.get("app") if hasattr(secrets, "get") else None
        pw = section.get("password") if section else None
    except Exception:  # no secrets file at all
        return None
    return str(pw) if pw else None


def password_ok(entered: str, expected: str) -> bool:
    return hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8"))
