"""Edited settings: saved to a file, with every earlier version kept so any of them can be restored.

  data/settings.json                       the settings in use (falls back to the shipped defaults if absent)
  data/settings_history/settings_<time>_v<n>.json   what the settings were BEFORE each change
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import DEFAULT_SETTINGS_PATH, SettingsError, load_settings, validate_settings


@dataclass
class HistoryEntry:
    path: Path
    saved_at: datetime      # when this version was replaced
    version: int
    settings: dict

    @property
    def label(self) -> str:
        return f"Version {self.version}, replaced {self.saved_at.strftime('%d %b %Y %H:%M')} UTC"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(value, list) and value and all(isinstance(i, dict) and "key" in i for i in value):
        out = {}
        for item in value:                                  # fields are compared by their key, not position
            out.update(_flatten({k: v for k, v in item.items() if k != "key"}, f"{prefix}[{item['key']}]"))
        return out
    return {prefix: value}


def describe_changes(old: dict, new: dict, limit: int = 8) -> list[str]:
    """Short human-readable lines about what differs between two settings dicts."""
    a, b = _flatten({k: v for k, v in old.items() if k != "settings_version"}), _flatten({k: v for k, v in new.items() if k != "settings_version"})
    lines = []
    for path in sorted(set(a) | set(b)):
        if a.get(path, "∅") == b.get(path, "∅"):
            continue
        if path not in a:
            lines.append(f"{path}: added")
        elif path not in b:
            lines.append(f"{path}: removed")
        else:
            lines.append(f"{path}: {_short(a[path])} → {_short(b[path])}")
    if len(lines) > limit:
        lines = lines[:limit] + [f"…and {len(lines) - limit} more changes"]
    return lines


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= 40 else text[:37] + "…"


class SettingsStore:
    def __init__(self, directory: str | Path, defaults_path: str | Path = DEFAULT_SETTINGS_PATH, keep_history: int = 30):
        self.directory = Path(directory)
        self.path = self.directory / "settings.json"
        self.history_dir = self.directory / "settings_history"
        self.defaults_path = Path(defaults_path)
        self.keep_history = keep_history
        self.last_error: str | None = None

    # ---- reading
    def defaults(self) -> dict:
        return load_settings(self.defaults_path)

    def load(self) -> dict:
        """The settings in use. If the saved file is unreadable, the defaults are used and `last_error` says why."""
        self.last_error = None
        if not self.path.exists():
            return self.defaults()
        try:
            return load_settings(self.path)
        except SettingsError as exc:
            self.last_error = f"The saved settings could not be used, so the built-in defaults are in use. ({exc})"
            return self.defaults()

    # ---- writing
    def save(self, new_settings: dict) -> dict:
        """Validate, keep the current version in the history, then make `new_settings` the current ones."""
        candidate = copy.deepcopy(new_settings)
        validate_settings(candidate)
        previous = self.load()
        candidate["settings_version"] = int(previous.get("settings_version", 1)) + 1
        self._snapshot(previous)
        _write_json(self.path, candidate)
        return candidate

    def _snapshot(self, settings: dict) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        _write_json(self.history_dir / f"settings_{stamp}_v{settings.get('settings_version', 1)}.json", settings)
        files = sorted(self.history_dir.glob("settings_*.json"))
        for stale in files[: max(0, len(files) - self.keep_history)]:
            stale.unlink(missing_ok=True)

    def history(self) -> list[HistoryEntry]:
        """Earlier versions, newest first."""
        entries = []
        for path in sorted(self.history_dir.glob("settings_*.json"), reverse=True) if self.history_dir.exists() else []:
            try:
                stamp, version = path.stem.split("_")[1:3], path.stem.rsplit("_v", 1)[1]
                saved = datetime.strptime("_".join(stamp), "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
                entries.append(HistoryEntry(path=path, saved_at=saved, version=int(version), settings=load_settings(path)))
            except (ValueError, IndexError, SettingsError):
                continue            # skip a damaged history file rather than break the screen
        return entries

    def restore(self, entry: HistoryEntry) -> dict:
        """Make an earlier version current again. The version being replaced goes into the history, so this can be undone."""
        return self.save(entry.settings)

    def reset_to_defaults(self) -> dict:
        return self.save(self.defaults())

    def export_json(self, settings: dict | None = None) -> bytes:
        return (json.dumps(settings or self.load(), indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    def import_json(self, raw: bytes | str) -> dict:
        """Save an uploaded settings file as a new version (after validating it)."""
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise SettingsError(f"That file is not valid settings JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SettingsError("That file does not look like a settings file.")
        return self.save(data)
