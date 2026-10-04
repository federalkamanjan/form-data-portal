"""Turn pipeline results into the tables and plain-language text the screens show. No Streamlit in here, so it can be tested."""
from __future__ import annotations

import pandas as pd

from portal.cleaner import REJECTED
from portal.connector.models import LinkResult, LinkStatus
from portal.pipeline import PreparedRun

BADGES = {
    LinkStatus.READY: "🟢 Ready",
    LinkStatus.EMPTY: "⚪ No responses yet",
    LinkStatus.NO_ACCESS: "🔴 No access",
    LinkStatus.INVALID: "🔴 Invalid link",
    LinkStatus.PUBLIC_LINK: "🟠 Wrong kind of link",
    LinkStatus.DUPLICATE: "🟡 Duplicate (skipped)",
    LinkStatus.ERROR: "🔴 Problem",
}
PREFLIGHT_BADGES = {
    "Looks good": "🟢 Looks good",
    "Wrong kind of link": "🟠 Wrong kind of link",
    "Invalid link": "🔴 Invalid link",
    "Duplicate": "🟡 Duplicate (skipped)",
}

HELP_MARKDOWN = """
**Use the form's *edit* link, the one you see when you are editing the form.**

1. Go to [forms.google.com](https://forms.google.com) and open your form.
2. Make sure you are on the editing page. You should see the **Questions** and **Responses** tabs at the top.
3. Copy the address from your browser's address bar. It looks like
   `https://docs.google.com/forms/d/…long code…/edit`.
4. Paste it here.

**These will not work:**
- The link from the **Send** button, or any `forms.gle/…` short link. That is the link people use to fill in the form.
- The link to the Google **Sheet** with the responses. Use the Form's link instead.

The form must belong to the Google account this portal was set up with, or be shared with it as an editor.
"""

STORAGE_NOTE = (
    "On Streamlit Community Cloud the server's files can be erased when the app restarts, so download "
    "your master CSV after each run and keep your own copy."
)


def _short(text: str, n: int = 60) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def preflight_badge(status: str) -> str:
    return PREFLIGHT_BADGES.get(status, status)


def results_table(results: list[LinkResult], labels: dict[str, str] | None = None) -> pd.DataFrame:
    """One row per pasted link: status badge, form name, responses fetched, and a plain-language note."""
    labels = labels or {}
    rows = []
    for r in results:
        name = labels.get(r.form_id or "", "") or r.title or _short(r.url)
        note = r.message if r.status != LinkStatus.READY else ""
        rows.append({"Status": BADGES.get(r.status, r.status.value), "Form": name,
                     "Responses": r.row_count if r.has_table else None, "Note": note})
    return pd.DataFrame(rows, columns=["Status", "Form", "Responses", "Note"])


def metrics(prepared: PreparedRun) -> dict[str, int]:
    done = [o for o in prepared.outcomes if o.cleaning]
    return {
        "forms": len(prepared.included),
        "fetched": sum(o.fetched_rows for o in prepared.included),
        "accepted": sum(o.cleaning.report.valid + o.cleaning.report.fixed for o in done),
        "rejected": sum(o.cleaning.report.rejected for o in done),
        "master": len(prepared.master),
        "duplicates": len(prepared.duplicates_removed),
    }


def _labelled(df: pd.DataFrame, settings: dict, source: str) -> pd.DataFrame:
    out = df.rename(columns={f["key"]: f["label"] for f in settings["fields"]})
    out.insert(0, "Source", source)
    return out


def _stack(frames: list[pd.DataFrame], columns: list[str]) -> pd.DataFrame:
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)


def _field_labels(settings: dict) -> list[str]:
    return [f["label"] for f in settings["fields"]]


def rejected_table(prepared: PreparedRun, settings: dict) -> pd.DataFrame:
    """Every rejected row (original values) with the reason, across all forms."""
    frames = []
    for o in prepared.outcomes:
        if o.cleaning is not None and len(o.cleaning.rejected):
            df = _labelled(o.cleaning.rejected, settings, o.label)
            frames.append(df.rename(columns={"reject_reason": "Why it was rejected", "flags": "Notes"}))
    cols = ["Source", *_field_labels(settings), "Why it was rejected", "Notes"]
    return _stack(frames, cols).reindex(columns=cols)


def flagged_table(prepared: PreparedRun, settings: dict) -> pd.DataFrame:
    """Accepted rows that carry a note worth a look (for example a likely email typo)."""
    frames = []
    for o in prepared.outcomes:
        if o.cleaning is not None:
            acc = o.cleaning.accepted
            flagged = acc[acc["flags"] != ""]
            if len(flagged):
                frames.append(_labelled(flagged, settings, o.label).rename(columns={"flags": "Notes", "record_status": "Status"}))
    cols = ["Source", *_field_labels(settings), "Status", "Notes"]
    return _stack(frames, cols).reindex(columns=cols)


def cleaned_dataset(prepared: PreparedRun, settings: dict) -> pd.DataFrame:
    """The full cleaned dataset for download: every row from every form, including status, reason and notes."""
    frames = []
    for o in prepared.outcomes:
        if o.cleaning is not None:
            df = _labelled(o.cleaning.cleaned, settings, o.label)
            frames.append(df.rename(columns={"record_status": "Status", "reject_reason": "Why it was rejected", "flags": "Notes"}))
    cols = ["Source", "response_id", "submitted_at", *_field_labels(settings), "Status", "Why it was rejected", "Notes"]
    return _stack(frames, cols).reindex(columns=cols)


def changes_by_form(prepared: PreparedRun, settings: dict) -> list[tuple[str, list[str]]]:
    """What the cleaner did, one block of plain lines per form."""
    return [(o.label, o.cleaning.report.summary_lines(settings)) for o in prepared.outcomes if o.cleaning is not None]


def needs_field_review(prepared: PreparedRun) -> bool:
    return any(o.mapping is not None and o.mapping.needs_attention for o in prepared.outcomes)


def csv_bytes(df: pd.DataFrame) -> bytes:
    """UTF-8 with BOM so Excel shows Urdu and accented text correctly."""
    return df.to_csv(index=False).encode("utf-8-sig")


def rejected_count_text(prepared: PreparedRun) -> str:
    n = metrics(prepared)["rejected"]
    return f"{n} row{'s' if n != 1 else ''} rejected"
