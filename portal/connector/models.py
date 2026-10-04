"""Shared result types for the connector."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LinkStatus(str, Enum):
    READY = "Ready"
    EMPTY = "Empty"              # form is reachable but has no responses yet
    NO_ACCESS = "No access"
    INVALID = "Invalid link"
    PUBLIC_LINK = "Public link"  # a respondent link, not an edit link
    DUPLICATE = "Duplicate"      # same form pasted twice (skipped, a warning rather than a failure)
    ERROR = "Error"              # Google problem that survived retries


@dataclass
class ParsedLink:
    url: str
    form_id: str | None = None
    problem: LinkStatus | None = None   # None means the link is usable
    message: str = ""


@dataclass
class LinkResult:
    url: str
    status: LinkStatus
    message: str = ""
    form_id: str | None = None
    title: str = ""
    row_count: int = 0
    table: Any = None                    # pandas DataFrame when a table was fetched
    questions: list[dict] = field(default_factory=list)  # [{"question_id", "title"}] in form order

    @property
    def has_table(self) -> bool:
        return self.table is not None
