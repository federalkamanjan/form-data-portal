"""Save each form's raw table untouched, one CSV per form."""
from __future__ import annotations

import os
from pathlib import Path

from .models import LinkResult


def save_raw_table(result: LinkResult, directory: str | Path) -> Path:
    """Write data/raw/<form_id>.csv atomically (UTF-8 with BOM so Excel shows Urdu and accents correctly)."""
    if not result.has_table or not result.form_id:
        raise ValueError("There is no table to save for this link.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{result.form_id}.csv"
    tmp = target.with_suffix(".csv.tmp")
    result.table.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, target)
    return target
