from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from forge.api.schemas import (
    CreateProject, CreateSession, PostMessage, RenameSession, ResolveApproval,
    SetAutonomy, SetModel, UpdateProject,
)
from forge.engine.bus import EventBus
from forge.engine.manager import SessionManager
from forge.engine.skills import discover_skills
from forge.llm.base import LLMClient
from forge.store.config import ForgeConfig
from forge.store.projects import ProjectStore
from forge.tools.search import SKIP_DIRS

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


def create_app(home: Path, config: ForgeConfig, llm: LLMClient) -> FastAPI:
    bus = EventBus()
    manager = SessionManager(home=home, config=config, llm=llm, bus=bus)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.rehydrate()
        yield

    app = FastAPI(title="Forge", lifespan=lifespan)
    app.state.manager = manager

    projects = ProjectStore(home)
    app.state.projects = projects

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
        actor = manager.create(cwd=body.cwd, model=body.model, autonomy=body.autonomy)
        return actor.meta.model_dump()

    @app.post("/api/sessions/{sid}/messages", status_code=202)
    async def post_message(sid: str, body: PostMessage):
        await _actor(sid).post_message(body.text)
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
        _actor(sid).set_autonomy(body.autonomy)
        return {}

    @app.post("/api/sessions/{sid}/model")
    async def set_model(sid: str, body: SetModel):
        if body.model not in {m.id for m in config.models}:
            raise HTTPException(400, f"unknown model: {body.model}")
        _actor(sid).set_model(body.model)
        return {}

    @app.post("/api/sessions/{sid}/compact")
    async def compact(sid: str):
        if not await _actor(sid).compact_now():
            raise HTTPException(409, "session is running; compact after the run finishes")
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

    @app.get("/api/sessions/{sid}/changesets")
    async def changesets(sid: str):
        return [c.model_dump() for c in _actor(sid).changesets.list()]

    @app.post("/api/sessions/{sid}/changesets/{index}/revert")
    async def revert(sid: str, index: int):
        _actor(sid).changesets.revert(index)
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

    @app.get("/api/projects")
    async def list_projects():
        return [p.model_dump() for p in projects.list()]

    @app.post("/api/projects")
    async def create_project(body: CreateProject):
        cwd = Path(body.cwd).expanduser()
        if not cwd.is_dir():
            raise HTTPException(400, f"not a directory: {body.cwd}")
        return projects.create(
            name=body.name, cwd=str(cwd), default_model=body.default_model,
            default_autonomy=body.default_autonomy,
            default_effort=body.default_effort).model_dump()

    @app.patch("/api/projects/{pid}")
    async def update_project(pid: str, body: UpdateProject):
        fields = body.model_dump(exclude_unset=True)
        if "cwd" in fields:
            cwd = Path(fields["cwd"]).expanduser() if fields["cwd"] else None
            if cwd is None or not cwd.is_dir():
                raise HTTPException(400, f"not a directory: {fields['cwd']}")
            fields["cwd"] = str(cwd)
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

    @app.get("/api/recent_dirs")
    async def recent_dirs():
        return manager.recent_cwds()

    @app.get("/api/skills")
    async def skills():
        return [s.model_dump() for s in discover_skills([home / "skills"])]

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
                if sid in manager.actors:
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
