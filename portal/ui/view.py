"""Everything the Streamlit page needs that is not Streamlit itself, so it can be tested.

Sources are plain dicts: {"id": int, "url": str, "label": str}.
"""
from __future__ import annotations

import re

import pandas as pd

from portal.connector import LinkResult, LinkStatus, extract_urls, parse_link
from portal.mapper import MappingResult, MappingStatus
from portal.mapper.mapper import RESPONDENT_EMAIL
from portal.pipeline import FAILED_STATUSES, PreparedRun

BADGES = {
    LinkStatus.READY: ("✅", "Ready"),
    LinkStatus.EMPTY: ("⚪", "No responses yet"),
    LinkStatus.NO_ACCESS: ("🔒", "No access"),
    LinkStatus.INVALID: ("❌", "Invalid link"),
    LinkStatus.PUBLIC_LINK: ("⚠️", "Public link"),
    LinkStatus.DUPLICATE: ("♻️", "Duplicate"),
    LinkStatus.ERROR: ("🟠", "Error"),
}
NEEDS_REVIEW_BADGE = "🟡 Needs review"

FRIENDLY_COLUMNS = {
    "response_id": "Response ID", "submitted_at": "Submitted At", "record_status": "Record Status",
    "flags": "Flags", "reject_reason": "Reason", "source_label": "Source", "fetched_at": "Fetched At",
}


def badge(status: LinkStatus) -> str:
    icon, text = BADGES[status]
    return f"{icon} {text}"


# ---------------------------------------------------------------- the list of links

def add_sources(sources: list[dict], pasted: str, next_id: int) -> tuple[list[dict], int, list[str]]:
    """Add every link found in `pasted`. Repeats of a form already in the list are skipped."""
    urls = extract_urls(pasted)
    if not urls:
        return sources, next_id, ["No links were found in what you pasted. Paste the form's edit link (it ends in /edit)."]
    sources = [dict(s) for s in sources]
    known_urls = {s["url"] for s in sources}
    known_ids = {fid for s in sources if (fid := parse_link(s["url"]).form_id)}
    added = skipped = 0
    for url in urls:
        form_id = parse_link(url).form_id
        if url in known_urls or (form_id and form_id in known_ids):
            skipped += 1
            continue
        sources.append({"id": next_id, "url": url, "label": ""})
        next_id += 1
        known_urls.add(url)
        if form_id:
            known_ids.add(form_id)
        added += 1
    notes = []
    if added:
        notes.append(f"Added {added} link{'s' if added != 1 else ''}.")
    if skipped:
        notes.append(f"{skipped} link{'s were' if skipped != 1 else ' was'} already in the list, so skipped.")
    return sources, next_id, notes


def remove_source(sources: list[dict], source_id: int) -> list[dict]:
    return [s for s in sources if s["id"] != source_id]


def sources_text(sources: list[dict]) -> str:
    return "\n".join(s["url"] for s in sources)


def short_url(url: str) -> str:
    form_id = parse_link(url).form_id
    if form_id:
        return f"Form …{form_id[-6:]}"
    return url if len(url) <= 48 else url[:45] + "…"


def source_status(source: dict, checks: dict[str, LinkResult], results_by_url: dict[str, LinkResult]) -> tuple[str, str]:
    """(badge, note) for one row: the latest fetch wins, then an access check, then the shape of the link."""
    url = source["url"]
    fetched = results_by_url.get(url)
    if fetched is not None:
        if fetched.status == LinkStatus.READY:
            return f"{badge(LinkStatus.READY)} · {fetched.row_count} responses", ""
        return badge(fetched.status), fetched.message
    checked = checks.get(url)
    if checked is not None:
        return badge(checked.status), ("Title: " + checked.title if checked.status == LinkStatus.READY else checked.message)
    parsed = parse_link(url)
    if parsed.problem:
        return badge(parsed.problem), parsed.message
    return "🔗 Link looks fine", "Not checked yet"


def label_hint(source: dict, checks: dict[str, LinkResult]) -> str:
    checked = checks.get(source["url"])
    return checked.title if checked and checked.title else "Label (optional), e.g. Grade 9 Chemistry"


def labels_map(sources: list[dict], results: list[LinkResult]) -> dict[str, str]:
    """form_id -> the label the operator typed (only where they typed one)."""
    typed = {s["url"]: s["label"].strip() for s in sources if s["label"].strip()}
    return {r.form_id: typed[r.url] for r in results if r.form_id and r.url in typed}


# ---------------------------------------------------------------- the review

def run_metrics(prepared: PreparedRun) -> dict[str, int]:
    accepted = rejected = fetched = flagged = 0
    for o in prepared.included:
        fetched += o.fetched_rows
        if o.cleaning:
            accepted += o.cleaning.report.valid + o.cleaning.report.fixed
            rejected += o.cleaning.report.rejected
            flagged += int((o.cleaning.accepted["flags"] != "").sum())
    return {"forms": len(prepared.included), "fetched": fetched, "accepted": accepted, "rejected": rejected,
            "flagged": flagged, "master_rows": len(prepared.master), "duplicates_removed": len(prepared.duplicates_removed)}


