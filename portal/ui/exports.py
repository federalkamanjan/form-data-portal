"""Tables and files the screens show or offer for download (no Streamlit here)."""
from __future__ import annotations

import pandas as pd

from portal.pipeline import PreparedRun
from portal.storage.master import DEFAULT_SYSTEM_LABELS

from .sources import Source


def csv_bytes(df: pd.DataFrame) -> bytes:
    """UTF-8 with BOM so Excel shows Urdu and accented text correctly."""
    return df.to_csv(index=False).encode("utf-8-sig")


def friendly_headers(df: pd.DataFrame, settings: dict) -> pd.DataFrame:
    names = {f["key"]: f["label"] for f in settings["fields"]}
    names.update(DEFAULT_SYSTEM_LABELS)
    names.update({"response_id": "Response ID", "submitted_at": "Submitted At", "flags": "Flags",
                  "reject_reason": "Reason Rejected"})
    return df.rename(columns=names)


def cleaned_all(prepared: PreparedRun, settings: dict) -> pd.DataFrame:
    """Every accepted row from every included form, with its status and flags."""
    frames = []
    for o in prepared.included:
        if o.cleaning is None:
            continue
        acc = o.cleaning.accepted.copy()
        acc.insert(0, "source_label", o.label)
        frames.append(acc)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return friendly_headers(out, settings)


def rejected_all(prepared: PreparedRun, settings: dict) -> pd.DataFrame:
    """Every rejected row (original values) with the reason it was rejected."""
    frames = []
    for o in prepared.included:
        if o.cleaning is None:
            continue
        rej = o.cleaning.rejected.copy()
        rej.insert(0, "source_label", o.label)
        frames.append(rej)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return friendly_headers(out, settings)


def mapping_rows(outcome, settings: dict) -> pd.DataFrame:
    """'How columns were matched' for one form."""
    cfg = settings.get("mapping", {})
    auto, suggest = cfg.get("auto_threshold", 0.85), cfg.get("suggest_threshold", 0.6)
    rows = []
    for f in settings["fields"]:
        m = outcome.mapping.matches[f["key"]]
        if m.column is None:
            source, confidence = "(not found)", "Not found"
        else:
            source = "The email used to fill the form" if m.column == "respondent_email" else m.column
            if m.fallback:
                source += f" (blanks filled from '{m.fallback}')"
            confidence = "High" if m.score >= auto else ("Please check" if m.score >= suggest else "Low")
        rows.append({"Field": f["label"], "Taken from": source, "Confidence": confidence})
    return pd.DataFrame(rows)


def link_summary(sources: list[Source], results: list, prepared: PreparedRun) -> pd.DataFrame:
    """One plain-language row per link: what happened to it and why (FR-13)."""
    by_id = {o.form_id: o for o in prepared.outcomes}
    rows = []
    for src, res in zip(sources, results):
        outcome = by_id.get(res.form_id) if res.form_id else None
        accepted = rejected = ""
        note = res.message
        status = src.badge
        if outcome is not None:
            if outcome.status == "Skipped":
                status, note = "⚠️ Fields need checking", outcome.message
            elif outcome.cleaning is not None:
                accepted = str(outcome.cleaning.report.valid + outcome.cleaning.report.fixed)
                rejected = str(outcome.cleaning.report.rejected)
        rows.append({
            "Form": src.label or res.title or src.short_url,
            "Status": status,
            "Responses": str(res.row_count) if res.has_table else "",
            "Accepted": accepted,
            "Rejected": rejected,
            "Note": note,
        })
    return pd.DataFrame(rows)
