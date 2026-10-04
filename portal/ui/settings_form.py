"""Turn settings into the values of the Settings screen's widgets, and back. No Streamlit code here, so it is testable.

Widget keys all start with "s_". Lists are typed one item per line; alias lists as "short = Full".
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from portal.cleaner.steps import DEFAULT_STEPS_BY_TYPE, FLAG_DESCRIPTIONS, ordered_steps
from portal.config import SettingsError

FIELD_TYPES = ["text", "phone", "email"]
FIELD_TYPE_TEXT = {"text": "Text (name, city, school…)", "phone": "Phone number", "email": "Email address"}
KEEP_CHOICES = {"newest": "Keep the newest submission", "first": "Keep the first submission", "most_complete": "Keep the most complete submission"}
NONE = "__none__"
FLAG_ORDER = list(FLAG_DESCRIPTIONS)


class SettingsInputError(SettingsError):
    """Something typed on the Settings screen could not be understood."""


# ---------------------------------------------------------------- parsing and formatting typed lists

def parse_lines(text: str) -> list[str]:
    """One item per line (commas also separate items); blanks and repeats are dropped."""
    seen, out = set(), []
    for chunk in re.split(r"[\n,]", text or ""):
        item = chunk.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def parse_aliases(text: str, what: str) -> dict[str, str]:
    """Lines like 'khi = Karachi' (or 'khi -> Karachi' / 'khi: Karachi') into a dict."""
    result: dict[str, str] = {}
    for n, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        parts = re.split(r"\s*(?:=>|->|=|:)\s*", line.strip(), maxsplit=1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise SettingsInputError(f"Line {n} of the {what} list needs the form  short form = full form  (you typed: {line.strip()}).")
        result[parts[0].strip()] = parts[1].strip()
    return result


def format_aliases(mapping: Mapping[str, str]) -> str:
    return "\n".join(f"{k} = {v}" for k, v in mapping.items())


def slugify(label: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "field"
    if base[0].isdigit():
        base = "f_" + base
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


# ---------------------------------------------------------------- settings -> widget values

def field_widget_values(field: dict) -> dict[str, Any]:
    k = field["key"]
    return {
        f"s_f_{k}_label": field["label"],
        f"s_f_{k}_type": field["type"],
        f"s_f_{k}_required": bool(field["required"]),
        f"s_f_{k}_keywords": "\n".join(field["keywords"]),
        f"s_f_{k}_steps": list(field["cleaning"]),
    }


def widget_values(settings: dict) -> dict[str, Any]:
    c, d, o, w = settings["cleaning"], settings["dedupe"], settings["output"], settings["workflow"]
    m = settings.get("mapping", {})
    values: dict[str, Any] = {}
    for f in settings["fields"]:
        values.update(field_widget_values(f))
    keys = d.get("key", [])
    values.update({
        "s_placeholders": "\n".join(c.get("placeholders", [])),
        "s_country": c.get("default_country_code", "PK"),
        "s_flag_landlines": bool(c.get("flag_landlines", False)),
        "s_city_aliases": format_aliases(c.get("city_aliases", {})),
        "s_school_abbr": format_aliases(c.get("school_abbreviations", {})),
        "s_email_typos": format_aliases(c.get("email_domain_typos", {})),
        "s_autofix_typos": bool(c.get("autofix_email_domain_typos", False)),
        "s_reject_flags": [code for code in FLAG_ORDER if code in c.get("reject_flags", [])],
        "s_school_similarity": float(c.get("school_similarity_threshold", 0.9)),
        "s_auto_threshold": float(m.get("auto_threshold", 0.85)),
        "s_suggest_threshold": float(m.get("suggest_threshold", 0.6)),
        "s_dedupe_enabled": bool(d.get("enabled", False)),
        "s_dedupe_primary": keys[0] if keys else NONE,
        "s_dedupe_secondary": keys[1] if len(keys) > 1 else NONE,
        "s_dedupe_keep": d.get("keep", "newest"),
        "s_master_name": o.get("master_file_name", "master.csv"),
        "s_include_system": bool(o.get("include_system_columns", True)),
        "s_labels_headers": bool(o.get("use_labels_as_headers", True)),
        "s_backups": int(o.get("backups_to_keep", 5)),
        "s_preview": bool(w.get("preview_enabled", True)),
        "s_require_confirm": bool(w.get("require_confirmation", True)),
    })
    return values


def field_order(settings: dict) -> list[str]:
    return [f["key"] for f in settings["fields"]]


def new_field(label: str, field_type: str, taken: set[str]) -> dict:
    """A blank field with sensible cleaning steps for its type."""
    label = label.strip()
    if not label:
        raise SettingsInputError("Please type a name for the new field.")
    return {"key": slugify(label, taken), "label": label, "type": field_type, "required": False,
            "keywords": [label.lower()], "cleaning": list(DEFAULT_STEPS_BY_TYPE[field_type])}


# ---------------------------------------------------------------- widget values -> settings

def settings_from_widgets(values: Mapping[str, Any], order: list[str], base: dict) -> dict:
    """Rebuild the full settings from the screen's widgets. Anything the screen does not edit is kept from `base`."""
    s = copy.deepcopy(base)
    old_fields = {f["key"]: f for f in base["fields"]}
    fields = []
    for key in order:
        old = old_fields.get(key, {})
        label = str(values.get(f"s_f_{key}_label", old.get("label", key))).strip()
        if not label:
            raise SettingsInputError("Every field needs a name.")
        fields.append({
            "key": key, "label": label,
            "type": values.get(f"s_f_{key}_type", old.get("type", "text")),
            "required": bool(values.get(f"s_f_{key}_required", old.get("required", False))),
            "keywords": parse_lines(values.get(f"s_f_{key}_keywords", "\n".join(old.get("keywords", [])))),
            "cleaning": ordered_steps(values.get(f"s_f_{key}_steps", old.get("cleaning", []))),
        })
    if not fields:
        raise SettingsInputError("There must be at least one field.")
    s["fields"] = fields

    c = s["cleaning"]
    c["placeholders"] = parse_lines(values["s_placeholders"])
    c["default_country_code"] = str(values["s_country"]).strip().upper()
    c["flag_landlines"] = bool(values["s_flag_landlines"])
    c["city_aliases"] = parse_aliases(values["s_city_aliases"], "city names")
    c["school_abbreviations"] = parse_aliases(values["s_school_abbr"], "school abbreviations")
    c["email_domain_typos"] = parse_aliases(values["s_email_typos"], "email typos")
    c["autofix_email_domain_typos"] = bool(values["s_autofix_typos"])
    c["reject_flags"] = [code for code in FLAG_ORDER if code in values["s_reject_flags"]]
    c["school_similarity_threshold"] = round(float(values["s_school_similarity"]), 3)

    s["mapping"] = {"auto_threshold": round(float(values["s_auto_threshold"]), 3),
                    "suggest_threshold": round(float(values["s_suggest_threshold"]), 3)}

    primary, secondary = values["s_dedupe_primary"], values["s_dedupe_secondary"]
    key_list = [k for k in (primary, secondary) if k and k != NONE]
    s["dedupe"] = {"enabled": bool(values["s_dedupe_enabled"]), "key": list(dict.fromkeys(key_list)), "keep": values["s_dedupe_keep"]}

    o = s["output"]
    o["master_file_name"] = str(values["s_master_name"]).strip()
    o["include_system_columns"] = bool(values["s_include_system"])
    o["use_labels_as_headers"] = bool(values["s_labels_headers"])
    o["backups_to_keep"] = int(values["s_backups"])

    s["workflow"]["preview_enabled"] = bool(values["s_preview"])
    s["workflow"]["require_confirmation"] = bool(values["s_require_confirm"])
    return s
