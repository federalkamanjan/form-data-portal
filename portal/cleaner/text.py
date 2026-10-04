"""Plain text clean-up shared by every field."""
from __future__ import annotations

import re
import unicodedata

_ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
_JOINERS = {"\u200c", "\u200d"}  # needed for correct Urdu/Persian spelling


def strip_invisible(text: str) -> str:
    """Unicode-normalise (NFKC) and drop zero-width and control characters.
    Joiners are kept only when the text contains Arabic-script letters, where they change the spelling."""
    text = unicodedata.normalize("NFKC", text)
    keep_joiners = bool(_ARABIC_SCRIPT.search(text))
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch in _JOINERS:
            if keep_joiners:
                out.append(ch)
        elif cat == "Cf" or (cat == "Cc" and ch not in "\t\n\r"):
            continue
        else:
            out.append(ch)
    return "".join(out)


def collapse_spaces(text: str) -> str:
    """Any run of whitespace (including non-breaking spaces, tabs, newlines) becomes one space."""
    return re.sub(r"\s+", " ", text).strip()


def is_placeholder(text: str, placeholders) -> bool:
    """True for 'n/a', 'none', '-' and similar, or anything with no letters or digits at all."""
    t = text.strip().lower()
    if not t:
        return False
    if t in {p.lower() for p in placeholders}:
        return True
    return not any(ch.isalnum() for ch in t)


def _fix_word(word: str) -> str:
    parts = re.split(r"([-'\u2019])", word)
    out = []
    for p in parts:
        if not p or p in ("-", "'", "\u2019"):
            out.append(p)
        elif p.islower() or p.isupper() or p[0].islower():
            out.append(p[0].upper() + p[1:].lower())
        else:
            out.append(p)  # deliberate mixed case such as McDonald is left alone
    return "".join(out)


def title_case(text: str) -> str:
    """Capitalise each word; hyphens and apostrophes start a new capital (O'Neil, Al-Hassan)."""
    return " ".join(_fix_word(w) for w in text.split(" "))


_SYMBOLS_TO_SPACE = re.compile(r"[_/\\|@#&*+=<>~^%$!?,:;()\[\]{}\"\u201c\u201d]")


def strip_digits_symbols(text: str) -> str:
    """For names: remove digits, turn symbols into spaces, keep letters (any script), spaces, hyphens, apostrophes, dots."""
    text = _SYMBOLS_TO_SPACE.sub(" ", text)
    kept = []
    for ch in text:
        if ch.isalpha() or unicodedata.category(ch).startswith("M") or ch in " -'\u2019." or ch in _JOINERS:
            kept.append(ch)
    out = collapse_spaces("".join(kept))
    return out.strip("-'\u2019 ")
