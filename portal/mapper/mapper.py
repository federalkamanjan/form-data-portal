"""Pick which column of a form's raw table feeds each configured field."""
from __future__ import annotations

from typing import Any

import pandas as pd

from portal.connector.forms_api import SYSTEM_COLUMNS

from .matching import combined_score, content_score, title_score
from .models import FieldMatch, MappingError, MappingResult, MappingStatus

RESPONDENT_EMAIL = "respondent_email"
RESPONDENT_EMAIL_SCORE = 0.97
CARRIED_COLUMNS = ["response_id", "submitted_at"]  # kept alongside the fields for dedupe and audit


def _thresholds(settings: dict) -> tuple[float, float]:
    cfg = settings.get("mapping", {})
    return cfg.get("auto_threshold", 0.85), cfg.get("suggest_threshold", 0.6)


def _has_data(table: pd.DataFrame, column: str) -> bool:
    return column in table.columns and bool(table[column].fillna("").astype(str).str.strip().any())


def detect_mapping(table: pd.DataFrame, settings: dict) -> MappingResult:
    """Automatic proposal: titles first, content shape as a second signal, each column used once."""
    auto, suggest = _thresholds(settings)
    fields = settings["fields"]
    question_columns = [c for c in table.columns if c not in SYSTEM_COLUMNS]

    scores: dict[tuple[str, str], float] = {}
    methods: dict[tuple[str, str], str] = {}
    for f in fields:
        keywords = [f["label"], *f["keywords"]]
        for col in question_columns:
            t_s = title_score(col, keywords)
            c_s = content_score(table[col], f["type"])
            s = combined_score(t_s, c_s)
            if s > 0:
                scores[(f["key"], col)] = s
                methods[(f["key"], col)] = "title+content" if t_s >= 0.6 and c_s > 0 else ("title" if t_s >= c_s else "content")

    field_order = {f["key"]: i for i, f in enumerate(fields)}
    col_order = {c: i for i, c in enumerate(question_columns)}
    ranked = sorted(
        ((s, key, col) for (key, col), s in scores.items() if s >= suggest),
        key=lambda x: (-x[0], field_order[x[1]], col_order[x[2]]),
    )
    matches = {f["key"]: FieldMatch(field_key=f["key"]) for f in fields}
    used_cols: set[str] = set()
    for s, key, col in ranked:
        if matches[key].column is None and col not in used_cols:
            matches[key].column, matches[key].score, matches[key].method = col, s, methods[(key, col)]
            used_cols.add(col)

    for f in fields:  # best alternatives for the confirm screen
        alts = sorted(((col, s) for (key, col), s in scores.items() if key == f["key"]), key=lambda x: -x[1])
        matches[f["key"]].candidates = alts[:3]

    # The email used to fill the form beats a typed email question; the typed one fills any blanks.
    email_field = next((f for f in fields if f["type"] == "email"), None)
    if email_field and _has_data(table, RESPONDENT_EMAIL):
        m = matches[email_field["key"]]
        m.fallback = m.column
        m.column, m.score, m.method = RESPONDENT_EMAIL, RESPONDENT_EMAIL_SCORE, "respondent_email"

    notes, uncertain = [], False
    for f in fields:
        m = matches[f["key"]]
        if m.column is None:
            if f["required"]:
                notes.append(f"No question matched '{f['label']}'. Please choose one.")
                uncertain = True
        elif m.score < auto:
            notes.append(f"'{f['label']}' was matched to '{m.column}' with low confidence. Please check.")
            uncertain = True
    status = MappingStatus.REVIEW if uncertain else MappingStatus.AUTO
    return MappingResult(form_id=None, status=status, matches=matches, notes=notes)


def result_from_columns(settings: dict, mapping: dict[str, str | None], fallbacks: dict[str, str] | None = None,
                        form_id: str | None = None, status: MappingStatus = MappingStatus.SAVED,
                        method: str = "saved") -> MappingResult:
    """Build a result from explicit choices (a saved mapping, or the operator's picks on the confirm screen)."""
    fallbacks = fallbacks or {}
    matches = {}
    for f in settings["fields"]:
        col = mapping.get(f["key"])
        matches[f["key"]] = FieldMatch(field_key=f["key"], column=col, method=method if col else "none",
                                       score=1.0 if col else 0.0, fallback=fallbacks.get(f["key"]))
    return MappingResult(form_id=form_id, status=status, matches=matches)


def confirm_choices(proposal: MappingResult, choices: dict[str, str | None], settings: dict,
                    form_id: str | None = None) -> MappingResult:
    """The mapping the operator confirmed on screen. A fallback column (a typed email question filling blanks
    in the collected email) is kept only if the operator left that field's choice unchanged."""
    fallbacks = {}
    for key, column in choices.items():
        match = proposal.matches.get(key)
        if match and match.fallback and match.column == column:
            fallbacks[key] = match.fallback
    return result_from_columns(settings, choices, fallbacks, form_id=form_id or proposal.form_id,
                               status=MappingStatus.SAVED, method="confirmed")


def propose_mapping(table: pd.DataFrame, settings: dict, form_id: str | None = None, store: Any = None) -> MappingResult:
    """Use the saved mapping when it still fits this form; otherwise detect afresh and say why."""
    saved = store.get(form_id) if (store is not None and form_id) else None
    if saved:
        mapping = saved.get("mapping", {})
        fallbacks = saved.get("fallbacks", {})
        wanted = {c for c in [*mapping.values(), *fallbacks.values()] if c}
        missing = sorted(c for c in wanted if c not in table.columns)
        field_keys = {f["key"] for f in settings["fields"]}
        if not missing and set(mapping) == field_keys:
            return result_from_columns(settings, mapping, fallbacks, form_id=form_id)
        fresh = detect_mapping(table, settings)
        fresh.form_id = form_id
        fresh.status = MappingStatus.CHANGED
        if missing:
            fresh.notes.insert(0, "These questions were renamed or removed since you last confirmed this form: "
                               + ", ".join(f"'{m}'" for m in missing) + ". Please confirm the fields again.")
        else:
            fresh.notes.insert(0, "The portal's fields have changed since you last confirmed this form. Please confirm again.")
        return fresh

    result = detect_mapping(table, settings)
    result.form_id = form_id
    return result


def apply_mapping(table: pd.DataFrame, result: MappingResult, settings: dict) -> pd.DataFrame:
    """The mapped table: response_id, submitted_at, then one column per field. Values stay raw."""
    needed = [m.column for m in result.matches.values() if m.column] + [m.fallback for m in result.matches.values() if m.fallback]
    absent = sorted({c for c in needed if c not in table.columns})
    if absent:
        raise MappingError("These columns are not in the table: " + ", ".join(absent))

    raw = table.fillna("").astype(str)
    out = pd.DataFrame(index=raw.index)
    for col in CARRIED_COLUMNS:
        out[col] = raw[col] if col in raw.columns else ""
    for f in settings["fields"]:
        m = result.matches.get(f["key"])
        series = raw[m.column] if m and m.column else pd.Series("", index=raw.index)
        if m and m.fallback:
            series = series.where(series.str.strip() != "", raw[m.fallback])
        out[f["key"]] = series
    return out.reset_index(drop=True)
