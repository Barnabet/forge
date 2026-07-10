from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel


class Project(BaseModel):
    id: str
    name: str
    cwd: str
    default_model: str = ""     # "" = unset → config default at session create
    default_autonomy: str = ""
    default_effort: str = ""


class ProjectStore:
    def __init__(self, home: Path):
        self._path = home / "projects.json"
        self._projects: list[Project] = []
        if self._path.exists():
            self._projects = [Project.model_validate(p)
                              for p in json.loads(self._path.read_text())]

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([p.model_dump() for p in self._projects], indent=2))
        os.replace(tmp, self._path)  # atomic on POSIX

    def list(self) -> list[Project]:
        return list(self._projects)

    def get(self, pid: str) -> Project | None:
        return next((p for p in self._projects if p.id == pid), None)

    def create(self, name: str, cwd: str, default_model: str = "",
               default_autonomy: str = "", default_effort: str = "") -> Project:
        p = Project(id=uuid4().hex[:8], name=name, cwd=cwd,
                    default_model=default_model, default_autonomy=default_autonomy,
                    default_effort=default_effort)
        self._projects.append(p)
        self._save()
        return p

    def update(self, pid: str, fields: dict) -> Project | None:
        p = self.get(pid)
        if p is None:
            return None
        # Re-validate the merged record (model_copy(update=...) would not),
        # so an invalid value can never be persisted and brick the next load.
        updated = Project.model_validate({**p.model_dump(), **fields})
        self._projects[self._projects.index(p)] = updated
        self._save()
        return updated

    def delete(self, pid: str) -> bool:
        p = self.get(pid)
        if p is None:
            return False
        self._projects.remove(p)
        self._save()
        return True
