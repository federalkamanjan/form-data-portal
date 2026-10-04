"""Shape the combined rows into the master table."""
from __future__ import annotations

import pandas as pd

from portal.config import master_columns

DEFAULT_SYSTEM_LABELS = {"source_label": "Source", "fetched_at": "Fetched At", "record_status": "Record Status"}


def select_master_columns(combined: pd.DataFrame, settings: dict) -> pd.DataFrame:
    """The fields (and the system columns, when switched on), in settings order."""
    cols = master_columns(settings)
    return combined.reindex(columns=cols).fillna("").reset_index(drop=True)


def to_output_headers(master: pd.DataFrame, settings: dict) -> pd.DataFrame:
    """Friendly column headers (Full Name, WhatsApp Contact...) when `output.use_labels_as_headers` is on."""
    out = settings["output"]
    if not out.get("use_labels_as_headers", True):
        return master
    names = {f["key"]: f["label"] for f in settings["fields"]}
    names.update({**DEFAULT_SYSTEM_LABELS, **out.get("system_column_labels", {})})
    return master.rename(columns=names)
