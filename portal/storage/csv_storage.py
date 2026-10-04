"""CSV-file storage: data/<layer>/<name>.csv, plus the master file and its backups."""
from __future__ import annotations

import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .base import LAYERS, CommitInfo, StorageAdapter, StorageBusyError


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    """Write to a temporary file, flush it to disk, then swap it in, so a crash never leaves a half-written CSV.
    UTF-8 with BOM so Excel shows Urdu and accented text correctly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        df.to_csv(fh, index=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class FileLock:
    """A lock file created exclusively. A lock older than `stale_after` seconds is assumed to be left by a crash."""

    def __init__(self, path: Path, stale_after: float = 600):
        self.path, self.stale_after = Path(path), stale_after

    def _try_create(self) -> bool:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}")
        return True

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._try_create():
            return self
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            age = 0
        if age > self.stale_after:
            self.path.unlink(missing_ok=True)
            if self._try_create():
                return self
        raise StorageBusyError("Another save is in progress. Please try again in a moment.")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
        return False


class CsvStorage(StorageAdapter):
    def __init__(self, root: str | Path, master_file_name: str = "master.csv", backups_to_keep: int = 5,
                 lock_stale_after: float = 600):
        self.root = Path(root)
        self.master_path = self.root / master_file_name
        self.backup_dir = self.root / "backups"
        self.backups_to_keep = backups_to_keep
        self._lock = FileLock(self.root / ".write.lock", lock_stale_after)

    @classmethod
    def from_settings(cls, root: str | Path, settings: dict, **kwargs) -> "CsvStorage":
        out = settings["output"]
        return cls(root, out.get("master_file_name", "master.csv"), out.get("backups_to_keep", 5), **kwargs)

    # ---- layers
    def _layer_path(self, layer: str, name: str) -> Path:
        if layer not in LAYERS:
            raise ValueError(f"Unknown layer '{layer}'.")
        safe = "".join(c for c in name if c.isalnum() or c in "-_.") or "table"
        return self.root / layer / f"{safe}.csv"

    def save_table(self, layer: str, name: str, df: pd.DataFrame) -> Path:
        path = self._layer_path(layer, name)
        atomic_write_csv(df, path)
        return path

    def load_table(self, layer: str, name: str) -> pd.DataFrame | None:
        path = self._layer_path(layer, name)
        return self._read(path)

    def list_tables(self, layer: str) -> list[str]:
        folder = self.root / layer
        return sorted(p.stem for p in folder.glob("*.csv")) if folder.exists() else []

    @staticmethod
    def _read(path: Path) -> pd.DataFrame | None:
        if not path.exists():
            return None
        return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")

    # ---- master
    def load_master(self) -> pd.DataFrame | None:
        return self._read(self.master_path)

    def write_lock(self):
        return self._lock

    def _backup_master(self) -> Path | None:
        if not self.master_path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        backup = self.backup_dir / f"{self.master_path.stem}_{stamp}{self.master_path.suffix}"
        shutil.copy2(self.master_path, backup)
        pattern = f"{self.master_path.stem}_*{self.master_path.suffix}"
        old = sorted(self.backup_dir.glob(pattern))
        for stale in old[: max(0, len(old) - self.backups_to_keep)]:
            stale.unlink(missing_ok=True)
        return backup

    def commit_master(self, df: pd.DataFrame) -> CommitInfo:
        """Back up the current master, then replace it. Call this inside `with storage.write_lock():`."""
        backup = self._backup_master()
        atomic_write_csv(df, self.master_path)
        return CommitInfo(path=self.master_path, rows=len(df), backup=backup)
