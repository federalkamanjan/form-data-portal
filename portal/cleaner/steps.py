"""The individual cleaning steps. A field's `cleaning` list in the settings names which ones run, in order.

A step takes the current value and a context, and returns the new value. It can also record:
  * an error  -> the row is Rejected (the value is unusable)
  * a flag    -> the row is kept but marked for a human look
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from . import text as T
from .email import domain_typo, email_problem, tidy_email_text
from .phone import normalise_phone
from .schools import expand_abbreviations, normalise_school


@dataclass
class CellContext:
    field: dict
    settings: dict
    errors: list[tuple[str, str]] = field(default_factory=list)
    flags: list[tuple[str, str]] = field(default_factory=list)

    @property
    def cfg(self) -> dict:
        return self.settings["cleaning"]

    @property
    def label(self) -> str:
        return self.field["label"]

    def error(self, code: str, message: str) -> None:
        self.errors.append((code, f"{self.label}: {message}"))

    def flag(self, code: str, message: str) -> None:
        self.flags.append((code, f"{self.label}: {message}"))


Step = Callable[[str, CellContext], str]


def _trim(v, ctx): return v.strip()
def _collapse(v, ctx): return T.collapse_spaces(v)
def _invisible(v, ctx): return T.strip_invisible(v)
def _placeholders(v, ctx): return "" if T.is_placeholder(v, ctx.cfg.get("placeholders", [])) else v
def _strip_digits_symbols(v, ctx): return T.strip_digits_symbols(v)
def _title_case(v, ctx): return T.title_case(v)
def _lowercase(v, ctx): return v.lower()


def _flag_short_name(v, ctx):
    if v and sum(c.isalpha() for c in v) <= 2:
        ctx.flag("name_short", "very short, please check")
    return v


_VOWELS = set("aeiou")


def looks_random(name: str) -> bool:
    """Plain-Latin names with almost no vowels, long consonant runs or repeated letters (e.g. 'Gsbgdjs')."""
    letters = [c for c in name.lower() if c.isalpha()]
    if len(letters) < 5 or any(not ("a" <= c <= "z") for c in letters):
        return False
    word = "".join(letters)
    vowel_ratio = sum(c in _VOWELS for c in letters) / len(letters)
    longest_run = max((len(m.group()) for m in re.finditer(r"[^aeiou]+", word)), default=0)
    return vowel_ratio <= 0.2 or longest_run >= 5 or bool(re.search(r"(.)\1{3,}", word))


def _flag_random_name(v, ctx):
    if v and looks_random(v):
        ctx.flag("name_random", "looks like random letters, please check")
    return v


def _phone(v, ctx):
    if not v:
        return v
    res = normalise_phone(v, ctx.cfg.get("default_country_code", "PK"), ctx.cfg.get("flag_landlines", False))
    for code, msg in res.flags:
        ctx.flag(code, msg)
    if res.e164 is None:
        ctx.error("phone_invalid", f"is not a valid phone number ({res.error})")
        return v
    return res.e164


def _city_aliases(v, ctx):
    aliases = {k.lower(): val for k, val in ctx.cfg.get("city_aliases", {}).items()}
    return aliases.get(v.lower(), v)


def _school(v, ctx): return normalise_school(v)
def _school_abbrev(v, ctx): return expand_abbreviations(v, ctx.cfg.get("school_abbreviations", {}))
def _tidy_email(v, ctx): return tidy_email_text(v)


def _validate_email(v, ctx):
    if v:
        problem = email_problem(v)
        if problem:
            ctx.error("email_invalid", problem)
    return v


def _flag_domain_typos(v, ctx):
    if not v or email_problem(v):
        return v
    hit = domain_typo(v, ctx.cfg.get("email_domain_typos", {}))
    if not hit:
        return v
    typed, intended = hit
    if ctx.cfg.get("autofix_email_domain_typos", False):
        return v.rsplit("@", 1)[0] + "@" + intended
    ctx.flag("email_domain_typo", f"domain '{typed}' may be a typo for '{intended}'")
    return v


STEPS: dict[str, Step] = {
    "trim": _trim,
    "collapse_spaces": _collapse,
    "strip_invisible": _invisible,
    "placeholders_to_empty": _placeholders,
    "strip_digits_symbols": _strip_digits_symbols,
    "title_case": _title_case,
    "tidy_email": _tidy_email,
    "lowercase": _lowercase,
    "flag_short_name": _flag_short_name,
    "flag_random_name": _flag_random_name,
    "normalise_phone_e164": _phone,
    "city_aliases": _city_aliases,
    "normalise_school": _school,
    "expand_school_abbreviations": _school_abbrev,
    "validate_email": _validate_email,
    "flag_domain_typos": _flag_domain_typos,
}
KNOWN_STEPS = frozenset(STEPS)

# Wording for the run summary ("<field>: <n> <label>")
STEP_LABELS = {
    "trim": "had leading or trailing spaces removed",
    "collapse_spaces": "had repeated spaces collapsed",
    "strip_invisible": "had hidden characters removed",
    "placeholders_to_empty": "were placeholders (n/a, none, -) treated as empty",
    "strip_digits_symbols": "had digits or symbols removed",
    "title_case": "had capitalisation fixed",
    "lowercase": "were lower-cased",
    "normalise_phone_e164": "were standardised to international format",
    "city_aliases": "had short names expanded",
    "normalise_school": "had capitalisation fixed",
    "expand_school_abbreviations": "had abbreviations expanded",
    "tidy_email": "had stray spaces or wrappers removed",
    "flag_domain_typos": "had a likely domain typo corrected",
}


# The order steps run in. Settings screens let people tick steps on and off; the order is then always this one,
# so a step can never run before the step it depends on.
STEP_ORDER = list(STEPS)

STEP_DESCRIPTIONS = {
    "trim": "Trim spaces at the start and end",
    "collapse_spaces": "Collapse repeated spaces",
    "strip_invisible": "Remove hidden characters",
    "placeholders_to_empty": "Treat n/a, none and - as empty",
    "strip_digits_symbols": "Remove digits and symbols (for names)",
    "title_case": "Fix capitalisation",
    "tidy_email": "Tidy stray spaces and brackets in emails",
    "lowercase": "Lower-case everything",
    "flag_short_name": "Flag very short names",
    "flag_random_name": "Flag random-looking names",
    "normalise_phone_e164": "Standardise phone numbers (+92…)",
    "city_aliases": "Expand short city names (Khi to Karachi)",
    "normalise_school": "Tidy school name capitalisation",
    "expand_school_abbreviations": "Expand school abbreviations (Govt to Government)",
    "validate_email": "Reject invalid emails",
    "flag_domain_typos": "Flag likely email typos (gmial.com)",
}

# Codes of the "keep but flag" warnings, with plain wording. Any of these can be switched to "reject" in the settings.
FLAG_DESCRIPTIONS = {
    "name_short": "Very short name",
    "name_random": "Random-looking name",
    "phone_unrecognised": "Phone number with an unknown pattern",
    "phone_multiple": "Several phone numbers typed in one cell",
    "phone_landline": "Landline number",
    "email_domain_typo": "Likely email typo",
    "school_near_duplicate": "School name similar to a more common one",
}

DEFAULT_STEPS_BY_TYPE = {
    "text": ["trim", "collapse_spaces", "strip_invisible", "placeholders_to_empty", "title_case"],
    "phone": ["trim", "strip_invisible", "placeholders_to_empty", "normalise_phone_e164"],
    "email": ["trim", "strip_invisible", "placeholders_to_empty", "tidy_email", "lowercase", "validate_email", "flag_domain_typos"],
}


def ordered_steps(chosen) -> list[str]:
    """The chosen steps, in the order they must run."""
    wanted = set(chosen)
    return [name for name in STEP_ORDER if name in wanted]
