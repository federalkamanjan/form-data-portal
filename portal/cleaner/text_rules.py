"""Small, independent text-cleaning functions (no settings, no state)."""
from __future__ import annotations

import re
import unicodedata

_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
_KEEP_FOR_ARABIC_SCRIPT = {"\u200c", "\u200d"}  # joiners are meaningful in Urdu/Persian
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")


def strip_invisible(value: str) -> str:
    """Turn odd spaces (NBSP, thin space...) into normal spaces and drop non-printing characters."""
    keep_joiners = bool(_ARABIC.search(value))
    out = []
    for ch in value:
        cat = unicodedata.category(ch)
        if cat == "Zs" or ch in "\t\n\r\x0b\x0c":
            out.append(" ")
        elif cat in ("Cc", "Cf", "Cs", "Co", "Cn"):
            if keep_joiners and ch in _KEEP_FOR_ARABIC_SCRIPT:
                out.append(ch)
        elif cat in ("Zl", "Zp"):
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_placeholder(value: str, placeholders: set[str]) -> bool:
    low = value.strip().lower()
    if not low:
        return False
    if low in placeholders:
        return True
    return not any(ch.isalnum() for ch in low)  # "...", "---", "?"


def _fix_word(word: str, *, force_upper: bool = False) -> str:
    """Capitalise one word, handling O'Neil / D'Souza and apostrophes like Ali's."""
    if force_upper:
        return word.upper()
    parts = re.split(r"(['’])", word)
    out = [parts[0].capitalize()]
    for i in range(1, len(parts), 2):
        out.append(parts[i])
        tail = parts[i + 1]
        out.append(tail.capitalize() if len(parts[i - 1]) == 1 else tail.lower())
    return "".join(out)


def _is_plain_camel(word: str) -> bool:
    """McDonald, DeShawn: starts uppercase and has more uppercase later: leave as typed."""
    letters = [c for c in word if c.isalpha()]
    return (len(letters) > 1 and letters[0].isupper() and any(c.isupper() for c in letters[1:])
            and any(c.islower() for c in letters))


def retitle(text: str, *, acronyms: frozenset[str] = frozenset(), joiners: frozenset[str] = frozenset(),
            keep_upper_words_in_mixed: bool = False) -> str:
    """Title-case a value. Words typed in mixed case like McDonald are left alone.

    With keep_upper_words_in_mixed (school names), a fully UPPERCASE word inside an otherwise mixed-case
    string is treated as an acronym and kept. Words in `acronyms` are always uppercased.
    """
    letters = [c for c in text if c.isalpha()]
    uniform = bool(letters) and (all(c.islower() for c in letters) or all(c.isupper() for c in letters))
    state = {"first": True}

    def repl(m: re.Match) -> str:
        word = m.group(0)
        first = state["first"]
        state["first"] = False
        low = word.lower()
        if low.upper() in acronyms:
            return word.upper()
        if not first and low in joiners:
            return low
        if _is_plain_camel(word):
            return word
        if keep_upper_words_in_mixed and not uniform and word.isupper() and len(word) > 1:
            return word
        return _fix_word(word)

    return _WORD.sub(repl, text)


def strip_digits_and_symbols(value: str) -> str:
    """For names: keep letters, spaces, hyphens, apostrophes and full stops."""
    value = re.sub(r"[^\w\s'’\-.]|[\d_]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip("-'’ ")


_KEYBOARD_ROWS = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
_KEYBOARD_SEQS = {row[i:i + 4] for row in _KEYBOARD_ROWS for i in range(len(row) - 3)}
_KEYBOARD_SEQS |= {seq[::-1] for seq in _KEYBOARD_SEQS}
_VOWELS = "aeiouy"


def looks_like_gibberish(text: str) -> bool:
    """Heuristic for keyboard mashing like 'Gsbgdjs' or 'asdfgh'. Used to flag, never to change or reject."""
    for token in re.findall(r"[A-Za-z]{4,}", text):
        t = token.lower()
        if re.search(rf"[^{_VOWELS}]{{5,}}", t) or re.search(r"(.)\1{3,}", t):
            return True
        if any(seq in t for seq in _KEYBOARD_SEQS):
            return True
    return False
