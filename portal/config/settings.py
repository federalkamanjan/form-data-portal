"""Load and validate the portal settings.

Settings live in a human-readable JSON file. The defaults ship with the app;
a user-edited copy (written later by the Settings screen) takes priority.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CONFIG_DIR = Path(__file__).parent
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "default_settings.json"

FIELD_TYPES = {"text", "phone", "email"}
REQUIRED_TOP_LEVEL = ("fields", "system_columns", "cleaning", "dedupe", "output", "workflow")
DEDUPE_KEEP_OPTIONS = {"newest", "first", "most_complete"}


class SettingsError(ValueError):
    """Raised when a settings file is missing something or has a bad value."""


def validate_settings(settings: dict) -> dict:
    """Check the settings structure and return it unchanged if it is valid."""
    for key in REQUIRED_TOP_LEVEL:
        if key not in settings:
            raise SettingsError(f"Settings are missing the '{key}' section.")

    fields = settings["fields"]
    if not fields:
        raise SettingsError("Settings must define at least one field.")

    seen = set()
    for field in fields:
        for attr in ("key", "label", "type", "required", "keywords", "cleaning"):
            if attr not in field:
                raise SettingsError(f"A field is missing '{attr}': {field.get('key', field)}")
        if field["type"] not in FIELD_TYPES:
            raise SettingsError(f"Field '{field['key']}' has unknown type '{field['type']}'.")
        if field["key"] in seen:
            raise SettingsError(f"Field key '{field['key']}' is used twice.")
        seen.add(field["key"])
        from portal.cleaner.steps import KNOWN_STEPS  # imported here to avoid a circular import
        for step in field["cleaning"]:
            if step not in KNOWN_STEPS:
                raise SettingsError(f"Field '{field['key']}' lists an unknown cleaning step '{step}'.")

    keep = settings["dedupe"].get("keep")
    if keep not in DEDUPE_KEEP_OPTIONS:
        raise SettingsError(f"dedupe.keep must be one of {sorted(DEDUPE_KEEP_OPTIONS)}, got '{keep}'.")

    for key in settings["dedupe"].get("key", []):
        if key not in seen:
            raise SettingsError(f"Dedupe key '{key}' is not a defined field.")

    _check_values(settings)
    return settings


def _check_values(settings: dict) -> None:
    """Plain-language checks on the values people can edit on the Settings screen."""
    cleaning = settings["cleaning"]
    region = cleaning.get("default_country_code", "PK")
    if not re.fullmatch(r"[A-Z]{2}", str(region)):
        raise SettingsError("The default country must be a two-letter code such as PK, IN, AE or GB.")
    mapping = settings.get("mapping", {})
    auto, suggest = mapping.get("auto_threshold", 0.85), mapping.get("suggest_threshold", 0.6)
    if not (0 <= suggest <= auto <= 1):
        raise SettingsError("For matching questions, the 'suggest' level must be between 0 and the 'auto' level, and both between 0 and 1.")
    similarity = cleaning.get("school_similarity_threshold", 0.9)
    if not 0.5 <= similarity <= 1:
        raise SettingsError("The school-name similarity level must be between 0.5 and 1.")
    name = settings["output"].get("master_file_name", "master.csv")
    if not re.fullmatch(r"[\w\- .]+\.csv", str(name)):
        raise SettingsError("The master file name must end in .csv and contain only letters, numbers, spaces, dashes or underscores.")
    if settings["output"].get("backups_to_keep", 5) < 0:
        raise SettingsError("The number of backups to keep cannot be negative.")


def load_settings(path: str | Path | None = None) -> dict:
    """Read a settings file (default: the bundled defaults) and validate it."""
    path = Path(path) if path else DEFAULT_SETTINGS_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            settings = json.load(fh)
    except FileNotFoundError as exc:
        raise SettingsError(f"Settings file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SettingsError(f"Settings file is not valid JSON: {exc}") from exc
    return validate_settings(settings)


def field_keys(settings: dict) -> list[str]:
    """Ordered list of the business field keys."""
    return [f["key"] for f in settings["fields"]]


def master_columns(settings: dict) -> list[str]:
    """Column order of the master CSV, honouring the system-columns switch."""
    cols = field_keys(settings)
    if settings["output"].get("include_system_columns", True):
        cols += settings["system_columns"]
    return cols