def link_table(prepared: PreparedRun, results: list[LinkResult], sources: list[dict]) -> pd.DataFrame:
    """One plain-language row per pasted link."""
    typed = {s["url"]: s["label"].strip() for s in sources if s["label"].strip()}
    outcomes = {o.form_id: o for o in prepared.outcomes}
    rows = []
    for r in results:
        name = typed.get(r.url) or r.title or short_url(r.url)
        o = outcomes.get(r.form_id) if r.form_id else None
        if r.status in FAILED_STATUSES or r.status == LinkStatus.DUPLICATE:
            rows.append((name, badge(r.status), "", "", "", r.message))
        elif o is not None and o.status == "Skipped":
            rows.append((name, NEEDS_REVIEW_BADGE, o.fetched_rows, "", "", "The portal isn't sure which questions are which. " + " ".join(o.mapping.notes if o.mapping else [])))
        elif o is not None and o.cleaning is not None:
            rep = o.cleaning.report
            flagged = int((o.cleaning.accepted["flags"] != "").sum())
            note = f"{flagged} kept but flagged for a look." if flagged else ""
            rows.append((name, badge(LinkStatus.READY), o.fetched_rows, rep.valid + rep.fixed, rep.rejected, note))
        else:
            rows.append((name, badge(r.status), 0, 0, 0, r.message))
    return pd.DataFrame(rows, columns=["Form", "Status", "Fetched", "Accepted", "Rejected", "Note"])


def friendly(df: pd.DataFrame, settings: dict) -> pd.DataFrame:
    names = {f["key"]: f["label"] for f in settings["fields"]}
    names.update(FRIENDLY_COLUMNS)
    return df.rename(columns=names)


def _stack(prepared: PreparedRun, attr: str) -> pd.DataFrame:
    frames = []
    for o in prepared.included:
        if o.cleaning is None:
            continue
        frame = getattr(o.cleaning, attr).copy()
        frame.insert(0, "source_label", o.label)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def cleaned_dataset(prepared: PreparedRun, settings: dict) -> pd.DataFrame:
    """Every accepted row from every form, with its status and flags (the separate cleaned sheet)."""
    return friendly(_stack(prepared, "accepted"), settings)


def rejected_dataset(prepared: PreparedRun, settings: dict) -> pd.DataFrame:
    return friendly(_stack(prepared, "rejected"), settings)


def flagged_dataset(prepared: PreparedRun, settings: dict) -> pd.DataFrame:
    stacked = _stack(prepared, "accepted")
    if stacked.empty:
        return stacked
    return friendly(stacked[stacked["flags"] != ""].reset_index(drop=True), settings)


def csv_bytes(df: pd.DataFrame) -> bytes:
    """UTF-8 with BOM so Excel opens Urdu and accented names correctly."""
    return df.to_csv(index=False).encode("utf-8-sig")


# ---------------------------------------------------------------- confirming which question is which

NONE_CHOICE = "__none__"   # selectbox value meaning "this form has no such question"
NONE_TEXT = "— this form has no such question —"


def colored(colour: str, text: str) -> str:
    """Streamlit coloured text, e.g. :green[Looks right]."""
    return f":{colour}[{text}]"


def needs_confirmation(results: list[LinkResult], confirmed: dict[str, MappingResult]) -> list[LinkResult]:
    """Fetched forms (with responses) whose fields have not been confirmed yet."""
    return [r for r in results if r.has_table and r.status == LinkStatus.READY and r.form_id not in confirmed]


def column_options(table: pd.DataFrame) -> list[str]:
    """Questions the operator can pick from, in form order. The collected email is offered only if it has data."""
    options = []
    for col in table.columns:
        if col in ("response_id", "submitted_at"):
            continue
        if col == RESPONDENT_EMAIL and not table[col].fillna("").astype(str).str.strip().any():
            continue
        options.append(col)
    return options


def _sample(table: pd.DataFrame, column: str, width: int = 28) -> str:
    values = table[column].fillna("").astype(str).str.strip()
    values = values[values != ""]
    if values.empty:
        return ""
    text = values.iloc[0]
    return text if len(text) <= width else text[: width - 1] + "…"


def column_display(table: pd.DataFrame, column: str) -> str:
    """How an option reads in the drop-down: the question plus an example answer."""
    if column == NONE_CHOICE:
        return NONE_TEXT
    name = "Email the person used to fill the form" if column == RESPONDENT_EMAIL else column
    sample = _sample(table, column)
    return f"{name}   (e.g. {sample})" if sample else name


def choice_confidence(proposed_column: str | None, proposed_score: float, chosen: str | None, settings: dict) -> tuple[str, str]:
    """(colour, short words) shown under each drop-down."""
    auto = settings.get("mapping", {}).get("auto_threshold", 0.85)
    if chosen != proposed_column:
        return "blue", "Your choice"
    if chosen is None:
        return "red", "No matching question was found"
    if proposed_score >= auto:
        return "green", "Looks right"
    return "orange", "Please check this one"


def validate_choices(choices: dict[str, str | None], settings: dict) -> list[str]:
    """Problems that stop the operator confirming: one question picked for two fields."""
    labels = {f["key"]: f["label"] for f in settings["fields"]}
    users: dict[str, list[str]] = {}
    for key, col in choices.items():
        if col:
            users.setdefault(col, []).append(labels[key])
    return [f"The question \"{col}\" is chosen for both {' and '.join(names)}. Each question can only be used once."
            for col, names in users.items() if len(names) > 1]


def missing_required(choices: dict[str, str | None], settings: dict) -> list[str]:
    return [f["label"] for f in settings["fields"] if f["required"] and not choices.get(f["key"])]


def form_badge(proposal: MappingResult) -> tuple[str, str]:
    if proposal.status == MappingStatus.AUTO:
        return "green", "Looks right"
    if proposal.status == MappingStatus.CHANGED:
        return "orange", "Questions changed since last time"
    return "orange", "Needs a quick check"


def mapping_rows(result: MappingResult, table: pd.DataFrame, settings: dict) -> pd.DataFrame:
    """Field -> question table for the 'which questions were used' view."""
    rows = []
    for f in settings["fields"]:
        m = result.matches.get(f["key"])
        col = m.column if m else None
        rows.append((f["label"], column_display(table, col) if col else NONE_TEXT))
    return pd.DataFrame(rows, columns=["Field", "Question used"])
