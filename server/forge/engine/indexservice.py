from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from forge.engine.bus import EventBus
from forge.engine.events import FileIndexProgress
from forge.engine.fileindex import FileIndex

log = logging.getLogger(__name__)


class IndexService:
    """Orchestrates background workspace vectorization per project. Keeps an
    in-memory status map (served over REST for reload survival) and publishes
    FileIndexProgress events so clients can render live progress."""

    def __init__(self, bus: EventBus, file_index: FileIndex | None,
                 max_bytes: int, max_files: int):
        self.bus = bus
        self.file_index = file_index
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.status: dict[str, dict] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def _publish(self, project_id: str, state: str, done: int, total: int) -> None:
        self.status[project_id] = {"state": state, "done": done, "total": total}
        self.bus.publish(FileIndexProgress(
            project_id=project_id, state=state, done=done, total=total))

    async def index_project(self, project_id: str, cwd: str) -> None:
        if self.file_index is None:
            return
        self._publish(project_id, "indexing", 0, 0)
        try:
            await self.file_index.reindex(
                Path(cwd), project_id,
                progress=lambda d, t: self._publish(project_id, "indexing", d, t),
                max_bytes=self.max_bytes, max_files=self.max_files)
        except Exception:
            log.exception("indexing failed for project %s", project_id)
            cur = self.status.get(project_id, {})
            self._publish(project_id, "error", cur.get("done", 0),
                          cur.get("total", 0))
            return
        cur = self.status.get(project_id, {})
        total = cur.get("total", 0)
        self._publish(project_id, "ready", total, total)

    def schedule(self, project_id: str, cwd: str) -> None:
        """Start a background index for a project, deduping live runs."""
        if self.file_index is None:
            return
        existing = self._tasks.get(project_id)
        if existing is not None and not existing.done():
            return

        async def _run() -> None:
            try:
                await self.index_project(project_id, cwd)
            finally:
                self._tasks.pop(project_id, None)

        self._tasks[project_id] = asyncio.create_task(_run())

    def index_all(self, projects) -> None:
        for p in projects:
            self.schedule(p.id, p.cwd)
