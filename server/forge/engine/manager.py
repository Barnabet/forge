from __future__ import annotations

import logging
import shutil
import time
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4

from forge.engine.actor import SessionActor, SessionMeta
from forge.engine.bus import EventBus
from forge.engine.events import RunFinished, SessionCreated
from forge.engine.projection import active_events, latest_run, unread_run_seq
from forge.engine.fileindex import FileIndex
from forge.engine.memindex import MemoryIndex
from forge.engine.memory import MemoryAgent
from forge.engine.scheduler import Scheduler
from forge.engine.sysprompt import (
    WorkspaceChange, WorkspacePeer, WorkspaceSummary, build_system_prompt,
)
from forge.engine.workspace import WorkspaceRegistry
from forge.llm.base import LLMClient
from forge.llm.embeddings import embedder_from_config
from forge.store.config import ForgeConfig


def _relativize_contained(cwd: Path, paths: list[str]) -> list[str]:
    """Relative-to-cwd path strings for those in ``paths`` contained under
    ``cwd``; paths outside the tree are dropped (never leak absolute internal
    paths). De-duplicates while preserving order."""
    out: list[str] = []
    for p in paths:
        try:
            rel = str(Path(p).resolve().relative_to(cwd))
        except (ValueError, OSError):
            continue
        if rel not in out:
            out.append(rel)
    return out


