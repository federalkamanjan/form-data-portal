"""Run the whole flow on fetched forms: map, clean, combine, (optionally) dedupe, then save.

Two steps, so the operator can look before anything is saved (FR-12):
  prepare_run(...)  works everything out in memory and reports what would happen
  commit_run(...)   writes the layers and replaces the master (with a backup)

The master is rebuilt from scratch on every run from the links given in that run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from portal.cleaner import CleaningResult, clean_table
from portal.connector.models import LinkResult, LinkStatus
from portal.dedupe import deduplicate
from portal.mapper import MappingResult, apply_mapping, propose_mapping
from portal.storage import CommitInfo, StorageAdapter, select_master_columns, to_output_headers

FAILED_STATUSES = {LinkStatus.NO_ACCESS, LinkStatus.INVALID, LinkStatus.PUBLIC_LINK, LinkStatus.ERROR}


class CommitBlocked(RuntimeError):
    """The run has problems that stop it being saved (unless the operator chooses to save anyway)."""


@dataclass
class FormOutcome:
    form_id: str
    label: str
    status: str                       # "Processed", "Empty" or "Skipped"
    message: str = ""
    fetched_rows: int = 0
    raw: pd.DataFrame | None = None
    mapping: MappingResult | None = None
    mapped: pd.DataFrame | None = None
    cleaning: CleaningResult | None = None

    @property
    def included(self) -> bool:
        return self.status in ("Processed", "Empty")


@dataclass
class PreparedRun:
    fetched_at: str
    outcomes: list[FormOutcome]
    master: pd.DataFrame                      # internal column keys, ready to save
    duplicates_removed: pd.DataFrame
    blockers: list[str] = field(default_factory=list)   # problems that mean data is missing from the master
    warnings: list[str] = field(default_factory=list)

    @property
    def included(self) -> list[FormOutcome]:
        return [o for o in self.outcomes if o.included]

    @property
    def can_commit(self) -> bool:
        return bool(self.included) and not self.blockers

    def summary_lines(self) -> list[str]:
        lines = []
        for o in self.outcomes:
            if o.cleaning:
                r = o.cleaning.report
                lines.append(f"{o.label}: {o.fetched_rows} fetched, {r.valid + r.fixed} accepted, {r.rejected} rejected")
            else:
                lines.append(f"{o.label}: {o.status.lower()}" + (f" ({o.message})" if o.message else ""))
        lines.append(f"Master would have {len(self.master)} rows"
                     + (f" after removing {len(self.duplicates_removed)} duplicates." if len(self.duplicates_removed) else "."))
        lines += [f"Warning: {w}" for w in self.warnings]
        lines += [f"Problem: {b}" for b in self.blockers]
        return lines


@dataclass
class CommitResult:
    master: CommitInfo
    saved_forms: int


def prepare_run(results: list[LinkResult], settings: dict, *, mapping_store: Any = None,
                mapping_overrides: dict[str, MappingResult] | None = None, labels: dict[str, str] | None = None,
                include_unreviewed: bool = False, now: datetime | None = None) -> PreparedRun:
    """Map and clean every fetched form and build the combined master, without saving anything."""
    fetched_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    overrides, labels = mapping_overrides or {}, labels or {}
    outcomes: list[FormOutcome] = []
    blockers: list[str] = []
    warnings: list[str] = []
    frames: list[pd.DataFrame] = []

    for res in results:
        name = labels.get(res.form_id or "", "") or res.title or res.url
        if res.status in FAILED_STATUSES:
            blockers.append(f"{name}: {res.status.value}. {res.message}")
            continue
        if res.status == LinkStatus.DUPLICATE:
            warnings.append(f"{name}: {res.message}")
            continue
        if not res.has_table:
            continue

        outcome = FormOutcome(form_id=res.form_id, label=name, status="Processed", fetched_rows=res.row_count, raw=res.table)
        if res.status == LinkStatus.EMPTY:
            outcome.status, outcome.message = "Empty", "no responses yet"
            warnings.append(f"{name}: no responses yet.")
            outcomes.append(outcome)
            continue

        mapping = overrides.get(res.form_id) or propose_mapping(res.table, settings, form_id=res.form_id, store=mapping_store)
        outcome.mapping = mapping
        if mapping.needs_attention and not include_unreviewed and res.form_id not in overrides:
            outcome.status, outcome.message = "Skipped", "its fields need to be confirmed first"
            blockers.append(f"{name}: the fields need to be confirmed before this form can be included. " + " ".join(mapping.notes))
            outcomes.append(outcome)
            continue

        outcome.mapped = apply_mapping(res.table, mapping, settings)
        outcome.cleaning = clean_table(outcome.mapped, settings)
        outcomes.append(outcome)

        accepted = outcome.cleaning.accepted.copy()
        accepted["source_label"] = name
        accepted["fetched_at"] = fetched_at
        frames.append(accepted)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["response_id", "submitted_at", *[f["key"] for f in settings["fields"]], "record_status", "source_label", "fetched_at"])
    removed = combined.iloc[0:0].assign(duplicate_key=pd.Series(dtype=str))
    if settings["dedupe"].get("enabled", False):
        combined, removed = deduplicate(combined, settings)

    return PreparedRun(fetched_at=fetched_at, outcomes=outcomes, master=select_master_columns(combined, settings),
                       duplicates_removed=removed, blockers=blockers, warnings=warnings)


def commit_run(prepared: PreparedRun, storage: StorageAdapter, settings: dict, *, allow_partial: bool = False) -> CommitResult:
    """Save every layer, then replace the master. Refuses when data would be missing, unless allow_partial."""
    if not prepared.included:
        raise CommitBlocked("There is nothing to save: no form could be included.")
    if prepared.blockers and not allow_partial:
        raise CommitBlocked("Some links could not be included, so saving would leave their data out of the master: "
                            + " | ".join(prepared.blockers))

    with storage.write_lock():
        for o in prepared.included:
            storage.save_table("raw", o.form_id, o.raw)
            if o.cleaning is not None:
                storage.save_table("mapped", o.form_id, o.mapped)
                storage.save_table("cleaned", o.form_id, o.cleaning.accepted)
                storage.save_table("rejected", o.form_id, o.cleaning.rejected)
        if len(prepared.duplicates_removed):
            storage.save_table("duplicates", "removed_duplicates", prepared.duplicates_removed)
        info = storage.commit_master(to_output_headers(prepared.master, settings))
    return CommitResult(master=info, saved_forms=len(prepared.included))
