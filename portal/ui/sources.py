"""The operator's list of form links: editing, remembering, and a quick check before anything is fetched."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from portal.connector import extract_urls, parse_link

COLUMNS = ["Label", "Link"]


@dataclass(frozen=True)
class SourceEntry:
    label: str = ""
    url: str = ""


@dataclass(frozen=True)
class PreflightRow:
    number: int
    label: str
    url: str
    status: str      # "Looks good", "Wrong kind of link", "Invalid link", "Duplicate"
    message: str
    ok: bool
    form_id: str | None = None


def frame_from_entries(entries: list[SourceEntry]) -> pd.DataFrame:
    rows = [{"Label": e.label, "Link": e.url} for e in entries]
    return pd.DataFrame(rows, columns=COLUMNS, dtype=str)


def entries_from_frame(df: pd.DataFrame) -> list[SourceEntry]:
    """Rows from the editable table; blank rows are dropped and stray spaces trimmed."""
    entries = []
    for _, row in df.iterrows():
        label = "" if pd.isna(row.get("Label")) else str(row.get("Label")).strip()
        url = "" if pd.isna(row.get("Link")) else str(row.get("Link")).strip()
        if label or url:
            entries.append(SourceEntry(label=label, url=url))
    return entries


def _identity(url: str) -> str:
    link = parse_link(url)
    return link.form_id or url.strip().lower()


def merge_pasted(entries: list[SourceEntry], text: str) -> tuple[list[SourceEntry], int, int]:
    """Add every link found in the pasted text. Returns (new list, how many added, how many skipped as already present)."""
    seen = {_identity(e.url) for e in entries if e.url}
    out, added, skipped = list(entries), 0, 0
    for url in extract_urls(text):
        key = _identity(url)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        out.append(SourceEntry(url=url))
        added += 1
    return out, added, skipped


def preflight(entries: list[SourceEntry]) -> list[PreflightRow]:
    """Check each link's shape without contacting Google."""
    rows, first_seen = [], {}
    for n, e in enumerate((e for e in entries if e.url), start=1):
        link = parse_link(e.url)
        if link.problem:
            rows.append(PreflightRow(n, e.label, e.url, link.problem.value if link.problem.value != "Public link" else "Wrong kind of link", link.message, False))
        elif link.form_id in first_seen:
            rows.append(PreflightRow(n, e.label, e.url, "Duplicate", f"Same form as row {first_seen[link.form_id]}, so it will be skipped.", False, link.form_id))
        else:
            first_seen[link.form_id] = n
            rows.append(PreflightRow(n, e.label, e.url, "Looks good", "", True, link.form_id))
    return rows


def labels_by_form_id(entries: list[SourceEntry]) -> dict[str, str]:
    """The operator's own names for forms (only where they typed one)."""
    labels: dict[str, str] = {}
    for e in entries:
        if e.label and e.url:
            form_id = parse_link(e.url).form_id
            if form_id and form_id not in labels:
                labels[form_id] = e.label
    return labels


def urls_text(entries: list[SourceEntry]) -> str:
    return "\n".join(e.url for e in entries if e.url)


def load_sources(path: str | Path) -> list[SourceEntry]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [SourceEntry(label=str(d.get("label", "")), url=str(d.get("url", ""))) for d in data if isinstance(d, dict)]
    except (ValueError, TypeError):
        return []


def save_sources(path: str | Path, entries: list[SourceEntry]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([{"label": e.label, "url": e.url} for e in entries], indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
