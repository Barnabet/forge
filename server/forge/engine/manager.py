from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

from forge.engine.actor import SessionActor, SessionMeta
from forge.engine.bus import EventBus
from forge.engine.events import RunFinished, SessionCreated
from forge.engine.scheduler import Scheduler
from forge.engine.sysprompt import build_system_prompt
from forge.llm.base import LLMClient
from forge.store.config import ForgeConfig


class SessionManager:
    def __init__(self, home: Path, config: ForgeConfig, llm: LLMClient, bus: EventBus):
        self.home = home
        self.config = config
        self.llm = llm
        self.bus = bus
        self.scheduler = Scheduler(config.max_concurrent)
        self.actors: dict[str, SessionActor] = {}
        self._creation_order: list[str] = []

    def _make_actor(self, meta: SessionMeta) -> SessionActor:
        actor = SessionActor(
            meta=meta, home=self.home, config=self.config, llm=self.llm,
            bus=self.bus, scheduler=self.scheduler,
            system_prompt_fn=lambda m: build_system_prompt(m, self.home))
        self.actors[meta.id] = actor
        self._creation_order.append(meta.id)
        return actor

    def create(self, cwd: str | None = None, model: str | None = None,
               autonomy: str | None = None, project_id: str | None = None,
               effort: str | None = None) -> SessionActor:
        if cwd is None:
            last = self.actors.get(self._creation_order[-1]) if self._creation_order else None
            cwd = last.meta.cwd if last else str(Path.home())
        meta = SessionMeta(
            id=uuid4().hex[:8], cwd=cwd,
            model=model or self.config.default_model,
            autonomy=autonomy or self.config.default_autonomy,
            project_id=project_id, effort=effort or "default")
        actor = self._make_actor(meta)
        actor.emit(SessionCreated(
            session_id=meta.id, ts=time.time(), name=meta.name, cwd=meta.cwd,
            model=meta.model, autonomy=meta.autonomy,
            project_id=meta.project_id, effort=meta.effort))
        return actor

    def get(self, session_id: str) -> SessionActor:
        return self.actors[session_id]

    def list(self) -> list[SessionMeta]:
        return [self.actors[i].meta for i in self._creation_order]

    def recent_cwds(self, limit: int = 10) -> list[str]:
        seen: list[str] = []
        for sid in reversed(self._creation_order):
            cwd = self.actors[sid].meta.cwd
            if cwd not in seen:
                seen.append(cwd)
            if len(seen) >= limit:
                break
        return seen

    def rehydrate(self) -> None:
        sessions_dir = self.home / "sessions"
        if not sessions_dir.is_dir():
            return
        for sdir in sorted(sessions_dir.iterdir()):
            if not (sdir / "events.jsonl").is_file() or sdir.name in self.actors:
                continue
            meta = self._replay_meta(sdir.name)
            if meta is None:
                continue
            actor = self._make_actor(meta)
            evs = actor.log.read()
            last_finished = max(
                (e.seq for e in evs if e.type == "run_finished"), default=0)
            mid_run = any(
                e.seq > last_finished and e.type in
                {"user_message", "assistant_message", "tool_call_started"}
                for e in evs)
            if mid_run:
                actor._close_dangling("Interrupted by server restart")
                actor.emit(RunFinished(session_id=meta.id, ts=time.time(),
                                       reason="interrupted"))

    def _replay_meta(self, session_id: str) -> SessionMeta | None:
        from forge.store.eventlog import EventLog
        log = EventLog(self.home / "sessions" / session_id / "events.jsonl")
        meta: SessionMeta | None = None
        for e in log.read():
            if e.type == "session_created":
                meta = SessionMeta(id=session_id, name=e.name, cwd=e.cwd,
                                   model=e.model, autonomy=e.autonomy,
                                   project_id=e.project_id, effort=e.effort)
            elif meta and e.type == "session_renamed":
                meta.name = e.name
            elif meta and e.type == "autonomy_changed":
                meta.autonomy = e.autonomy
            elif meta and e.type == "model_changed":
                meta.model = e.model
            elif meta and e.type == "effort_changed":
                meta.effort = e.effort
            elif meta and e.type == "session_archived":
                meta.archived = True
            elif meta and e.type == "session_unarchived":
                meta.archived = False
        if meta:
            meta.status = "idle"
        return meta
