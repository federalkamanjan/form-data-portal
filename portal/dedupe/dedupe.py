"""Optional de-duplication of the combined rows (off by default: settings `dedupe.enabled`)."""
from __future__ import annotations

import pandas as pd


def _normalised(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.lower()


def duplicate_keys(df: pd.DataFrame, key_fields: list[str]) -> pd.Series:
    """One key per row: the first non-empty key field (email, else WhatsApp). Empty means 'never a duplicate'."""
    key = pd.Series("", index=df.index, dtype=str)
    for field in key_fields:
        if field not in df.columns:
            continue
        norm = _normalised(df[field])
        take = (key == "") & (norm != "")
        key[take] = field + ":" + norm[take]
    return key


def deduplicate(df: pd.DataFrame, settings: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (kept rows in original order, removed rows with the key they shared).

    keep = "newest" (latest submission), "first" (earliest) or "most_complete" (most fields filled, then newest).
    """
    cfg = settings["dedupe"]
    keep = cfg.get("keep", "newest")
    field_keys = [f["key"] for f in settings["fields"]]
    if df.empty:
        return df.copy(), df.assign(duplicate_key=pd.Series(dtype=str)).iloc[0:0]

    key = duplicate_keys(df, cfg.get("key", []))
    stamp = (pd.to_datetime(df["submitted_at"], utc=True, errors="coerce", format="ISO8601")
             if "submitted_at" in df.columns else pd.Series(pd.NaT, index=df.index))
    filled = sum((_normalised(df[f]) != "").astype(int) for f in field_keys if f in df.columns)
    work = pd.DataFrame({
        "_key": key,
        "_pos": range(len(df)),
        "_newest": stamp.fillna(pd.Timestamp.min.tz_localize("UTC")),
        "_oldest": stamp.fillna(pd.Timestamp.max.tz_localize("UTC")),
        "_filled": filled,
    }, index=df.index)

    cand = work[work["_key"] != ""]
    if keep == "first":
        ordered = cand.sort_values(["_key", "_oldest", "_pos"], ascending=[True, True, True])
    elif keep == "most_complete":
        ordered = cand.sort_values(["_key", "_filled", "_newest", "_pos"], ascending=[True, False, False, False])
    else:
        ordered = cand.sort_values(["_key", "_newest", "_pos"], ascending=[True, False, False])
    winners = set(ordered.groupby("_key").head(1)["_pos"])

    keep_mask = (work["_key"] == "") | work["_pos"].isin(winners)
    kept = df[keep_mask].reset_index(drop=True)
    removed = df[~keep_mask].copy()
    removed["duplicate_key"] = key[~keep_mask]
    return kept, removed.reset_index(drop=True)