class SessionManager:
    def __init__(self, home: Path, config: ForgeConfig, llm: LLMClient, bus: EventBus):
        self.home = home
        self.config = config
        self.llm = llm
        self.bus = bus
        self.scheduler = Scheduler(config.max_concurrent)
        embedder = embedder_from_config(
            config.openrouter_api_key, config.embedding_model)
        self.memory_index = MemoryIndex(
            home, embedder, config.memory_similarity_threshold
        ) if embedder else None
        self.file_index = FileIndex(
            home, embedder, config.memory_similarity_threshold
        ) if embedder else None
        self.memory_agent = MemoryAgent(home, llm, index=self.memory_index)
        # One registry per home: every session whose cwd resolves to the same
        # real directory shares that directory's SharedWorkspace (and its lock).
        self.workspaces = WorkspaceRegistry(home)
        # `metas` is the authoritative, always-resident registry of every known
        # session (cheap: just SessionMeta). `actors` is a bounded LRU cache of
        # live SessionActors — each holds a full in-memory event log, so we cap
        # how many stay resident and fault the rest back in on demand.
        self.metas: dict[str, SessionMeta] = {}
        self._creation_order: list[str] = []
        self.actors: OrderedDict[str, SessionActor] = OrderedDict()
        self.max_resident = max(1, config.max_resident_sessions)

    async def apply_config(self) -> None:
        """Re-derive runtime state from the (already-mutated) live config so a
        PATCH /api/config takes effect without a server restart.

        Fields read live per operation (default_model/autonomy, memory_model,
        compaction_model, subagent_model) need nothing here. This handles the
        rest: the scheduler cap, the LRU bound, the LLM credentials, the memory
        embedder/index, and the tool set / subagent limits captured inside each
        resident actor at build time."""
        cfg = self.config
        await self.scheduler.set_max_concurrent(cfg.max_concurrent)
        self.max_resident = max(1, cfg.max_resident_sessions)
        reconfigure = getattr(self.llm, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(cfg.base_url, cfg.api_key)
        embedder = embedder_from_config(cfg.openrouter_api_key, cfg.embedding_model)
        self.memory_index = MemoryIndex(
            self.home, embedder, cfg.memory_similarity_threshold
        ) if embedder else None
        self.memory_agent.index = self.memory_index
        # Idle actors captured config at build time (web/image tools, api keys,
        # subagent limits, memory index). Drop them so they fault back in fresh
        # on next access; busy actors keep their tools until their run ends.
        for sid in list(self.actors):
            if not self.actors[sid].is_busy():
                del self.actors[sid]

    def _build_actor(self, meta: SessionMeta) -> SessionActor:
        return SessionActor(
            meta=meta, home=self.home, config=self.config, llm=self.llm,
            bus=self.bus, scheduler=self.scheduler,
            system_prompt_fn=lambda m: build_system_prompt(
                m, self.home, memory_search=self.memory_index is not None,
                workspace=self._workspace_summary(m)),
            memory_agent=self.memory_agent, memory_index=self.memory_index,
            file_index=self.file_index,
            shared_workspace=self.workspaces.get(meta.cwd))

    def workspace_peers(self, cwd: str | Path, *, exclude: str | None = None,
                        include_archived: bool = True) -> list[SessionMeta]:
        """Session metas whose cwd resolves (canonical Path.resolve) to the same
        real directory as ``cwd``. Includes the current session unless its id is
        passed as ``exclude``. Reads only resident meta state — never faults an
        idle actor in merely to report status. When ``include_archived`` is
        False, archived sessions are dropped."""
        try:
            target = str(Path(cwd).resolve())
        except OSError:
            target = str(Path(cwd))
        out: list[SessionMeta] = []
        for sid in self._creation_order:
            meta = self.metas[sid]
            if meta.id == exclude:
                continue
            if not include_archived and meta.archived:
                continue
            try:
                resolved = str(Path(meta.cwd).resolve())
            except OSError:
                resolved = str(Path(meta.cwd))
            if resolved == target:
                out.append(meta)
        return out

    def workspace_session_infos(self, cwd: str | Path) -> list[dict]:
        """Compact status rows for every session sharing ``cwd`` (including the
        current one), for the REST status endpoint. ``busy`` is reported only
        when an actor is resident (we never fault one in for status)."""
        rows: list[dict] = []
        for meta in self.workspace_peers(cwd):
            actor = self.actors.get(meta.id)
            rows.append({
                "id": meta.id, "name": meta.name, "status": meta.status,
                "mode": meta.mode, "archived": meta.archived,
                "last_message_at": meta.last_message_at,
                "busy": actor.is_busy() if actor is not None else None})
        return rows

    def _workspace_summary(self, meta: SessionMeta) -> WorkspaceSummary:
        """Compute the compact prompt summary for ``meta``: peer sessions on the
        same live tree and the most recent foreign/external changes. Never
        acquires the async workspace lock or reconciles — it only reads resident
        meta state and the durable activity log."""
        peers = [
            WorkspacePeer(id=m.id, status=str(m.status), mode=str(m.mode))
            for m in self.workspace_peers(
                meta.cwd, exclude=meta.id, include_archived=False)]
        ws = self.workspaces.get(meta.cwd)
        changes: list[WorkspaceChange] = []
        for rec in reversed(ws.recent_activity(50)):
            foreign = (rec.origin == "external"
                       or (rec.session_id is not None
                           and rec.session_id != meta.id))
            if not foreign:
                continue
            rels = _relativize_contained(ws.cwd, rec.paths)
            author = (f"session {rec.session_id}" if rec.session_id
                      else rec.origin)
            changes.append(WorkspaceChange(
                author=author, action=rec.action, paths=rels))
            if len(changes) >= 5:
                break
        return WorkspaceSummary(peers=peers, recent_changes=changes)

    def _register(self, meta: SessionMeta) -> None:
        if meta.id not in self.metas:
            self._creation_order.append(meta.id)
        self.metas[meta.id] = meta

    def _resident(self, meta: SessionMeta) -> SessionActor:
        """Return the live actor for a known session, faulting it in and
        evicting idle actors past the LRU cap. Marks it most-recently-used."""
        actor = self.actors.get(meta.id)
        if actor is None:
            actor = self._build_actor(meta)
            self.actors[meta.id] = actor
        self.actors.move_to_end(meta.id)
        self._evict()
        return actor

    def _evict(self) -> None:
        """Drop least-recently-used idle actors until within the cap. Busy
        actors (live run/memory tasks) are pinned so we never sever a run, and
        the most-recently-used actor (the one just faulted in) is never dropped.
        The cap may be exceeded when too many actors are pinned."""
        mru = next(reversed(self.actors), None)
        for sid in list(self.actors):
            if len(self.actors) <= self.max_resident:
                break
            if sid == mru or self.actors[sid].is_busy():
                continue
            del self.actors[sid]

    def _make_actor(self, meta: SessionMeta) -> SessionActor:
        self._register(meta)
        return self._resident(meta)

    def create(self, cwd: str | None = None, model: str | None = None,
               autonomy: str | None = None, project_id: str | None = None,
               effort: str | None = None) -> SessionActor:
        if cwd is None:
            last = self.metas.get(self._creation_order[-1]) if self._creation_order else None
            cwd = last.cwd if last else str(Path.home())
        sid = uuid4().hex[:8]
        meta = SessionMeta(
            id=sid, cwd=cwd,
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
        return self._resident(self.metas[session_id])

    def delete(self, session_id: str) -> bool:
        actor = self.actors.get(session_id)
        # A live run/memory/compaction task blocks deletion, but live terminals
        # do not: we reap their process groups as part of teardown.
        if actor is not None and actor._has_active_task():
            return False
        if actor is not None:
            actor.teardown()
        self.metas.pop(session_id, None)
        self._creation_order.remove(session_id)
        self.actors.pop(session_id, None)
        shutil.rmtree(self.home / "sessions" / session_id, ignore_errors=True)
        return True

    def shutdown(self) -> None:
        """Reap every resident actor's terminals so server shutdown orphans no
        PTY child. Idempotent."""
        for actor in self.actors.values():
            actor.teardown()

    def list(self) -> list[SessionMeta]:
        return [self.metas[i] for i in self._creation_order]

    def recent_cwds(self, limit: int = 10) -> list[str]:
        seen: list[str] = []
        for sid in reversed(self._creation_order):
            meta = self.metas[sid]
            cwd = meta.cwd
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
            if not (sdir / "events.jsonl").is_file() or sdir.name in self.metas:
                continue
            try:
                meta = self._replay_meta(sdir.name)
            except Exception:
                logging.getLogger(__name__).exception(
                    "skipping session %s: corrupt event log", sdir.name)
                continue
            if meta is None:
                continue
            self._register(meta)
            # Crash recovery must run for every session, but we don't retain the
            # actor: build it transiently, reconcile, then let it fall out of the
            # LRU cache. `_resident` bounds how many stay resident.
            actor = self._resident(meta)
            # Close the crash window between workspace restore and event
            # persistence before we decide whether a run was interrupted.
            actor.recover_rewind()
            # No real PTY survives a restart: mark any still-running terminal
            # in the replay as orphaned so its record reaches a coherent state.
            actor.reconcile_terminals()
            evs = active_events(actor.log.read())
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
        events = log.read()
        meta: SessionMeta | None = None
        for e in events:
            if e.type == "session_created":
                meta = SessionMeta(
                    id=session_id, name=e.name, cwd=e.cwd,
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
            elif meta and e.type == "mode_changed":
                meta.mode = e.mode
            elif meta and e.type == "session_archived":
                meta.archived = True
            elif meta and e.type == "session_unarchived":
                meta.archived = False
        if meta:
            active = active_events(events)
            last_message = next(
                (e for e in reversed(active)
                 if e.type in ("user_message", "assistant_message")), None)
            meta.last_message_at = last_message.ts if last_message else None
            # Restore the session pill state from the active branch so unread
            # completions survive a restart. The interrupted-run patch below
            # (rehydrate) re-emits run_finished for mid-run sessions, which
            # refreshes this again through actor.emit.
            latest = latest_run(events)
            meta.last_run_reason = latest[1] if latest else None
            meta.last_run_seq = latest[0] if latest else None
            meta.unread = unread_run_seq(events) is not None
            meta.status = "idle"
        return meta
