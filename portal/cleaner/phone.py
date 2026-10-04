"""Phone numbers to international (E.164) format, e.g. 0314 1837972 -> +923141837972."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

try:  # the real library is used whenever it is installed
    import phonenumbers
    PHONENUMBERS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only where the library is missing
    phonenumbers = None
    PHONENUMBERS_AVAILABLE = False

_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_SPLIT = re.compile(r"\s*(?:[/,;|\n]|\bor\b|\band\b)\s*", re.IGNORECASE)


@dataclass
class PhoneResult:
    e164: str | None = None
    valid: bool = False                 # matches a known number pattern (not just the right length)
    error: str = ""                     # set when no usable number could be produced
    flags: list[tuple[str, str]] = field(default_factory=list)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _pick_segment(text: str) -> tuple[str, bool]:
    """If several numbers were typed in one cell, use the first. Returns (segment, had_multiple)."""
    parts = [p for p in _SPLIT.split(text) if p]
    good = [p for p in parts if len(_digits(p)) >= 9]
    if len(good) >= 2:
        return good[0], True
    return text, False


def _parse_fallback(seg: str, region: str) -> PhoneResult:
    """Limited built-in handling for Pakistan and '+' numbers, used only if `phonenumbers` is not installed."""
    digits = _digits(seg)
    if seg.startswith("+"):
        if 8 <= len(digits) <= 15:
            valid = bool(re.fullmatch(r"923\d{9}", digits)) if digits.startswith("92") else False
            return PhoneResult(e164="+" + digits, valid=valid)
        return PhoneResult(error="wrong length")
    if region != "PK":
        return PhoneResult(error="install the 'phonenumbers' package to handle numbers for this country")
    if len(digits) == 12 and digits.startswith("92"):
        national = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        national = digits[1:]
    elif len(digits) == 10 and digits.startswith("3"):
        national = digits
    else:
        return PhoneResult(error="wrong length for a Pakistani number")
    return PhoneResult(e164="+92" + national, valid=bool(re.fullmatch(r"3\d{9}", national)))


def _parse_library(seg: str, region: str, flag_landlines: bool) -> PhoneResult:
    try:
        num = phonenumbers.parse(seg, region)
    except phonenumbers.NumberParseException:
        return PhoneResult(error="not a phone number")
    if not phonenumbers.is_possible_number(num):
        return PhoneResult(error="wrong length")
    result = PhoneResult(e164=phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164),
                         valid=phonenumbers.is_valid_number(num))
    if flag_landlines and phonenumbers.number_type(num) == phonenumbers.PhoneNumberType.FIXED_LINE:
        result.flags.append(("phone_landline", "looks like a landline, which may not have WhatsApp"))
    return result


def normalise_phone(raw: str, default_region: str = "PK", flag_landlines: bool = False) -> PhoneResult:
    text = unicodedata.normalize("NFKC", raw).translate(_DIGIT_MAP)
    seg, multiple = _pick_segment(text)
    seg = re.sub(r"[A-Za-z]+", " ", seg).strip()          # drop words such as '(whatsapp)' or 'ext'
    if len(_digits(seg)) < 7:
        return PhoneResult(error="too few digits to be a phone number")
    if seg.startswith("00"):
        seg = "+" + seg[2:]
    result = _parse_library(seg, default_region, flag_landlines) if PHONENUMBERS_AVAILABLE else _parse_fallback(seg, default_region)
    if result.e164 and not result.valid:
        result.flags.append(("phone_unrecognised", "the number has a plausible length but does not match a known number pattern"))
    if result.e164 and multiple:
        result.flags.append(("phone_multiple", "several numbers were entered; the first one was used"))
    return result
