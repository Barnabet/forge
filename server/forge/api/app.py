from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import shutil
import signal as signalmod
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import get_args
from urllib.parse import urlparse

from fastapi import (
    FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from forge.api.schemas import (
    CreateProject, CreateSession, FsMove, FsPath, PostMessage, RenameSession,
    ResolveApproval, ResolvePlan, Rewind, SetAutonomy, SetEffort, SetMode,
    SetModel, TerminalResize, TerminalSignal, TerminalWrite, UpdateConfig,
    UpdateProject,
)
from forge.engine.actor import (
    RewindConflict, RewindEmptyReplacement, RewindNoCheckpoint,
    RewindProvenanceUnavailable, RewindTargetInactive, RewindTargetMissing,
    RewindWorkspaceError, TerminalNotFound, TerminalNotRunning,
)
from forge.engine.bus import EventBus
from forge.engine.events import Effort, SessionDeleted
from forge.engine.indexservice import IndexService
from forge.engine.manager import SessionManager
from forge.engine.skills import discover_skills, stock_skills_dir
from forge.llm.base import LLMClient
from forge.store.config import ForgeConfig, save_config
from forge.store.projects import ProjectStore
from forge.store.subagent_grades import (
    ModelLeaderboardEntry, OrchestratorSummary, RecordSummary, SubagentGradeRecord,
    SubagentGradeStore,
)
from forge.tools.search import SKIP_DIRS

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

_EFFORTS = get_args(Effort)
_AUTONOMIES = ("yolo", "guarded")

_TEXT_FILE_CAP = 10 * 1024 * 1024  # 10 MB cap for text/unknown reads
_UPLOAD_CAP = 50 * 1024 * 1024  # 50 MB per-file upload cap

# Terminal input and geometry bounds (V1). Input is capped so a single write
# can't buffer unbounded data into the PTY; dimensions and cursors are clamped
# to sane ranges before reaching the runtime.
_TERMINAL_INPUT_CAP = 64 * 1024  # 64 KiB per write
_TERMINAL_MAX_DIM = 10_000
# Only the signals a UI legitimately needs: interrupt, terminate, hard-kill,
# and window-change. Anything else is rejected.
_TERMINAL_SIGNALS = {
    "INT": signalmod.SIGINT, "TERM": signalmod.SIGTERM,
    "KILL": signalmod.SIGKILL, "WINCH": signalmod.SIGWINCH,
}


def _safe_path(cwd: Path, rel: str) -> Path:
    """Resolve a client-supplied relative path against cwd, rejecting anything
    that escapes cwd (absolute paths, .. traversal, or symlinks pointing out)."""
    if rel.startswith(("/", "~")) or Path(rel).is_absolute():
        raise HTTPException(400, f"invalid path: {rel}")
    candidate = (cwd / rel).resolve()
    root = cwd.resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise HTTPException(400, f"invalid path: {rel}")
    return candidate


def _relativize_contained(cwd: Path, paths: list[str]) -> list[str]:
    """Return de-duplicated paths contained by cwd, relative to cwd."""
    out: list[str] = []
    for path in paths:
        try:
            rel = str(Path(path).resolve().relative_to(cwd))
        except (ValueError, OSError):
            continue
        if rel not in out:
            out.append(rel)
    return out


def _validate_effort(value: str | None) -> None:
    if value is not None and value not in _EFFORTS:
        raise HTTPException(400, f"invalid effort: {value}")


def _validate_autonomy(value: str | None) -> None:
    if value is not None and value not in _AUTONOMIES:
        raise HTTPException(400, f"invalid autonomy: {value}")


def _validate_default(field: str, value: str) -> None:
    if field == "default_effort" and value != "" and value not in _EFFORTS:
        raise HTTPException(400, f"invalid default_effort: {value}")
    if field == "default_autonomy" and value != "" and value not in _AUTONOMIES:
        raise HTTPException(400, f"invalid default_autonomy: {value}")


def create_app(home: Path, config: ForgeConfig, llm: LLMClient) -> FastAPI:
    bus = EventBus()
    manager = SessionManager(home=home, config=config, llm=llm, bus=bus)
    projects = ProjectStore(home)
    # Read-only view of the global append-only grade store. Reads parse the
    # shared JSONL; writes stay owned by the session actors.
    grades = SubagentGradeStore(home)
    index_service = IndexService(
        bus, manager.file_index,
        config.file_search_max_file_bytes, config.file_search_max_files)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.rehydrate()
        # Proactively vectorize each project's workspace in the background so
        # search_files is warm and the sidebar can show progress.
        index_service.index_all(projects.list())
        try:
            yield
        finally:
            # Reap any live PTY terminals so shutdown orphans no child process.
            manager.shutdown()

    app = FastAPI(title="Forge", lifespan=lifespan)
    app.state.manager = manager
    app.state.projects = projects
    app.state.index_service = index_service
    app.state.grades = grades

    def _actor(sid: str):
        try:
            return manager.get(sid)
        except KeyError:
            raise HTTPException(404, f"unknown session: {sid}") from None

    @app.get("/api/health")
    async def health():
        return {"ok": await llm.healthy()}

    @app.get("/api/models")
    async def models():
        return [m.model_dump() for m in config.models]

    @app.get("/api/sessions")
    async def sessions():
        return [m.model_dump() for m in manager.list()]

    @app.post("/api/sessions")
    async def create_session(body: CreateSession):
        project = None
        if body.project_id is not None:
            project = projects.get(body.project_id)
            if project is None:
                raise HTTPException(400, f"unknown project: {body.project_id}")
        effort = body.effort or (project.default_effort if project else None) or None
        autonomy = body.autonomy or (project.default_autonomy if project else None) or None
        # Validate the RESOLVED values (guards both bad request bodies and legacy
        # projects poisoned with invalid defaults) so SessionMeta never 500s.
        _validate_effort(effort)
        _validate_autonomy(autonomy)
        actor = manager.create(
            cwd=body.cwd or (project.cwd if project else None),
            model=body.model or (project.default_model if project else None) or None,
            autonomy=autonomy,
            project_id=body.project_id,
            effort=effort)
        return actor.meta.model_dump()

    @app.post("/api/sessions/{sid}/messages", status_code=202)
    async def post_message(sid: str, body: PostMessage):
        await _actor(sid).post_message(body.text, images=body.images)
        return {}

    @app.post("/api/sessions/{sid}/approvals/{call_id}")
    async def resolve(sid: str, call_id: str, body: ResolveApproval):
        await _actor(sid).resolve_approval(
            call_id, body.decision,
            body.always.model_dump() if body.always else None)
        return {}

    @app.post("/api/sessions/{sid}/cancel")
    async def cancel(sid: str):
        _actor(sid).cancel()
        return {}

    @app.post("/api/sessions/{sid}/autonomy")
    async def set_autonomy(sid: str, body: SetAutonomy):
        _validate_autonomy(body.autonomy)
        _actor(sid).set_autonomy(body.autonomy)
        return {}

    @app.post("/api/sessions/{sid}/model")
    async def set_model(sid: str, body: SetModel):
        if body.model not in {m.id for m in config.models}:
            raise HTTPException(400, f"unknown model: {body.model}")
        _actor(sid).set_model(body.model)
        return {}

    @app.post("/api/sessions/{sid}/effort")
    async def set_effort(sid: str, body: SetEffort):
        if body.effort not in _EFFORTS:
            raise HTTPException(400, f"invalid effort: {body.effort}")
        _actor(sid).set_effort(body.effort)
        return {}

    @app.post("/api/sessions/{sid}/mode")
    async def set_mode(sid: str, body: SetMode):
        if body.mode not in ("act", "plan"):
            raise HTTPException(400, f"invalid mode: {body.mode}")
        _actor(sid).set_mode(body.mode)
        return {}

    @app.post("/api/sessions/{sid}/plan/{call_id}")
    async def resolve_plan(sid: str, call_id: str, body: ResolvePlan):
        if body.decision not in ("approve", "revise"):
            raise HTTPException(400, f"invalid decision: {body.decision}")
        await _actor(sid).resolve_plan(call_id, body.decision, body.feedback)
        return {}

    @app.post("/api/sessions/{sid}/rewind")
    async def rewind(sid: str, body: Rewind):
        actor = _actor(sid)
        # Distinguish rewind-only (both fields omitted) from edit-and-resend:
        # once either field is supplied we pass through so the actor can reject
        # an empty replacement with 400.
        supplied = body.text is not None or body.images is not None
        try:
            if supplied:
                await actor.rewind(body.target_user_seq, text=body.text or "",
                                   images=body.images or [])
            else:
                await actor.rewind(body.target_user_seq)
        except RewindEmptyReplacement as e:
            raise HTTPException(400, str(e)) from None
        except RewindTargetMissing as e:
            raise HTTPException(404, str(e)) from None
        except (RewindTargetInactive, RewindNoCheckpoint, RewindWorkspaceError,
                RewindConflict, RewindProvenanceUnavailable) as e:
            raise HTTPException(409, str(e)) from None
        return {}

    @app.post("/api/sessions/{sid}/read")
    async def mark_read(sid: str):
        _actor(sid).acknowledge()
        return {}

    @app.post("/api/sessions/{sid}/compact")
    async def compact(sid: str):
        if not await _actor(sid).compact_now():
            raise HTTPException(409, "session is running; compact after the run finishes")
        return {}

    @app.post("/api/sessions/{sid}/archive")
    async def archive(sid: str):
        if not _actor(sid).archive():
            raise HTTPException(409, "session is running; cancel before archiving")
        return {}

    @app.post("/api/sessions/{sid}/unarchive")
    async def unarchive(sid: str):
        _actor(sid).unarchive()
        return {}

    @app.delete("/api/sessions/{sid}")
    async def delete_session(sid: str):
        _actor(sid)  # 404 for unknown ids
        if not manager.delete(sid):
            raise HTTPException(409, "session is running; cancel before deleting")
        bus.publish(SessionDeleted(session_id=sid))
        return {}

    @app.patch("/api/sessions/{sid}")
    async def rename(sid: str, body: RenameSession):
        actor = _actor(sid)
        actor.meta.name = body.name
        from forge.engine.events import SessionRenamed
        actor.emit(actor._e(SessionRenamed, name=body.name))
        return {}

    @app.get("/api/sessions/{sid}/events")
    async def events(sid: str, after: int = 0):
        return [e.model_dump(mode="json") for e in _actor(sid).log.read(after)]

    def _terminal_snapshot(t) -> dict:
        return {
            "terminal_id": t.id, "command": list(t.command), "cwd": t.cwd,
            "cols": t.cols, "rows": t.rows, "state": t.state,
            "output_offset": t.buffer.end, "exit_code": t.exit_code,
            "exit_reason": t.exit_reason}

    def _terminal(sid: str, tid: str):
        try:
            return _actor(sid)._get_terminal(tid)
        except TerminalNotFound:
            raise HTTPException(404, f"unknown terminal: {tid}") from None

    @app.get("/api/sessions/{sid}/terminals")
    async def list_terminals(sid: str):
        return [_terminal_snapshot(t) for t in _actor(sid).list_terminals()]

    @app.get("/api/sessions/{sid}/terminals/{tid}")
    async def read_terminal(sid: str, tid: str, after: int = 0):
        actor = _actor(sid)
        _terminal(sid, tid)  # 404 for unknown ids
        after = max(0, after)
        text, start, end, dropped = actor.read_terminal(tid, after)
        return {"terminal_id": tid, "text": text, "start_offset": start,
                "end_offset": end, "dropped": dropped}

    @app.post("/api/sessions/{sid}/terminals/{tid}/input")
    async def terminal_input(sid: str, tid: str, body: TerminalWrite):
        if len(body.data.encode("utf-8")) > _TERMINAL_INPUT_CAP:
            raise HTTPException(413, "terminal input too large")
        _terminal(sid, tid)
        try:
            _actor(sid).write_terminal(tid, body.data)
        except TerminalNotRunning as e:
            raise HTTPException(409, str(e)) from None
        return {}

    @app.post("/api/sessions/{sid}/terminals/{tid}/resize")
    async def terminal_resize(sid: str, tid: str, body: TerminalResize):
        if not (1 <= body.cols <= _TERMINAL_MAX_DIM and 1 <= body.rows <= _TERMINAL_MAX_DIM):
            raise HTTPException(400, "invalid terminal dimensions")
        _terminal(sid, tid)
        try:
            _actor(sid).resize_terminal(tid, body.cols, body.rows)
        except TerminalNotRunning as e:
            raise HTTPException(409, str(e)) from None
        return {}

    @app.post("/api/sessions/{sid}/terminals/{tid}/signal")
    async def terminal_signal(sid: str, tid: str, body: TerminalSignal):
        sig = _TERMINAL_SIGNALS.get(body.signal)
        if sig is None:
            raise HTTPException(400, f"unsupported signal: {body.signal}")
        _terminal(sid, tid)
        try:
            _actor(sid).signal_terminal(tid, sig)
        except TerminalNotRunning as e:
            raise HTTPException(409, str(e)) from None
        return {}

    @app.post("/api/sessions/{sid}/terminals/{tid}/close")
    async def terminal_close(sid: str, tid: str):
        _terminal(sid, tid)
        _actor(sid).close_terminal(tid)
        return {}

    @app.get("/api/sessions/{sid}/workspace/status")
    async def workspace_status(sid: str, limit: int = Query(20, ge=1, le=100)):
        actor = _actor(sid)
        ws = actor.shared_workspace
        cwd = ws.cwd
        async with actor.workspace_lock:
            reconciled = ws.reconcile() is not None
            recent = ws.recent_activity(limit)
            current_tree = ws.current_tree()
        activity = []
        last_external_paths: list[str] = []
        for rec in reversed(recent):
            rels = _relativize_contained(cwd, rec.paths)
            author = (f"session {rec.session_id}" if rec.session_id
                      else rec.origin)
            activity.append({
                "seq": rec.seq, "timestamp": rec.timestamp,
                "session_id": rec.session_id, "author": author,
                "origin": rec.origin, "action": rec.action, "paths": rels,
                "note": rec.note})
            if rec.origin == "external" and not last_external_paths:
                last_external_paths = rels
        return {
            "cwd": str(cwd),
            "sessions": manager.workspace_session_infos(cwd),
            "recent_activity": activity,
            "current_tree": current_tree,
            "reconciled": reconciled,
            "last_external_paths": last_external_paths}

    @app.get("/api/sessions/{sid}/changesets")
    async def changesets(sid: str):
        return [c.model_dump() for c in _actor(sid).changesets.list()]

    @app.post("/api/sessions/{sid}/changesets/{index}/revert")
    async def revert(sid: str, index: int):
        from pathlib import Path as _Path

        from forge.store.changesets import RevertConflict
        actor = _actor(sid)
        async with actor.workspace_lock:
            ws = actor.shared_workspace
            # Fold any out-of-band drift into the log before the revert's
            # conflict check runs, so an external mutation is recorded before a
            # RevertConflict (409) rather than lost.
            if ws is not None:
                ws.reconcile()
            try:
                info = actor.changesets.revert(index)
            except RevertConflict as e:
                raise HTTPException(409, str(e)) from None
            if ws is not None:
                path = _Path(info["path"])
                key = str(ws.canonical(path))
                ws.record_controlled_change(
                    session_id=actor.meta.id, action="revert", paths=[path],
                    origin="revert", before={key: info["before_hash"]})
        return {}

    @app.post("/api/sessions/{sid}/changesets/keep_all")
    async def keep_all(sid: str):
        _actor(sid).changesets.keep_all()
        return {}

    @app.get("/api/sessions/{sid}/changesets/{index}/file")
    async def changeset_file(sid: str, index: int):
        actor = _actor(sid)
        try:
            cs = actor.changesets.get(index)
            return {"path": cs.path, "content": actor.changesets.after_content(index)}
        except (IndexError, FileNotFoundError):
            raise HTTPException(404, f"no changeset {index}") from None

    @app.get("/api/sessions/{sid}/files")
    async def file_search(sid: str, q: str = ""):
        cwd = Path(_actor(sid).meta.cwd)
        return await asyncio.to_thread(_walk_files, cwd, q)

    def _fs_before(actor, *paths: Path) -> dict[str, str | None]:
        """Capture the canonical-keyed on-disk hashes of ``paths`` before an
        fs_api mutation, so the recorded provenance has an accurate before-map.
        Call under the workspace lock. Empty when the session has no workspace."""
        ws = actor.shared_workspace
        if ws is None:
            return {}
        return {str(ws.canonical(p)): ws.current_hash(p) for p in paths}

    def _fs_record(actor, action: str, paths: list[Path],
                   before: dict[str, str | None]) -> None:
        """Record an fs_api mutation's provenance (after-hashes captured from
        disk, baselines refreshed). Call under the workspace lock after the
        mutation completes."""
        ws = actor.shared_workspace
        if ws is None:
            return
        ws.record_controlled_change(
            session_id=actor.meta.id, action=action, paths=paths,
            origin="fs_api", before=before)

    @app.get("/api/sessions/{sid}/fs/list")
    async def fs_list(sid: str, path: str = ""):
        cwd = Path(_actor(sid).meta.cwd)
        target = _safe_path(cwd, path)
        return await asyncio.to_thread(_fs_list, target)

    @app.get("/api/fs/browse")
    async def fs_browse(path: str = ""):
        target = (Path(path).expanduser() if path else Path.home()).resolve()
        if not target.is_dir():
            raise HTTPException(400, f"not a directory: {path or target}")

        def _browse():
            data = _fs_list(target)
            parent = str(target.parent) if target.parent != target else None
            return {"path": str(target), "parent": parent, **data}

        try:
            return await asyncio.to_thread(_browse)
        except PermissionError:
            raise HTTPException(403, "permission denied") from None

    @app.get("/api/fs/pick")
    async def fs_pick(path: str = ""):
        start = (Path(path).expanduser() if path else Path.home())
        try:
            picked = await asyncio.to_thread(_native_pick_folder, start)
        except RuntimeError as exc:
            raise HTTPException(501, str(exc)) from None
        return {"path": picked}

    @app.get("/api/sessions/{sid}/fs/file")
    async def fs_file(sid: str, path: str = ""):
        actor = _actor(sid)
        cwd = Path(actor.meta.cwd)
        target = _safe_path(cwd, path)
        data, media_type = await asyncio.to_thread(_fs_read, target)
        # Register the exact bytes returned as this session's baseline, so a
        # later stale-write guard compares against what the viewer actually saw.
        ws = actor.shared_workspace
        if ws is not None:
            ws.observe_hash(actor.meta.id, target, hashlib.sha256(data).hexdigest())
        return Response(content=data, media_type=media_type)

    @app.post("/api/sessions/{sid}/fs/mkdir")
    async def fs_mkdir(sid: str, body: FsPath):
        actor = _actor(sid)
        target = _safe_path(Path(actor.meta.cwd), body.path)
        async with actor.workspace_lock:
            before = _fs_before(actor, target)
            await asyncio.to_thread(_fs_mkdir, target)
            _fs_record(actor, "mkdir", [target], before)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/fs/touch")
    async def fs_touch(sid: str, body: FsPath):
        actor = _actor(sid)
        target = _safe_path(Path(actor.meta.cwd), body.path)
        async with actor.workspace_lock:
            before = _fs_before(actor, target)
            await asyncio.to_thread(_fs_touch, target)
            _fs_record(actor, "touch", [target], before)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/fs/move")
    async def fs_move(sid: str, body: FsMove):
        actor = _actor(sid)
        cwd = Path(actor.meta.cwd)
        src = _safe_path(cwd, body.src)
        dst = _safe_path(cwd, body.dst)
        async with actor.workspace_lock:
            before = _fs_before(actor, src, dst)
            await asyncio.to_thread(_fs_move, src, dst)
            _fs_record(actor, "move", [src, dst], before)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/fs/delete")
    async def fs_delete(sid: str, body: FsPath):
        actor = _actor(sid)
        cwd = Path(actor.meta.cwd)
        if body.path == "":
            raise HTTPException(400, "cannot delete the root")
        target = _safe_path(cwd, body.path)
        if target == cwd.resolve():
            raise HTTPException(400, "cannot delete the root")
        async with actor.workspace_lock:
            before = _fs_before(actor, target)
            await asyncio.to_thread(_fs_delete, target)
            _fs_record(actor, "delete", [target], before)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/fs/upload")
    async def fs_upload(sid: str, dir: str = Form(""),  # noqa: A002
                        files: list[UploadFile] = File(...)):
        actor = _actor(sid)
        target_dir = _safe_path(Path(actor.meta.cwd), dir)
        payloads = []
        for f in files:
            data = await f.read()
            if len(data) > _UPLOAD_CAP:
                raise HTTPException(413, f"file too large: {f.filename}")
            payloads.append((f.filename, data))
        async with actor.workspace_lock:
            written = [target_dir / Path(name).name for name, _ in payloads]
            before = _fs_before(actor, *written)
            for name, data in payloads:
                await asyncio.to_thread(_fs_write_upload, target_dir, name, data)
            if written:
                _fs_record(actor, "upload", written, before)
        return {"ok": True}

    @app.get("/api/projects")
    async def list_projects():
        return [p.model_dump() for p in projects.list()]

    @app.post("/api/projects")
    async def create_project(body: CreateProject):
        cwd = Path(body.cwd).expanduser()
        if not cwd.is_dir():
            raise HTTPException(400, f"not a directory: {body.cwd}")
        _validate_default("default_effort", body.default_effort)
        _validate_default("default_autonomy", body.default_autonomy)
        p = projects.create(
            name=body.name, cwd=str(cwd), default_model=body.default_model,
            default_autonomy=body.default_autonomy,
            default_effort=body.default_effort)
        index_service.schedule(p.id, p.cwd)
        return p.model_dump()

    @app.patch("/api/projects/{pid}")
    async def update_project(pid: str, body: UpdateProject):
        fields = body.model_dump(exclude_unset=True)
        if "cwd" in fields:
            cwd = Path(fields["cwd"]).expanduser() if fields["cwd"] else None
            if cwd is None or not cwd.is_dir():
                raise HTTPException(400, f"not a directory: {fields['cwd']}")
            fields["cwd"] = str(cwd)
        if "default_effort" in fields and fields["default_effort"] is not None:
            _validate_default("default_effort", fields["default_effort"])
        if "default_autonomy" in fields and fields["default_autonomy"] is not None:
            _validate_default("default_autonomy", fields["default_autonomy"])
        try:
            p = projects.update(pid, fields)
        except ValidationError:
            raise HTTPException(400, f"invalid project fields: {fields}") from None
        if p is None:
            raise HTTPException(404, f"unknown project: {pid}")
        return p.model_dump()

    @app.delete("/api/projects/{pid}")
    async def delete_project(pid: str):
        if not projects.delete(pid):
            raise HTTPException(404, f"unknown project: {pid}")
        return {}

    @app.get("/api/index")
    async def index_status():
        return index_service.status

    @app.get("/api/config")
    async def get_config():
        return config.model_dump()

    @app.patch("/api/config")
    async def update_config(body: UpdateConfig):
        fields = body.model_dump(exclude_unset=True)
        try:
            ForgeConfig.model_validate({**config.model_dump(), **fields})
        except ValidationError:
            raise HTTPException(400, f"invalid config fields: {fields}") from None
        # Mutate the live config in place so SessionManager sees the updates;
        # don't rebind the `config` name.
        for k, v in fields.items():
            setattr(config, k, v)
        save_config(home, config)
        # Re-derive runtime state so the change takes effect without a restart.
        await manager.apply_config()
        return config.model_dump()

    @app.get("/api/recent_dirs")
    async def recent_dirs():
        return manager.recent_cwds()

    @app.get("/api/skills")
    async def skills():
        return [s.model_dump() for s in discover_skills(
            [stock_skills_dir(), home / "skills"])]

    @app.get("/api/subagents/leaderboard", response_model=list[ModelLeaderboardEntry])
    async def subagent_leaderboard(orchestrator_model: str | None = None):
        return grades.leaderboard(orchestrator_model=orchestrator_model)

    @app.get("/api/subagents/orchestrators", response_model=list[OrchestratorSummary])
    async def subagent_orchestrators():
        return grades.orchestrators()

    @app.get("/api/subagents/evaluations", response_model=list[RecordSummary])
    async def subagent_evaluations(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        orchestrator_model: str | None = None,
    ):
        return grades.list(
            limit=limit, offset=offset, orchestrator_model=orchestrator_model)

    @app.get("/api/subagents/evaluations/{record_id}",
             response_model=SubagentGradeRecord)
    async def subagent_evaluation(record_id: str):
        record = grades.get(record_id)
        if record is None:
            raise HTTPException(404, f"unknown evaluation: {record_id}")
        return record

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        # Browsers don't apply CORS to WebSockets: reject cross-origin handshakes
        # so a random web page can't open ws://127.0.0.1 and read every session.
        origin = websocket.headers.get("origin")
        if origin is not None and urlparse(origin).hostname not in (
                "localhost", "127.0.0.1"):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        raw = await websocket.receive_text()
        try:
            cursors_raw = json.loads(raw).get("cursors", {})
            cursors: dict[str, int] = {
                str(k): int(v) for k, v in cursors_raw.items()}
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            await websocket.close(code=4400)
            return
        q = bus.subscribe()
        try:
            for sid, after in cursors.items():
                if sid in manager.metas:
                    for e in manager.get(sid).log.read(after):
                        await websocket.send_text(json.dumps(e.model_dump(mode="json")))
            while True:
                event = await q.get()
                await websocket.send_text(json.dumps(event.model_dump(mode="json")))
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(q)

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


def _subseq(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(ch in it for ch in needle)


def _walk_files(cwd: Path, q: str) -> list[str]:
    """Blocking file walk (run in a thread). Prunes skipped/hidden dirs in place
    so we never descend .git/node_modules/.venv, and caps candidate collection."""
    needle = q.lower()
    candidates: list[str] = []
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.startswith(".") or name in SKIP_DIRS:
                continue
            rel = os.path.relpath(os.path.join(root, name), cwd)
            if _subseq(needle, rel.lower()):
                candidates.append(rel)
        if len(candidates) >= 5000:
            break
    return sorted(candidates, key=len)[:50]


def _native_pick_folder(start: Path) -> str | None:
    """Open the OS's native folder-chooser. Returns the chosen path, or None
    if the user cancelled. Raises RuntimeError when no picker is available."""
    if sys.platform == "darwin":
        default = str(start) if start.is_dir() else str(Path.home())
        script = (
            'set startFolder to POSIX file "%s"\n'
            "try\n"
            "  set chosen to choose folder with prompt "
            '"Select a folder" default location startFolder\n'
            "  return POSIX path of chosen\n"
            "on error number -128\n"
            '  return ""\n'
            "end try" % default.replace('"', '\\"')
        )
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=300,
        )
        out = proc.stdout.strip()
        return out or None
    raise RuntimeError("native folder picker unavailable on this platform")


def _fs_list(target: Path) -> dict:
    if not target.exists():
        raise HTTPException(404, "not found")
    if not target.is_dir():
        raise HTTPException(400, "not a directory")
    dirs, files = [], []
    for entry in target.iterdir():
        try:
            st = entry.stat()
        except OSError:
            continue
        item = {"name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": st.st_size, "mtime": st.st_mtime}
        (dirs if entry.is_dir() else files).append(item)
    dirs.sort(key=lambda e: e["name"].lower())
    files.sort(key=lambda e: e["name"].lower())
    return {"entries": dirs + files}


def _fs_read(target: Path) -> tuple[bytes, str]:
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    size = target.stat().st_size
    capped = media_type == "application/octet-stream" or media_type.startswith("text/")
    if capped and size > _TEXT_FILE_CAP:
        raise HTTPException(413, "file too large")
    return target.read_bytes(), media_type


def _fs_mkdir(target: Path) -> None:
    if target.is_file():
        raise HTTPException(409, "a file exists at that path")
    target.mkdir(parents=True, exist_ok=True)


def _fs_touch(target: Path) -> None:
    if target.exists():
        raise HTTPException(409, "already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


def _fs_move(src: Path, dst: Path) -> None:
    if not src.exists():
        raise HTTPException(404, "source not found")
    if dst.exists():
        raise HTTPException(409, "destination exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _fs_delete(target: Path) -> None:
    if not target.exists():
        raise HTTPException(404, "not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def _fs_write_upload(target_dir: Path, name: str, data: bytes) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / Path(name).name).write_bytes(data)


def main() -> None:
    import uvicorn

    from forge.llm.openai_client import OpenAILLM
    from forge.store.config import load_config

    home = Path(os.environ.get("FORGE_HOME", Path.home() / ".forge"))
    home.mkdir(parents=True, exist_ok=True)
    config = load_config(home)
    llm = OpenAILLM(config.base_url, config.api_key)
    uvicorn.run(create_app(home, config, llm), host="127.0.0.1", port=8700)


if __name__ == "__main__":
    main()
