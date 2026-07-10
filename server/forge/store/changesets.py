from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class Changeset(BaseModel):
    index: int
    path: str  # absolute target path
    added: int
    removed: int
    diff: str
    status: Literal["pending", "kept", "reverted"] = "pending"


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

    def record(self, path: Path, before: str | None, after: str) -> Changeset:
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
        cs = Changeset(index=index, path=str(path), added=added, removed=removed, diff=diff)
        self._sets.append(cs)
        self._save()
        return cs

    def list(self) -> list[Changeset]:
        return list(self._sets)

    def get(self, index: int) -> Changeset:
        return self._sets[index]

    def after_content(self, index: int) -> str:
        return (self.blobs / f"{index}.after").read_text()

    def revert(self, index: int) -> None:
        cs = self._sets[index]
        before = self.blobs / f"{index}.before"
        target = Path(cs.path)
        if before.exists():
            target.write_text(before.read_text())
        elif target.exists():
            target.unlink()
        cs.status = "reverted"
        self._save()

    def keep_all(self) -> None:
        for cs in self._sets:
            if cs.status == "pending":
                cs.status = "kept"
        self._save()
