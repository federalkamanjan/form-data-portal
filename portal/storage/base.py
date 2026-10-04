"""What the rest of the portal needs from a storage backend. CSV files are the first implementation;
a database or Google Sheet backend can be added later by implementing the same methods."""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

LAYERS = ("raw", "mapped", "cleaned", "rejected", "duplicates")


class StorageBusyError(RuntimeError):
    """Another save is in progress."""


@dataclass
class CommitInfo:
    path: Path
    rows: int
    backup: Path | None


class StorageAdapter(ABC):
    @abstractmethod
    def save_table(self, layer: str, name: str, df: pd.DataFrame) -> Path: ...

    @abstractmethod
    def load_table(self, layer: str, name: str) -> pd.DataFrame | None: ...

    @abstractmethod
    def list_tables(self, layer: str) -> list[str]: ...

    @abstractmethod
    def load_master(self) -> pd.DataFrame | None: ...

    @abstractmethod
    def commit_master(self, df: pd.DataFrame) -> CommitInfo:
        """Replace the master: back up the old one, write the new one atomically. Call inside write_lock()."""

    @abstractmethod
    def write_lock(self) -> AbstractContextManager:
        """Only one writer at a time. Raises StorageBusyError if another save is running."""
