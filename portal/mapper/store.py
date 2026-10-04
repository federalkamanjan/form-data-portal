"""Remember each form's confirmed mapping so the operator only confirms once."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .models import MappingResult


class MappingStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except ValueError:
            self.path.replace(self.path.with_suffix(".json.bad"))  # keep the unreadable file, start fresh
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, form_id: str) -> dict | None:
        return self._load().get(form_id)

    def save(self, form_id: str, result: MappingResult) -> None:
        data = self._load()
        entry = result.to_saved()
        entry["saved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data[form_id] = entry
        self._write(data)

    def forget(self, form_id: str) -> None:
        data = self._load()
        if data.pop(form_id, None) is not None:
            self._write(data)
