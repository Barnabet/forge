from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from forge.api.schemas import (
    CreateSession, PostMessage, RenameSession, ResolveApproval, SetAutonomy,
)
from forge.engine.bus import EventBus
from forge.engine.manager import SessionManager
from forge.engine.skills import discover_skills
from forge.llm.base import LLMClient
from forge.store.config import ForgeConfig
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
        await manager.get(sid).post_message(body.text)
        return {}

    @app.post("/api/sessions/{sid}/approvals/{call_id}")
    async def resolve(sid: str, call_id: str, body: ResolveApproval):
        await manager.get(sid).resolve_approval(
            call_id, body.decision,
            body.always.model_dump() if body.always else None)
        return {}

    @app.post("/api/sessions/{sid}/cancel")
    async def cancel(sid: str):
        manager.get(sid).cancel()
        return {}

    @app.post("/api/sessions/{sid}/autonomy")
    async def set_autonomy(sid: str, body: SetAutonomy):
        manager.get(sid).set_autonomy(body.autonomy)
        return {}

    @app.patch("/api/sessions/{sid}")
    async def rename(sid: str, body: RenameSession):
        actor = manager.get(sid)
        actor.meta.name = body.name
        from forge.engine.events import SessionRenamed
        actor.emit(actor._e(SessionRenamed, name=body.name))
        return {}

    @app.get("/api/sessions/{sid}/events")
    async def events(sid: str, after: int = 0):
        return [e.model_dump(mode="json") for e in manager.get(sid).log.read(after)]

    @app.get("/api/sessions/{sid}/changesets")
    async def changesets(sid: str):
        return [c.model_dump() for c in manager.get(sid).changesets.list()]

    @app.post("/api/sessions/{sid}/changesets/{index}/revert")
    async def revert(sid: str, index: int):
        manager.get(sid).changesets.revert(index)
        return {}

    @app.post("/api/sessions/{sid}/changesets/keep_all")
    async def keep_all(sid: str):
        manager.get(sid).changesets.keep_all()
        return {}

    @app.get("/api/sessions/{sid}/files")
    async def file_search(sid: str, q: str = ""):
        cwd = Path(manager.get(sid).meta.cwd)
        hits = []
        for p in cwd.rglob("*"):
            if not p.is_file() or any(part in SKIP_DIRS or part.startswith(".")
                                      for part in p.relative_to(cwd).parts):
                continue
            rel = str(p.relative_to(cwd))
            if _subseq(q.lower(), rel.lower()):
                hits.append(rel)
        return sorted(hits, key=len)[:50]

    @app.get("/api/skills")
    async def skills():
        return [s.model_dump() for s in discover_skills([home / "skills"])]

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        raw = await websocket.receive_text()
        cursors: dict[str, int] = json.loads(raw).get("cursors", {})
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


def main() -> None:
    import os

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
