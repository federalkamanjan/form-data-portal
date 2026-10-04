"""Clean a mapped table: every value standardised, every row marked Valid, Fixed or Rejected with a reason."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from .schools import find_near_duplicates
from .steps import STEP_LABELS, STEPS, CellContext

VALID, FIXED, REJECTED = "Valid", "Fixed", "Rejected"
CARRIED = ["response_id", "submitted_at"]


@dataclass
class CellResult:
    value: str
    changed: bool
    steps_changed: list[str]
    errors: list[tuple[str, str]]
    flags: list[tuple[str, str]]


@dataclass
class CleaningReport:
    total: int = 0
    valid: int = 0
    fixed: int = 0
    rejected: int = 0
    rule_counts: Counter = field(default_factory=Counter)     # (field_key, step) -> values changed
    flag_counts: Counter = field(default_factory=Counter)     # message -> rows
    reject_reasons: Counter = field(default_factory=Counter)  # message -> rows

    def summary_lines(self, settings: dict) -> list[str]:
        labels = {f["key"]: f["label"] for f in settings["fields"]}
        lines = [f"{self.total} rows: {self.valid} valid, {self.fixed} fixed, {self.rejected} rejected."]
        for (key, step), n in sorted(self.rule_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            if step in STEP_LABELS and n:
                lines.append(f"  {labels.get(key, key)}: {n} {STEP_LABELS[step]}")
        if self.reject_reasons:
            lines.append("Rejected because:")
            lines += [f"  {n} x {msg}" for msg, n in self.reject_reasons.most_common()]
        if self.flag_counts:
            lines.append("Flagged for a look (kept):")
            lines += [f"  {n} x {msg}" for msg, n in self.flag_counts.most_common()]
        return lines


@dataclass
class CleaningResult:
    cleaned: pd.DataFrame    # every row with record_status, reject_reason, flags
    accepted: pd.DataFrame   # Valid and Fixed rows only
    rejected: pd.DataFrame   # original values of rejected rows plus the reason
    report: CleaningReport


def clean_value(raw: str, field_cfg: dict, settings: dict) -> CellResult:
    """Run one field's cleaning steps, in the order the settings list them."""
    ctx = CellContext(field=field_cfg, settings=settings)
    value, changed_steps = raw, []
    for name in field_cfg["cleaning"]:
        before = value
        value = STEPS[name](value, ctx)
        if value != before:
            changed_steps.append(name)
    if field_cfg.get("required") and value.strip() == "":
        ctx.error("missing", "is empty" if raw.strip() == "" else "had no usable value")
    return CellResult(value=value, changed=value != raw, steps_changed=changed_steps, errors=ctx.errors, flags=ctx.flags)


def clean_table(mapped: pd.DataFrame, settings: dict) -> CleaningResult:
    fields = settings["fields"]
    keys = [f["key"] for f in fields]
    carried = [c for c in CARRIED if c in mapped.columns]
    data = mapped.fillna("").astype(str)
    reject_codes = set(settings["cleaning"].get("reject_flags", []))

    # pass 1: clean each cell (identical raw values are cleaned once)
    cache: dict[tuple[str, str], CellResult] = {}
    rows: list[dict[str, CellResult]] = []
    for values in data[keys].itertuples(index=False, name=None):
        cells = {}
        for f, raw in zip(fields, values):
            ck = (f["key"], raw)
            if ck not in cache:
                cache[ck] = clean_value(raw, f, settings)
            cells[f["key"]] = cache[ck]
        rows.append(cells)

    # pass 2: spot near-duplicate school spellings across the whole table
    extra_flags: list[list[tuple[str, str]]] = [[] for _ in rows]
    school_field = next((f for f in fields if "normalise_school" in f["cleaning"]), None)
    if school_field:
        k = school_field["key"]
        usable = [i for i, cells in enumerate(rows) if not any(c.errors for c in cells.values())]
        similar = find_near_duplicates([rows[i][k].value for i in usable], settings["cleaning"].get("school_similarity_threshold", 0.9))
        for i in usable:
            name = rows[i][k].value
            if name in similar:
                extra_flags[i].append(("school_near_duplicate", f"{school_field['label']}: resembles '{similar[name]}', please check"))

    # pass 3: decide each row's status
    report = CleaningReport(total=len(rows))
    out_rows, reject_idx = [], []
    for i, cells in enumerate(rows):
        errors = [e for c in cells.values() for e in c.errors]
        flags = [fl for c in cells.values() for fl in c.flags] + extra_flags[i]
        errors += [fl for fl in flags if fl[0] in reject_codes]
        for c_key, c in cells.items():
            for step in c.steps_changed:
                report.rule_counts[(c_key, step)] += 1
        if errors:
            status = REJECTED
            reject_idx.append(i)
            report.rejected += 1
        elif any(c.changed for c in cells.values()):
            status = FIXED
            report.fixed += 1
        else:
            status = VALID
            report.valid += 1
        for _, msg in errors:
            report.reject_reasons[msg] += 1
        for _, msg in flags:
            report.flag_counts[msg] += 1
        out_rows.append({
            **{c: data[c].iat[i] for c in carried},
            **{key: cells[key].value for key in keys},
            "record_status": status,
            "reject_reason": "; ".join(dict.fromkeys(m for _, m in errors)),
            "flags": "; ".join(dict.fromkeys(m for _, m in flags)),
        })

    columns = carried + keys + ["record_status", "reject_reason", "flags"]
    cleaned = pd.DataFrame(out_rows, columns=columns, dtype=str)
    accepted = cleaned[cleaned["record_status"] != REJECTED].drop(columns=["reject_reason"]).reset_index(drop=True)

    rejected = data.iloc[reject_idx][carried + keys].reset_index(drop=True)
    rejected["reject_reason"] = cleaned.iloc[reject_idx]["reject_reason"].reset_index(drop=True)
    rejected["flags"] = cleaned.iloc[reject_idx]["flags"].reset_index(drop=True)
    return CleaningResult(cleaned=cleaned, accepted=accepted, rejected=rejected, report=report)
