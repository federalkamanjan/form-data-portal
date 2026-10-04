"""Email checks that need no internet: shape rules only, no delivery test."""
from __future__ import annotations

import re

_LOCAL_OK = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_LABEL_OK = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")


def tidy_email_text(text: str) -> str:
    """Remove spaces inside the address, a 'mailto:' prefix and surrounding <angle brackets> or trailing dots/commas."""
    t = re.sub(r"\s+", "", text)
    t = re.sub(r"^mailto:", "", t, flags=re.IGNORECASE)
    t = t.rstrip(".,;").strip("<>").rstrip(".,;")
    return t


def email_problem(address: str) -> str | None:
    """None when the address looks valid, otherwise a short plain-language reason."""
    if not address:
        return "is empty"
    if len(address) > 254 or address.count("@") != 1:
        return "does not look like an email address"
    local, domain = address.split("@")
    if not local or len(local) > 64 or not _LOCAL_OK.match(local) or local.startswith(".") or local.endswith(".") or ".." in local:
        return "has a problem before the @ sign"
    labels = domain.split(".")
    if len(labels) < 2 or any(not _LABEL_OK.match(lab) for lab in labels):
        return "has a problem after the @ sign"
    if not re.fullmatch(r"[A-Za-z]{2,}", labels[-1]):
        return "has an invalid ending (such as .com)"
    return None


def domain_typo(address: str, typos: dict[str, str]) -> tuple[str, str] | None:
    """(typed domain, likely intended domain) when the domain is a known misspelling."""
    domain = address.rsplit("@", 1)[-1].lower()
    if domain in typos:
        return domain, typos[domain]
    return None
