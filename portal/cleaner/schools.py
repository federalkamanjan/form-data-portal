"""School names: acronym-aware capitalisation, abbreviation expansion, near-duplicate detection."""
from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

from .text import collapse_spaces, title_case


def normalise_school(text: str) -> str:
    """Capitalise words. In mixed-case input, short ALL-CAPS words are treated as acronyms and kept (APS Garrison)."""
    text = collapse_spaces(text)
    has_lower = any(c.islower() for c in text)
    words = []
    for w in text.split(" "):
        if has_lower and w.isalpha() and w.isupper() and len(w) <= 5:
            words.append(w)
        else:
            words.append(title_case(w))
    return " ".join(words)


def expand_abbreviations(text: str, abbreviations: dict[str, str]) -> str:
    """Replace whole-word abbreviations (Govt, Sch, Pvt...) using the configured list."""
    lookup = {k.lower().rstrip("."): v for k, v in abbreviations.items()}
    out = []
    for w in text.split(" "):
        key = w.lower().rstrip(".,")
        out.append(lookup.get(key, w))
    return " ".join(out)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def find_near_duplicates(names: list[str], threshold: float = 0.9) -> dict[str, str]:
    """Map each rarer spelling to the most common similar one. Names are only flagged, never merged."""
    counts = Counter(n for n in names if n)
    ordered = [n for n, _ in counts.most_common()]
    keys = {n: _key(n) for n in ordered}
    result: dict[str, str] = {}
    for i, name in enumerate(ordered):
        for better in ordered[:i]:
            if better in result:  # only compare against spellings that are themselves "main" spellings
                continue
            ka, kb = keys[name], keys[better]
            if ka == kb or abs(len(ka) - len(kb)) > 4 or ka[:1] != kb[:1]:
                continue
            if SequenceMatcher(None, ka, kb).ratio() >= threshold:
                result[name] = better
                break
    return result
