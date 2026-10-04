"""Scoring: how well does a column look like a given field?

Two signals are combined:
  * the question title against the field's label and keywords (word match, then typo-tolerant match)
  * the values themselves, for fields with a recognisable shape (email, phone)
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_CHARS = re.compile(r"^[+\d\s\-()./]+$")
SNIFF_SAMPLE = 200


def normalize_title(title: str) -> str:
    """Lowercase, drop a leading question number like '3.' or '(2)', and keep only letters and digits."""
    t = (title or "").lower()
    t = re.sub(r"^\s*\(?\d+[\).:\-]\s*", "", t)
    return re.sub(r"[\W_]+", " ", t).strip()


def title_score(title: str, keywords: Iterable[str]) -> float:
    """0 to 1. Exact match 1.0; keyword words inside the title scale with how much of the title they cover;
    near-misses (typos) score a little under their similarity."""
    t = normalize_title(title)
    if not t:
        return 0.0
    t_tokens = t.split()
    best = 0.0
    for keyword in keywords:
        k = normalize_title(keyword)
        if not k:
            continue
        if t == k:
            return 1.0
        k_tokens = k.split()
        if set(k_tokens) <= set(t_tokens):
            best = max(best, 0.5 + 0.7 * len(k_tokens) / len(t_tokens))
        else:
            ratio = SequenceMatcher(None, t, k).ratio()
            if ratio >= 0.8:
                best = max(best, 0.97 * ratio)
    return round(min(best, 0.99), 3)


def _sample(values) -> list[str]:
    cleaned = [str(v).strip() for v in values if v is not None and str(v).strip()]
    return cleaned[:SNIFF_SAMPLE]


def content_score(values, field_type: str) -> float:
    """0 to 0.84. Only email and phone fields have a recognisable shape. Capped under the auto threshold
    so a column is never auto-mapped on its values alone."""
    sample = _sample(values)
    if not sample or field_type not in ("email", "phone"):
        return 0.0
    if field_type == "email":
        hits = sum(bool(_EMAIL_RE.match(v)) for v in sample)
    else:
        def looks_like_phone(v: str) -> bool:
            digits = re.sub(r"\D", "", v)
            return bool(_PHONE_CHARS.match(v)) and 9 <= len(digits) <= 14
        hits = sum(looks_like_phone(v) for v in sample)
    fraction = hits / len(sample)
    return round(0.6 + 0.24 * fraction, 3) if fraction >= 0.8 else 0.0


def combined_score(title_s: float, content_s: float) -> float:
    """Take the stronger signal; if the title also points the same way, add a small agreement bonus."""
    if title_s >= 0.6 and content_s > 0:
        return round(min(1.0, title_s + 0.1), 3)
    return round(max(title_s, content_s), 3)
