from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class RevertConflict(Exception):
    """Raised when a changeset revert would overwrite content that changed on
    disk since the changeset was recorded. Disk and status are left untouched."""


class Changeset(BaseModel):
    index: int
    path: str  # absolute target path
    added: int
    removed: int
    diff: str
    status: Literal["pending", "kept", "reverted"] = "pending"
    # Provenance for the change (backward-compatible: absent in legacy records).
    session_id: str | None = None
    call_id: str | None = None
    before_hash: str | None = None
    after_hash: str | None = None


class ChangesetStore:
    def __init__(self, dir: Path):
        self.dir = dir
        self.blobs = dir / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self._file = dir / "changesets.jsonl"
        self._sets: list[Changeset] = []
        if self._file.exists():
            self._sets = [Changeset.model_validate(json.loads(line))
                          for line in self._file.read_text().splitlines() if line.strip()]

    def _save(self) -> None:
        self._file.write_text(
            "".join(json.dumps(c.model_dump()) + "\n" for c in self._sets))

    def record(self, path: Path, before: str | None, after: str, *,
               session_id: str | None = None, call_id: str | None = None,
               before_hash: str | None = None,
               after_hash: str | None = None) -> Changeset:
        index = len(self._sets)
        b_lines = (before or "").splitlines(keepends=True)
        a_lines = after.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(
            b_lines, a_lines, fromfile=f"a/{path.name}", tofile=f"b/{path.name}"))
        added = sum(1 for line in diff.splitlines()
                    if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff.splitlines()
                      if line.startswith("-") and not line.startswith("---"))
        if before is not None:
            (self.blobs / f"{index}.before").write_text(before)
        (self.blobs / f"{index}.after").write_text(after)
        cs = Changeset(index=index, path=str(path), added=added, removed=removed,
                       diff=diff, session_id=session_id, call_id=call_id,
                       before_hash=before_hash, after_hash=after_hash)
        self._sets.append(cs)
        self._save()
        return cs

    def list(self) -> list[Changeset]:
        return list(self._sets)

    def get(self, index: int) -> Changeset:
        return self._sets[index]

    def after_content(self, index: int) -> str:
        return (self.blobs / f"{index}.after").read_text()

    def revert(self, index: int) -> dict:
        """Restore the recorded before-content, refusing to clobber content that
        changed since this changeset was applied. Raises RevertConflict (leaving
        disk and status untouched) when the file no longer matches the recorded
        after-content. Returns provenance for the successful revert."""
        cs = self._sets[index]
        before = self.blobs / f"{index}.before"
        target = Path(cs.path)
        # Expected current content = what this changeset wrote. Prefer the
        # recorded after_hash; legacy records without one fall back to hashing
        # the after-blob's bytes.
        expected = cs.after_hash
        if expected is None:
            expected = hashlib.sha256(
                (self.blobs / f"{index}.after").read_bytes()).hexdigest()
        actual = self._hash_file(target)
        if actual != expected:
            raise RevertConflict(
                f"{cs.path} changed since the change was applied; "
                "revert refused to avoid overwriting newer content")
        if before.exists():
            restored = before.read_text()
            target.write_text(restored)
            new_hash: str | None = hashlib.sha256(
                restored.encode("utf-8")).hexdigest()
        elif target.exists():
            target.unlink()
            new_hash = None
        else:
            new_hash = None
        cs.status = "reverted"
        self._save()
        return {"path": cs.path, "before_hash": expected, "after_hash": new_hash,
                "session_id": cs.session_id, "call_id": cs.call_id}

    @staticmethod
    def _hash_file(target: Path) -> str | None:
        try:
            return hashlib.sha256(target.read_bytes()).hexdigest()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError,
                PermissionError, OSError):
            return None

    def keep_all(self) -> None:
        for cs in self._sets:
            if cs.status == "pending":
                cs.status = "kept"
        self._save()
