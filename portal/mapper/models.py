"""Result types for the mapper."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MappingStatus(str, Enum):
    SAVED = "Saved"        # confirmed earlier and still valid: no action needed
    AUTO = "Auto-detected" # every field found with high confidence: one-click confirm
    REVIEW = "Needs review"  # a field is missing or uncertain
    CHANGED = "Changed"    # the form's questions changed since the saved mapping


@dataclass
class FieldMatch:
    field_key: str
    column: str | None = None          # the raw-table column that feeds this field
    method: str = "none"               # title / content / title+content / respondent_email / saved / manual / none
    score: float = 0.0
    fallback: str | None = None        # fills blanks in the primary column (e.g. a typed email question)
    candidates: list[tuple[str, float]] = field(default_factory=list)  # best alternatives for the confirm screen


@dataclass
class MappingResult:
    form_id: str | None
    status: MappingStatus
    matches: dict[str, FieldMatch]
    notes: list[str] = field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        return self.status != MappingStatus.SAVED

    @property
    def needs_attention(self) -> bool:
        return self.status in (MappingStatus.REVIEW, MappingStatus.CHANGED)

    def columns(self) -> dict[str, str | None]:
        return {key: m.column for key, m in self.matches.items()}

    def to_saved(self) -> dict:
        return {
            "mapping": self.columns(),
            "fallbacks": {k: m.fallback for k, m in self.matches.items() if m.fallback},
        }


class MappingError(ValueError):
    """The mapping refers to a column that is not in the table."""
