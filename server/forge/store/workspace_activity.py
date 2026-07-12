from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

# Origin of a recorded activity. `tool`/`fs_api`/`bash`/`terminal`/`subagent`
# are Forge-controlled mutations; `external` is a change observed to have
# happened outside Forge's control; `checkpoint`/`rewind`/`revert` are provenance
# markers for workspace snapshot machinery.
ActivityOrigin = Literal[
    "tool", "fs_api", "bash", "terminal", "subagent", "external", "checkpoint",
    "rewind", "revert",
]


def workspace_hash(resolved_cwd: Path) -> str:
    """Stable, filesystem-safe hash of a resolved cwd. Two aliases that resolve
    to the same real directory produce the same hash."""
    raw = str(resolved_cwd).encode("utf-8", "surrogatepass")
    return hashlib.sha256(raw).hexdigest()[:16]


class WorkspaceActivity(BaseModel):
    """One durable append-only provenance record for a workspace (a real cwd).

    Records both Forge-controlled mutations and observed external changes so a
    later task can reason about stale writes and rewind safety. Records are
    self-describing: everything needed to audit the change is captured inline.
    """

    seq: int  # monotonic per-workspace sequence, assigned on append
    timestamp: float = Field(default_factory=time.time)
    cwd: str  # resolved workspace directory this record belongs to
    session_id: str | None = None
    origin: ActivityOrigin
    action: str  # short verb/description, e.g. "write_file", "revert"
    paths: list[str] = Field(default_factory=list)
    call_id: str | None = None
    # Content hashes keyed by path, captured before/after the change. A value of
    # None means the path was missing (did not exist) at that point.
    before: dict[str, str | None] = Field(default_factory=dict)
    after: dict[str, str | None] = Field(default_factory=dict)
    note: str | None = None


class WorkspaceActivityStore:
    """Append-only JSONL provenance log for one resolved workspace directory.

    Persisted under ``FORGE_HOME/workspaces/<hash of resolved cwd>/activity.jsonl``.
    Appends are durable (flush + fsync) and guarded by a thread lock so
    concurrent in-process appends never interleave or race the seq counter.
    Malformed or missing logs load cleanly: a corrupt line is skipped and an
    absent file reads as empty.
    """

    def __init__(self, home: Path, resolved_cwd: Path):
        self._cwd = str(resolved_cwd)
        self._dir = home / "workspaces" / workspace_hash(resolved_cwd)
        self._path = self._dir / "activity.jsonl"
        self._lock = threading.Lock()
        # Seed the seq counter from any existing log so restarts keep monotonic.
        self._seq = self._last_seq_on_disk()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def dir(self) -> Path:
        """The per-workspace data directory (``FORGE_HOME/workspaces/<hash>``).
        Siblings of the activity log — the shadow tree tracker and cursor file —
        live here so everything about one workspace is colocated."""
        return self._dir

    def last_seq(self) -> int:
        """The highest seq appended so far (0 when empty)."""
        return self._seq

    def _last_seq_on_disk(self) -> int:
        records = self.read()
        return records[-1].seq if records else 0

    def append(self, *, origin: ActivityOrigin, action: str,
               paths: list[str] | None = None, session_id: str | None = None,
               call_id: str | None = None,
               before: dict[str, str | None] | None = None,
               after: dict[str, str | None] | None = None,
               note: str | None = None) -> WorkspaceActivity:
        """Durably append one record and return it (with its assigned seq)."""
        with self._lock:
            self._seq += 1
            record = WorkspaceActivity(
                seq=self._seq, cwd=self._cwd, origin=origin, action=action,
                paths=list(paths or []), session_id=session_id, call_id=call_id,
                before=dict(before or {}), after=dict(after or {}), note=note)
            self._dir.mkdir(parents=True, exist_ok=True)
            # On the very first append the log file (and possibly its parent
            # directories) are newly created. fsyncing the file's bytes is not
            # enough to make the new directory entry durable across a crash, so
            # also fsync the containing directory when we create the file.
            is_new_file = not self._path.exists()
            line = json.dumps(record.model_dump(mode="json")) + "\n"
            with self._path.open("a") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            if is_new_file:
                self._fsync_dir(self._dir)
            return record

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        """fsync a directory so a newly created entry within it is durable.
        No-op on platforms that cannot open a directory for fsync."""
        try:
            fd = os.open(str(directory), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def read(self, after_seq: int = 0) -> list[WorkspaceActivity]:
        """All records with seq > after_seq, in append order. Missing log → []."""
        if not self._path.exists():
            return []
        records: list[WorkspaceActivity] = []
        for line in self._path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = WorkspaceActivity.model_validate_json(line)
            except (json.JSONDecodeError, ValidationError):
                continue  # tolerate malformed/legacy lines
            if rec.seq > after_seq:
                records.append(rec)
        return records

    def recent(self, limit: int = 50) -> list[WorkspaceActivity]:
        """The most recent ``limit`` records, oldest-first within the window."""
        records = self.read()
        return records[-limit:] if limit is not None else records
