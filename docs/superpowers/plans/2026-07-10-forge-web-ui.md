# Forge Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Forge SPA — pixel-perfect implementation of design handoff card **2a "Stream"** (`design_handoff_agent_workspace/README.md`), consuming the merged Python engine's REST + WebSocket API, plus the three small engine endpoints the composer requires (`/model`, `/compact`, changeset file content).

**Architecture:** React + Vite + TS SPA in `web/`. The UI is a pure projection of the engine's event stream: a projection reducer folds durable + ephemeral events into per-session `stream[]` items; a Zustand store holds sessions/drawer/connection; one multiplexed WebSocket with per-session cursors feeds it. TS protocol types are generated from the engine's Pydantic models (`python -m forge.protocol_export`) — no hand-synced protocol. Tasks 1–3 are Python (engine gaps + hardening the UI depends on); tasks 4–17 are the web app.

**Tech Stack:** React 19, Vite, TypeScript (strict), Zustand, react-markdown, CSS Modules, @fontsource/geist-sans + @fontsource/geist-mono, vitest + @testing-library/react + jsdom, json-schema-to-typescript (dev), pnpm. Server side: existing stack (FastAPI, pydantic v2, pytest, ruff line-length 100), no new Python deps.

## Global Constraints

- **Pixel fidelity:** every color, radius, shadow, font size, and spacing value comes verbatim from `design_handoff_agent_workspace/README.md` (card 2a). They are encoded once in `web/src/styles/tokens.css` as CSS custom properties; component CSS references the variables (or the README's literal value for one-offs). ACCENT = `#35e0c2`; accent tints ONLY via `color-mix(in oklab, …)`, never hardcoded. Reference viewport 1360×830; layout is fluid (chat column flexes, drawer fixed 480px).
- **Fonts:** Geist Sans + Geist Mono self-hosted via `@fontsource/geist-sans` and `@fontsource/geist-mono` (weights 400/500/600/700). `font-family` names: `'Geist Sans'` / `'Geist Mono'`.
- **Autonomy naming:** engine values are `yolo` | `guarded` and UI copy uses them as-is (model pill `opus-5 · yolo`). The handoff's "autopilot" == `yolo`.
- **UI holds no business logic.** All state changes arrive as events; REST calls are fire-and-forget commands. The reducer is pure: `(SessionStream, WireEvent) -> SessionStream`, no I/O.
- **TypeScript:** `strict: true`, no `any` in `web/src` (tests may use casts). `lib` must include `ES2023` (reducer uses `findLastIndex`).
- **Dev servers:** engine on `127.0.0.1:8700`; Vite dev server proxies `/api` (http) and `/ws` (ws) to it. Production: FastAPI serves `web/dist` (already mounted in `server/forge/api/app.py`).
- **Python tasks:** run with `uv run pytest` / `uv run ruff check .` from `server/`; ruff line-length 100; tests use FakeLLM + FastAPI TestClient like the existing suite in `server/tests/`.
- **Web tests:** `pnpm test` (vitest, `globals: false` — import `describe/it/expect` from `vitest`).
- **Commits:** small, per task step, conventional prefixes (`feat:`, `test:`, `chore:`).

## Protocol Contract (binding for the reducer/WS tasks)

These are engine facts, verified against `server/forge/` on main (e6dd9a8 + tasks 1–3 of this plan):

1. **Dedupe by seq.** Durable events carry a per-session `seq` ≥ 1; ephemeral events (`text_delta`, `output_chunk`) always have `seq == 0`. On reconnect the server replays from the client's cursor — replay and live delivery can overlap, so the reducer MUST drop any durable event with `seq <= lastSeq` for its session. Ephemeral events are never deduped (and never replayed).
2. **`tool_call_finished` may arrive without a matching `tool_call_started`.** The engine emits a bare `finished` for: unknown tool, invalid arguments JSON, a denied approval (`output: "User denied this action."`), and dangling-call closure on cancel/restart. The reducer must create a completed card in that case — except when a *denied gate* with the same `call_id` exists, where the event is dropped (the denied gate already tells the story). `tool_call_finished` has no `display` field; fall back to the `tool` name.
3. **Idle is derived.** `run_finished` (any reason) sets the session idle. Normal runs also emit `status_changed(idle)` after it (harmless no-op), but crash rehydration synthesizes `run_finished(interrupted)` with NO `status_changed` — so the reducer must not rely on `status_changed` alone. Session status otherwise follows `status_changed` (`idle|running|attention|queued`); `attention` == waiting on approval.
4. **`text_delta` has no durable-event reference.** Deltas belong to the currently streaming assistant turn of their session: append to the trailing streaming prose item, create one if absent. The following `assistant_message` REPLACES the accumulated delta text with the final text (empty final text with tool calls only → remove the placeholder). `output_chunk` DOES carry `call_id` → append to that running tool card; `tool_call_finished.output` replaces the accumulated live output.
5. **WS handshake:** the server blocks on a first text frame `{"cursors": {"<sid>": <last_seq>}}` before sending anything. It replays only sessions named in `cursors`, but publishes ALL sessions' live events afterward. Non-localhost `Origin` → close 4403; malformed first payload → close 4400 (Task 3).
6. **Generated TS optionality:** pydantic fields with defaults (`seq`, `auto_approved`, `is_error`, `duration_ms`, `diff_stats`, `tool_calls`) are not in JSON-Schema `required`, so codegen marks them optional. The wire always includes them (`model_dump`), but TS code must still use `??` fallbacks (`e.seq ?? 0`, `e.is_error ?? false`, …).

REST surface used by the UI (all under same origin): `GET /api/health` → `{ok}`, `GET /api/models` → `ModelInfo[]`, `GET/POST /api/sessions`, `POST /api/sessions/{sid}/messages` (202; also mid-run steering), `POST …/approvals/{call_id}` `{decision, always?: {pattern, scope}}`, `POST …/cancel`, `POST …/autonomy`, `POST …/model` (Task 1), `POST …/compact` (Task 2, 409 while running), `PATCH /api/sessions/{sid}` `{name}`, `GET …/events?after=`, `GET …/changesets`, `POST …/changesets/{i}/revert`, `POST …/changesets/keep_all`, `GET …/changesets/{i}/file` (Task 3), `GET …/files?q=` → `string[]` (fuzzy, relative paths), `GET /api/skills`.

## File Structure

```
server/forge/engine/events.py        # Task 1: + ModelChanged
server/forge/engine/actor.py         # Task 1: set_model · Task 2: _compact/compact_now
server/forge/engine/manager.py       # Task 1: replay model_changed
server/forge/api/schemas.py          # Task 1: SetModel
server/forge/api/app.py              # Tasks 1–3: new routes, 404 helper, WS payload guard
server/forge/store/changesets.py     # Task 3: after_content()
server/tests/test_api_extras.py      # Tasks 1–3 tests

web/
  package.json  vite.config.ts  tsconfig.json  index.html
  scripts/gen-protocol.mjs           # Task 5
  src/
    main.tsx  App.tsx  App.module.css
    styles/tokens.css  styles/global.css
    test/setup.ts
    protocol/generated.ts            # codegen output (committed)
    protocol/index.ts                # WireEvent union, guards, ModelInfo
    api.ts                           # REST client
    ws.ts                            # WS client w/ reconnect + cursors
    lib/diff.ts                      # unified-diff parser (drawer)
    state/reducer.ts                 # projection reducer (the core)
    state/store.ts                   # Zustand store
    components/
      TopBar.tsx / .module.css
      ChatStream.tsx / .module.css   # user bubble, agent prose, status line, item routing
      ToolCard.tsx / .module.css
      ApprovalGate.tsx / .module.css
      Composer.tsx / .module.css
      FilePicker.tsx  CommandPalette.tsx / Popover.module.css
      DetailDrawer.tsx / .module.css
    (tests co-located: *.test.ts / *.test.tsx)
```

---

### Task 1: Engine — `model_changed` event + `POST /api/sessions/{sid}/model`

The spec's composer `/model` command needs a way to switch a session's model. Working dir: `server/`.

**Files:**
- Modify: `server/forge/engine/events.py` (new event class + union)
- Modify: `server/forge/engine/actor.py` (`set_model`)
- Modify: `server/forge/engine/manager.py` (`_replay_meta` applies it)
- Modify: `server/forge/api/schemas.py`, `server/forge/api/app.py`
- Test: `server/tests/test_api_extras.py` (new file)

**Interfaces:**
- Produces: `ModelChanged(type="model_changed", model: str)` durable event; `SessionActor.set_model(model: str) -> None`; `POST /api/sessions/{sid}/model` body `{"model": "<id>"}` → 200 `{}` / 400 unknown model id.
- Consumes: existing `SessionActor.emit/_e`, `ForgeConfig.models` (list of `ModelConfig(id, display_name, context_window)`).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_api_extras.py`. Copy the app/client fixture pattern from `server/tests/test_api.py` (tmp home + FakeLLM + TestClient); it is referenced by every task-1–3 test:

```python
import pytest
from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.llm.fake import FakeLLM
from forge.store.config import load_config


@pytest.fixture()
def make_client(tmp_path):
    def _make(script=None):
        config = load_config(tmp_path)
        llm = FakeLLM(script or [])
        app = create_app(tmp_path, config, llm)
        return TestClient(app)
    return _make


def test_set_model_emits_event_and_updates_meta(make_client):
    client = make_client()
    with client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        r = client.post(f"/api/sessions/{sid}/model", json={"model": "gpt-5"})
        assert r.status_code == 200
        metas = client.get("/api/sessions").json()
        assert metas[0]["model"] == "gpt-5"
        events = client.get(f"/api/sessions/{sid}/events").json()
        assert events[-1]["type"] == "model_changed"
        assert events[-1]["model"] == "gpt-5"


def test_set_model_rejects_unknown_id(make_client):
    client = make_client()
    with client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        r = client.post(f"/api/sessions/{sid}/model", json={"model": "nope"})
        assert r.status_code == 400


def test_model_change_survives_rehydrate(tmp_path):
    config = load_config(tmp_path)
    app = create_app(tmp_path, config, FakeLLM([]))
    with TestClient(app) as client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post(f"/api/sessions/{sid}/model", json={"model": "gpt-5"})
    app2 = create_app(tmp_path, load_config(tmp_path), FakeLLM([]))
    with TestClient(app2) as client:
        assert client.get("/api/sessions").json()[0]["model"] == "gpt-5"
```

Note: `load_config` on an empty home returns `DEFAULT_MODELS` — check `server/forge/store/config.py` and use a real default id from that list instead of `"gpt-5"` if it differs (the test must use an id present in `config.models`, and a bogus one for the 400 case).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_extras.py -v`
Expected: FAIL (404 route not found / no `model_changed` event).

- [ ] **Step 3: Implement**

`server/forge/engine/events.py` — after `AutonomyChanged`:

```python
class ModelChanged(BaseEvent):
    type: Literal["model_changed"] = "model_changed"
    model: str
```

and add `ModelChanged` to the `Event` union.

`server/forge/engine/actor.py` — import `ModelChanged`; after `set_autonomy`:

```python
    def set_model(self, model: str) -> None:
        self.meta.model = model
        self.emit(self._e(ModelChanged, model=model))
```

`server/forge/engine/manager.py` — in `_replay_meta`'s loop:

```python
            elif meta and e.type == "model_changed":
                meta.model = e.model
```

`server/forge/api/schemas.py`:

```python
class SetModel(BaseModel):
    model: str
```

`server/forge/api/app.py` — import `SetModel` and `HTTPException`; new route next to `set_autonomy`:

```python
    @app.post("/api/sessions/{sid}/model")
    async def set_model(sid: str, body: SetModel):
        if body.model not in {m.id for m in config.models}:
            raise HTTPException(400, f"unknown model: {body.model}")
        manager.get(sid).set_model(body.model)
        return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_extras.py -v` then the whole suite `uv run pytest -q` and `uv run ruff check .`
Expected: all PASS (58 existing + 3 new), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add forge/engine/events.py forge/engine/actor.py forge/engine/manager.py forge/api/schemas.py forge/api/app.py tests/test_api_extras.py
git commit -m "feat: model_changed event + POST /api/sessions/{sid}/model"
```

---

### Task 2: Engine — manual compaction endpoint `POST /api/sessions/{sid}/compact`

The composer's `/compact` command. Extract the summarize-and-emit body from `_maybe_compact` so both the automatic threshold path and the manual trigger share it.

**Files:**
- Modify: `server/forge/engine/actor.py` (`_maybe_compact` → `_compact` + `compact_now`)
- Modify: `server/forge/api/app.py`
- Test: `server/tests/test_api_extras.py`

**Interfaces:**
- Produces: `SessionActor.compact_now() -> bool` (False when a run is active); `POST /api/sessions/{sid}/compact` → 200 `{}` idle / 409 while running.
- Consumes: existing `to_messages`, `self.llm.complete`, `ContextCompacted`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_api_extras.py`. FakeLLM script entries follow the existing suite's format (see `server/tests/test_api.py` / `forge/llm/fake.py`) — a scripted `CompletionResult` per call; the compact test needs one plain-text completion for the initial run and one for the summary:

```python
from forge.llm.base import CompletionResult


def test_manual_compact_emits_event(make_client):
    client = make_client(script=[
        CompletionResult(text="hi there", tool_calls=[], usage_tokens=10),
        CompletionResult(text="SUMMARY", tool_calls=[], usage_tokens=5),
    ])
    with client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post(f"/api/sessions/{sid}/messages", json={"text": "hello"})
        # wait for the run to finish (idle status in meta)
        for _ in range(100):
            if client.get("/api/sessions").json()[0]["status"] == "idle":
                break
        r = client.post(f"/api/sessions/{sid}/compact")
        assert r.status_code == 200
        events = client.get(f"/api/sessions/{sid}/events").json()
        compacted = [e for e in events if e["type"] == "context_compacted"]
        assert compacted and compacted[-1]["summary"] == "SUMMARY"
        assert compacted[-1]["upto_seq"] > 0


def test_compact_while_running_is_409(make_client, tmp_path):
    # a FakeLLM with a delay keeps the run active while we hit /compact
    client = make_client(script=[
        CompletionResult(text="slow", tool_calls=[], usage_tokens=10),
    ])
    client.app.state.manager.llm.delay = 0.3
    with client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        client.post(f"/api/sessions/{sid}/messages", json={"text": "go"})
        r = client.post(f"/api/sessions/{sid}/compact")
        assert r.status_code == 409
```

Adjust to the repo's actual FakeLLM constructor (`FakeLLM(script, delay=0.0)`) — if `delay` is per-instance, build the second client with `FakeLLM(script, delay=0.3)` instead of mutating (extend `make_client` with a `delay=0.0` kwarg).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_extras.py -v`
Expected: the two new tests FAIL (404: no /compact route).

- [ ] **Step 3: Implement**

`server/forge/engine/actor.py` — replace `_maybe_compact` with:

```python
    async def _maybe_compact(self, usage_tokens: int) -> None:
        window = self.config.context_window(self.meta.model)
        if usage_tokens <= COMPACT_THRESHOLD * window:
            return
        await self._compact()

    async def compact_now(self) -> bool:
        """Manual /compact. Refused while a run is active."""
        if self.run_task and not self.run_task.done():
            return False
        await self._compact()
        return True

    async def _compact(self) -> None:
        msgs = to_messages(self.log.read(), "")[1:]  # drop system stub
        transcript = "\n".join(
            f"{m['role'].upper()}: {m.get('content') or m.get('tool_calls', '')}"
            for m in msgs)[-200_000:]

        async def no_delta(_: str) -> None: ...

        # Capture the cut point BEFORE the summarizer await: a steering message
        # posted while the summarizer is in flight must survive projection.
        upto = self.log.last_seq
        summary = await self.llm.complete(
            self.meta.model,
            [{"role": "user", "content":
              "Summarize this agent session so far for continuation. Include the "
              "original task, key decisions, files touched, current progress, and "
              "immediate next steps.\n\n" + transcript}],
            [], no_delta)
        self.emit(self._e(ContextCompacted, summary=summary.text, upto_seq=upto))
```

(The `_compact` body is the existing `_maybe_compact` tail, moved verbatim — including the upto-before-await comment and ordering. Do not reorder.)

`server/forge/api/app.py`:

```python
    @app.post("/api/sessions/{sid}/compact")
    async def compact(sid: str):
        if not await manager.get(sid).compact_now():
            raise HTTPException(409, "session is running; compact after the run finishes")
        return {}
```

- [ ] **Step 4: Run tests, full suite, ruff**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all PASS (the existing compaction-threshold test in the suite must still pass — the extraction must not change automatic behavior).

- [ ] **Step 5: Commit**

```bash
git add forge/engine/actor.py forge/api/app.py tests/test_api_extras.py
git commit -m "feat: manual compaction via POST /api/sessions/{sid}/compact"
```

---

### Task 3: Engine — changeset file content, 404s for unknown sessions, WS payload guard

Three small API gaps the UI depends on: the drawer's File view needs the post-edit content; unknown session ids currently raise `KeyError` (500); a malformed WS first payload currently kills the handler with a traceback.

**Files:**
- Modify: `server/forge/store/changesets.py` (`after_content`)
- Modify: `server/forge/api/app.py` (`_actor` helper used by ALL session routes, new route, WS guard)
- Test: `server/tests/test_api_extras.py`

**Interfaces:**
- Produces: `ChangesetStore.after_content(index: int) -> str`; `GET /api/sessions/{sid}/changesets/{index}/file` → `{"path": str, "content": str}` (404 bad index); every `/api/sessions/{sid}/…` route → 404 `{"detail": "unknown session: <sid>"}` for unknown sid; WS close code 4400 on malformed first payload.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_api_extras.py`:

```python
def test_unknown_session_is_404(make_client):
    client = make_client()
    with client:
        assert client.get("/api/sessions/nope/events").status_code == 404
        assert client.post("/api/sessions/nope/messages",
                           json={"text": "x"}).status_code == 404
        assert client.post("/api/sessions/nope/cancel").status_code == 404


def test_changeset_file_content(make_client, tmp_path):
    client = make_client()
    with client:
        sid = client.post("/api/sessions",
                          json={"cwd": str(tmp_path)}).json()["id"]
        actor = client.app.state.manager.get(sid)
        target = tmp_path / "hello.txt"
        actor.changesets.record(target, None, "new content\n")
        r = client.get(f"/api/sessions/{sid}/changesets/0/file")
        assert r.status_code == 200
        assert r.json() == {"path": str(target), "content": "new content\n"}
        assert client.get(
            f"/api/sessions/{sid}/changesets/9/file").status_code == 404


def test_ws_malformed_first_payload_closes_4400(make_client):
    client = make_client()
    with client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws") as ws:
                ws.send_text("this is not json")
                ws.receive_text()
```

(The WS test asserts the connection dies rather than the exact close code — Starlette's test client surfaces the close as a `WebSocketDisconnect`; assert `excinfo.value.code == 4400` if the existing WS tests in the suite show that pattern, otherwise the raises-check is the gate.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_extras.py -v`
Expected: 404 tests FAIL with 500s (KeyError), file test FAILs 404-route-missing, WS test FAILs (server error, not clean close).

- [ ] **Step 3: Implement**

`server/forge/store/changesets.py`:

```python
    def after_content(self, index: int) -> str:
        return (self.blobs / f"{index}.after").read_text()
```

`server/forge/api/app.py` — inside `create_app`, above the routes:

```python
    def _actor(sid: str):
        try:
            return manager.get(sid)
        except KeyError:
            raise HTTPException(404, f"unknown session: {sid}") from None
```

Replace every `manager.get(sid)` in route bodies with `_actor(sid)` (messages, approvals, cancel, autonomy, model, compact, rename, events, changesets, revert, keep_all, files). Add the route:

```python
    @app.get("/api/sessions/{sid}/changesets/{index}/file")
    async def changeset_file(sid: str, index: int):
        actor = _actor(sid)
        try:
            cs = actor.changesets.get(index)
            return {"path": cs.path, "content": actor.changesets.after_content(index)}
        except (IndexError, FileNotFoundError):
            raise HTTPException(404, f"no changeset {index}") from None
```

WS handler — wrap the first-payload parse (replacing the bare `json.loads(raw).get(...)` line):

```python
        raw = await websocket.receive_text()
        try:
            cursors_raw = json.loads(raw).get("cursors", {})
            cursors: dict[str, int] = {
                str(k): int(v) for k, v in cursors_raw.items()}
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            await websocket.close(code=4400)
            return
```

- [ ] **Step 4: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add forge/store/changesets.py forge/api/app.py tests/test_api_extras.py
git commit -m "feat: changeset file endpoint, 404 for unknown sessions, WS payload guard"
```

---

### Task 4: Web scaffold — Vite app, tokens, fonts, vitest

**Files:**
- Create: `web/` via Vite template, then: `web/vite.config.ts`, `web/tsconfig.app.json` (edit), `web/src/styles/tokens.css`, `web/src/styles/global.css`, `web/src/test/setup.ts`, `web/src/main.tsx`, `web/src/App.tsx` (placeholder), `web/index.html` (edit title)
- Test: `web/src/App.test.tsx`

**Interfaces:**
- Produces: the `web/` package with working `pnpm dev` / `pnpm build` / `pnpm test`; the design-token custom properties every later component task references (`--accent`, `--ink`, `--bg-*`, `--text-*`, `--ok*`, `--danger*`, `--warn*`, `--hair-*`, `--r-*`, `--font-sans`, `--font-mono`, `--shadow-card`, `--shadow-composer`).

- [ ] **Step 1: Scaffold and install**

From the repo root:

```bash
pnpm create vite web --template react-ts
cd web
pnpm install
pnpm add zustand react-markdown @fontsource/geist-sans @fontsource/geist-mono
pnpm add -D vitest jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom json-schema-to-typescript
```

Delete the template noise: `src/App.css`, `src/index.css`, `src/assets/`, `public/vite.svg` and their imports/references.

- [ ] **Step 2: Config**

`web/vite.config.ts` (replace):

```ts
/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8700',
      '/ws': { target: 'ws://127.0.0.1:8700', ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['src/test/setup.ts'],
    globals: false,
    css: { modules: { classNameStrategy: 'non-scoped' } },
  },
})
```

`web/tsconfig.app.json`: set `"lib": ["ES2023", "DOM", "DOM.Iterable"]` (keep the template's other strict options; ensure `"strict": true`).

`web/package.json` scripts:

```json
{
  "dev": "vite",
  "build": "tsc -b && vite build",
  "test": "vitest run",
  "test:watch": "vitest",
  "gen:protocol": "node scripts/gen-protocol.mjs"
}
```

`web/src/test/setup.ts`:

```ts
import '@testing-library/jest-dom/vitest'
```

`web/index.html`: `<title>Forge</title>`, `<html lang="en">`, keep `<div id="root">`.

- [ ] **Step 3: Design tokens and global styles**

`web/src/styles/tokens.css` — the handoff README token sheet, verbatim values:

```css
:root {
  /* accent — tints ONLY via color-mix, never hardcoded */
  --accent: #35e0c2;
  --ink: color-mix(in oklab, var(--accent) 20%, #050505);

  /* backgrounds */
  --bg-app: #0a0a0c;
  --bg-bar: linear-gradient(180deg, #0e0e11, #0b0b0d);
  --bg-card: #0e0e11;
  --bg-drawer: #0c0c0f;
  --bg-composer: #131317;
  --bg-raised-1: #1a1a20;
  --bg-raised-2: #1b1b21;
  --bg-raised-3: #22222a;

  /* text */
  --text-primary: #ececef;
  --text-body: #b9b9c2;
  --text-secondary: #9d9da8;
  --text-muted: #8f8f9a;
  --text-faint: #62626d;
  --text-faint-2: #55555f;
  --text-ghost: #4c4c56;
  --text-ghost-2: #3f3f49;

  /* semantic */
  --ok: #6fd598;
  --ok-dim: #86d9a8;
  --ok-bg: rgba(111, 213, 152, 0.1);
  --ok-row: rgba(111, 213, 152, 0.07);
  --danger: #ee8484;
  --danger-dim: #e89b9b;
  --danger-bg: rgba(238, 132, 132, 0.1);
  --danger-row: rgba(238, 132, 132, 0.07);
  --warn: #e5b84b;
  --warn-dot: #e0b34b;
  --warn-title: #eac26a;

  /* hairlines by elevation */
  --hair-1: rgba(255, 255, 255, 0.05);
  --hair-2: rgba(255, 255, 255, 0.06);
  --hair-3: rgba(255, 255, 255, 0.07);
  --hair-4: rgba(255, 255, 255, 0.08);
  --hair-5: rgba(255, 255, 255, 0.09);
  --hair-hover: rgba(255, 255, 255, 0.14);

  /* radii */
  --r-tile: 5px;
  --r-btn: 7px;
  --r-btn-lg: 8px;
  --r-seg: 9px;
  --r-card: 12px;
  --r-composer: 14px;
  --r-pill: 999px;

  /* shadows */
  --shadow-card: inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 2px 10px rgba(0, 0, 0, 0.25);
  --shadow-composer: 0 12px 32px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.05);
  --glow-accent: 0 0 12px color-mix(in oklab, var(--accent) 30%, transparent);

  /* type */
  --font-sans: 'Geist Sans', system-ui, sans-serif;
  --font-mono: 'Geist Mono', ui-monospace, monospace;
}
```

`web/src/styles/global.css`:

```css
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body, #root { height: 100%; }
body {
  background: var(--bg-app);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: 13.5px;
  -webkit-font-smoothing: antialiased;
  overflow: hidden;
}
button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; }
input, textarea { font: inherit; color: inherit; background: none; border: none; outline: none; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.08); border-radius: 5px; border: 3px solid transparent; background-clip: content-box; }
```

`web/src/main.tsx`:

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/geist-sans/400.css'
import '@fontsource/geist-sans/500.css'
import '@fontsource/geist-sans/600.css'
import '@fontsource/geist-sans/700.css'
import '@fontsource/geist-mono/400.css'
import '@fontsource/geist-mono/500.css'
import './styles/tokens.css'
import './styles/global.css'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

If a `@fontsource/geist-sans` import path fails at build, check `node_modules/@fontsource/` for the actual package layout (`geist-sans/index.css` etc.) and adjust the weight imports — family names stay `'Geist Sans'` / `'Geist Mono'`.

`web/src/App.tsx` (placeholder, replaced in Task 17):

```tsx
export default function App() {
  return <div>Forge</div>
}
```

- [ ] **Step 4: Write the smoke test and run everything**

`web/src/App.test.tsx`:

```tsx
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from './App'

describe('App', () => {
  it('renders', () => {
    render(<App />)
    expect(screen.getByText('Forge')).toBeInTheDocument()
  })
})
```

Run: `pnpm test` → 1 pass. `pnpm build` → succeeds. `pnpm dev` boots without errors (Ctrl-C after confirming).

- [ ] **Step 5: Commit**

```bash
git add web
git commit -m "chore: scaffold web app (vite/react-ts, tokens, fonts, vitest)"
```

(Confirm the repo root `.gitignore` covers `web/node_modules` and `web/dist`; add them if not.)

---

### Task 5: Protocol codegen — Pydantic → TypeScript

One type system: `python -m forge.protocol_export` (already on main, `server/Makefile` target `export-protocol`) prints a JSON-Schema bundle with keys `event`, `text_delta`, `output_chunk`, `session_meta`, `changeset`. A node script compiles it to `web/src/protocol/generated.ts`, which is committed so builds never need the server.

**Files:**
- Create: `web/scripts/gen-protocol.mjs`, `web/src/protocol/generated.ts` (generated, committed), `web/src/protocol/index.ts`
- Test: `web/src/protocol/protocol.test.ts`

**Interfaces:**
- Produces (from `index.ts`, consumed by every later task):

```ts
export type DurableEvent   // the 15-member discriminated union (incl. model_changed)
export type WireEvent = DurableEvent | TextDelta | OutputChunk
export type { SessionMeta, Changeset, TextDelta, OutputChunk }
export type Autonomy = 'yolo' | 'guarded'
export type Status = 'idle' | 'running' | 'attention' | 'queued'
export interface DiffStats { path: string; added: number; removed: number; changeset_index: number }
export interface ModelInfo { id: string; display_name: string; context_window: number }
export function seqOf(e: WireEvent): number   // e.seq ?? 0
```

- [ ] **Step 1: Write the codegen script**

`web/scripts/gen-protocol.mjs`:

```js
// Regenerate web/src/protocol/generated.ts from the engine's Pydantic models.
// Usage: pnpm gen:protocol  (requires uv + the server venv)
import { execSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { compile } from 'json-schema-to-typescript'

const serverDir = fileURLToPath(new URL('../../server', import.meta.url))
const raw = execSync('uv run python -m forge.protocol_export', { cwd: serverDir })
const bundle = JSON.parse(raw.toString())

let out =
  '/* AUTO-GENERATED from the engine Pydantic models — do not edit.\n' +
  ' * Regenerate with: pnpm gen:protocol */\n\n'
for (const [key, schema] of Object.entries(bundle)) {
  out += await compile(schema, key, {
    bannerComment: '',
    additionalProperties: false,
  })
  out += '\n'
}
writeFileSync(new URL('../src/protocol/generated.ts', import.meta.url), out)
console.log('wrote src/protocol/generated.ts')
```

- [ ] **Step 2: Generate and inspect**

Run: `pnpm gen:protocol`
Expected: `web/src/protocol/generated.ts` exists and contains `export type Event =` (a union of `SessionCreated | … | ModelChanged | …`), plus `TextDelta`, `OutputChunk`, `SessionMeta`, `Changeset` interfaces. If `json-schema-to-typescript` names collide (it may emit duplicate helper types across bundle keys), fix by compiling with `{ declareExternallyReferenced: true }` only for the first key that references each `$def` — verify by running `tsc`: the file must typecheck with zero errors. The generated union member names must match the Pydantic class names; if the root union is named differently (e.g. `Event1`), adapt `index.ts` re-exports accordingly and note it in the report.

- [ ] **Step 3: Write `index.ts` and the failing test**

`web/src/protocol/index.ts`:

```ts
import type { Changeset, Event, OutputChunk, SessionMeta, TextDelta } from './generated'

export type DurableEvent = Event
export type WireEvent = Event | TextDelta | OutputChunk
export type { Changeset, OutputChunk, SessionMeta, TextDelta }

export type Autonomy = SessionMeta['autonomy']
export type Status = SessionMeta['status']

export interface DiffStats {
  path: string
  added: number
  removed: number
  changeset_index: number
}

export interface ModelInfo {
  id: string
  display_name: string
  context_window: number
}

/** Pydantic defaults make seq optional in the generated types; wire always has it. */
export function seqOf(e: WireEvent): number {
  return e.seq ?? 0
}
```

`web/src/protocol/protocol.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { seqOf, type WireEvent } from './index'

describe('protocol', () => {
  it('generated bundle covers every engine event type', () => {
    const src = readFileSync(new URL('./generated.ts', import.meta.url), 'utf8')
    for (const t of [
      'session_created', 'session_renamed', 'status_changed', 'autonomy_changed',
      'model_changed', 'user_message', 'assistant_message', 'tool_call_started',
      'tool_call_finished', 'approval_requested', 'approval_resolved',
      'policy_added', 'context_compacted', 'run_finished', 'error',
      'text_delta', 'output_chunk',
    ]) expect(src).toContain(`"${t}"`)
  })

  it('seqOf defaults missing seq to 0', () => {
    const e = { type: 'text_delta', session_id: 's', text: 'x' } as WireEvent
    expect(seqOf(e)).toBe(0)
  })
})
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm test && pnpm build`
Expected: PASS. The `model_changed` literal is present (Task 1 landed first).

- [ ] **Step 5: Commit**

```bash
git add scripts/gen-protocol.mjs src/protocol
git commit -m "feat: protocol codegen from engine pydantic models"
```

---

### Task 6: Projection reducer — core durable events

The heart of the UI: a pure reducer folding events into the design's `stream[]` items. This task covers session meta, user/assistant messages, tool calls (including the dedupe and finished-without-started contracts). Task 7 adds ephemeral deltas, approvals, errors, and run lifecycle.

**Files:**
- Create: `web/src/state/reducer.ts`
- Test: `web/src/state/reducer.test.ts`

**Interfaces:**
- Produces (consumed by the store, ChatStream, TopBar):

```ts
export type StreamItem =
  | { kind: 'user'; seq: number; text: string }
  | { kind: 'prose'; seq: number; text: string; streaming: boolean }
  | { kind: 'tool'; seq: number; callId: string; tool: string; display: string;
      status: 'running' | 'done' | 'error'; output: string; durationMs: number;
      diffStats: DiffStats | null; autoApproved: boolean }
  | { kind: 'gate'; seq: number; callId: string; tool: string; display: string; denied: boolean }
  | { kind: 'error'; seq: number; message: string }
  | { kind: 'info'; seq: number; text: string }
  | { kind: 'compacted'; seq: number }

export interface SessionStream {
  lastSeq: number
  items: StreamItem[]
  name: string; cwd: string; model: string
  autonomy: Autonomy; status: Status
  steps: number            // tool calls since the last user message (status line "step N")
}

export function emptyStream(): SessionStream
export function reduce(s: SessionStream, e: WireEvent): SessionStream  // pure, no I/O
```

- [ ] **Step 1: Write the failing tests**

`web/src/state/reducer.test.ts`. Helper builds events tersely; `run` folds a list:

```ts
import { describe, expect, it } from 'vitest'
import { emptyStream, reduce, type SessionStream } from './reducer'
import type { WireEvent } from '../protocol'

let seq = 0
const ev = (type: string, fields: object = {}, opts: { seq?: number } = {}): WireEvent =>
  ({ type, session_id: 's1', ts: 0, seq: opts.seq ?? ++seq, ...fields }) as unknown as WireEvent
const eph = (type: string, fields: object): WireEvent =>
  ({ type, session_id: 's1', seq: 0, ...fields }) as unknown as WireEvent
const run = (events: WireEvent[], from = emptyStream()): SessionStream =>
  events.reduce(reduce, from)

describe('reducer: session meta', () => {
  it('applies created/renamed/status/autonomy/model', () => {
    seq = 0
    const s = run([
      ev('session_created', { name: 'New session', cwd: '/w', model: 'm1', autonomy: 'yolo' }),
      ev('session_renamed', { name: 'fix the bug' }),
      ev('status_changed', { status: 'running' }),
      ev('autonomy_changed', { autonomy: 'guarded' }),
      ev('model_changed', { model: 'm2' }),
    ])
    expect(s).toMatchObject({
      name: 'fix the bug', cwd: '/w', model: 'm2',
      autonomy: 'guarded', status: 'running', lastSeq: 5,
    })
    expect(s.items).toHaveLength(0)  // meta events produce no stream items
  })
})

describe('reducer: dedupe by seq (replay overlap)', () => {
  it('drops a durable event already applied', () => {
    seq = 0
    const first = ev('user_message', { text: 'hi' })
    const s = run([first, first])  // replayed + live copy
    expect(s.items).toHaveLength(1)
    expect(s.lastSeq).toBe(1)
  })
})

describe('reducer: messages and tool calls', () => {
  it('user message resets steps and appends a bubble', () => {
    seq = 0
    const s = run([ev('user_message', { text: 'do it' })])
    expect(s.items[0]).toMatchObject({ kind: 'user', text: 'do it' })
    expect(s.steps).toBe(0)
  })

  it('assistant text becomes finalized prose', () => {
    seq = 0
    const s = run([ev('assistant_message', { text: 'Working on it.', tool_calls: [] })])
    expect(s.items[0]).toMatchObject({ kind: 'prose', text: 'Working on it.', streaming: false })
  })

  it('assistant message with only tool calls adds no prose', () => {
    seq = 0
    const s = run([ev('assistant_message', { text: '', tool_calls: [{ id: 'c1', name: 'bash', arguments: '{}' }] })])
    expect(s.items).toHaveLength(0)
  })

  it('tool started→chunk→finished lifecycle', () => {
    seq = 0
    const s = run([
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'pytest -q', auto_approved: true }),
      eph('output_chunk', { call_id: 'c1', text: 'collecting…\n' }),
      eph('output_chunk', { call_id: 'c1', text: '3 passed\n' }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: '3 passed', is_error: false, duration_ms: 812, diff_stats: null }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({
      kind: 'tool', callId: 'c1', display: 'pytest -q', status: 'done',
      output: '3 passed', durationMs: 812, autoApproved: true,
    })
    expect(s.steps).toBe(1)
  })

  it('mid-run output chunks accumulate on the running card', () => {
    seq = 0
    const s = run([
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'ls' }),
      eph('output_chunk', { call_id: 'c1', text: 'a\n' }),
      eph('output_chunk', { call_id: 'c1', text: 'b\n' }),
    ])
    expect(s.items[0]).toMatchObject({ kind: 'tool', status: 'running', output: 'a\nb\n' })
  })

  it('finished without started creates a completed card (contract #2)', () => {
    seq = 0
    const s = run([
      ev('tool_call_finished', { call_id: 'cx', tool: 'nope', output: 'Unknown tool: nope', is_error: true, duration_ms: 0, diff_stats: null }),
    ])
    expect(s.items[0]).toMatchObject({
      kind: 'tool', callId: 'cx', display: 'nope', status: 'error', output: 'Unknown tool: nope',
    })
  })

  it('finished with diff stats keeps them for the drawer link', () => {
    seq = 0
    const s = run([
      ev('tool_call_started', { call_id: 'c1', tool: 'edit_file', display: 'app.py' }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'edit_file', output: 'ok', is_error: false, duration_ms: 5, diff_stats: { path: '/w/app.py', added: 41, removed: 38, changeset_index: 2 } }),
    ])
    expect(s.items[0]).toMatchObject({ kind: 'tool', diffStats: { added: 41, removed: 38, changeset_index: 2 } })
  })

  it('optional fields absent (generated types) default safely', () => {
    seq = 0
    const s = run([
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'ls' }),  // no auto_approved
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: 'x' }),   // no is_error/duration/diff
    ])
    expect(s.items[0]).toMatchObject({ autoApproved: false, status: 'done', durationMs: 0, diffStats: null })
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pnpm test src/state/reducer.test.ts`
Expected: FAIL (module doesn't exist).

- [ ] **Step 3: Implement the reducer**

`web/src/state/reducer.ts` — complete file (the cases Task 7 covers are included in the switch skeleton but may be left as no-op `break` for now ONLY if the Task-6 tests pass without them; simplest is to write the full switch now and let Task 7's tests lock the remaining behavior):

```ts
import { seqOf, type Autonomy, type DiffStats, type Status, type WireEvent } from '../protocol'

export type StreamItem =
  | { kind: 'user'; seq: number; text: string }
  | { kind: 'prose'; seq: number; text: string; streaming: boolean }
  | { kind: 'tool'; seq: number; callId: string; tool: string; display: string;
      status: 'running' | 'done' | 'error'; output: string; durationMs: number;
      diffStats: DiffStats | null; autoApproved: boolean }
  | { kind: 'gate'; seq: number; callId: string; tool: string; display: string; denied: boolean }
  | { kind: 'error'; seq: number; message: string }
  | { kind: 'info'; seq: number; text: string }
  | { kind: 'compacted'; seq: number }

type ToolItem = Extract<StreamItem, { kind: 'tool' }>
type GateItem = Extract<StreamItem, { kind: 'gate' }>

export interface SessionStream {
  lastSeq: number
  items: StreamItem[]
  name: string
  cwd: string
  model: string
  autonomy: Autonomy
  status: Status
  steps: number
}

export function emptyStream(): SessionStream {
  return {
    lastSeq: 0, items: [], name: 'New session', cwd: '', model: '',
    autonomy: 'yolo', status: 'idle', steps: 0,
  }
}

function finalizeProse(items: StreamItem[]): void {
  const i = items.findLastIndex(it => it.kind === 'prose' && it.streaming)
  if (i >= 0) items[i] = { ...(items[i] as Extract<StreamItem, { kind: 'prose' }>), streaming: false }
}

export function reduce(s: SessionStream, e: WireEvent): SessionStream {
  const seq = seqOf(e)
  if (seq !== 0 && seq <= s.lastSeq) return s // replay/live overlap: drop duplicates
  const n: SessionStream = { ...s, items: [...s.items] }
  if (seq !== 0) n.lastSeq = seq

  switch (e.type) {
    case 'session_created':
      n.name = e.name; n.cwd = e.cwd; n.model = e.model; n.autonomy = e.autonomy
      break
    case 'session_renamed':
      n.name = e.name
      break
    case 'status_changed':
      n.status = e.status
      break
    case 'autonomy_changed':
      n.autonomy = e.autonomy
      break
    case 'model_changed':
      n.model = e.model
      break

    case 'user_message':
      finalizeProse(n.items)
      n.items.push({ kind: 'user', seq, text: e.text })
      n.steps = 0
      break

    case 'text_delta': {
      const last = n.items[n.items.length - 1]
      if (last?.kind === 'prose' && last.streaming)
        n.items[n.items.length - 1] = { ...last, text: last.text + e.text }
      else n.items.push({ kind: 'prose', seq: 0, text: e.text, streaming: true })
      break
    }

    case 'assistant_message': {
      // Final text replaces any accumulated deltas (contract #4).
      const i = n.items.findLastIndex(it => it.kind === 'prose' && it.streaming)
      if (i >= 0) {
        if (e.text) n.items[i] = { kind: 'prose', seq, text: e.text, streaming: false }
        else n.items.splice(i, 1)
      } else if (e.text) {
        n.items.push({ kind: 'prose', seq, text: e.text, streaming: false })
      }
      break
    }

    case 'tool_call_started':
      n.items.push({
        kind: 'tool', seq, callId: e.call_id, tool: e.tool, display: e.display,
        status: 'running', output: '', durationMs: 0, diffStats: null,
        autoApproved: e.auto_approved ?? false,
      })
      n.steps += 1
      break

    case 'output_chunk': {
      const i = n.items.findLastIndex(it => it.kind === 'tool' && it.callId === e.call_id)
      const it = n.items[i]
      if (it?.kind === 'tool' && it.status === 'running')
        n.items[i] = { ...it, output: it.output + e.text }
      break
    }

    case 'tool_call_finished': {
      const status = (e.is_error ?? false) ? 'error' as const : 'done' as const
      const i = n.items.findLastIndex(it => it.kind === 'tool' && it.callId === e.call_id)
      if (i >= 0) {
        n.items[i] = {
          ...(n.items[i] as ToolItem), status, output: e.output,
          durationMs: e.duration_ms ?? 0, diffStats: (e.diff_stats as DiffStats | null) ?? null,
        }
      } else if (!n.items.some(it => it.kind === 'gate' && it.callId === e.call_id && it.denied)) {
        // finished-without-started (contract #2); denied gates already tell the story
        n.items.push({
          kind: 'tool', seq, callId: e.call_id, tool: e.tool, display: e.tool,
          status, output: e.output, durationMs: e.duration_ms ?? 0,
          diffStats: (e.diff_stats as DiffStats | null) ?? null, autoApproved: false,
        })
      }
      break
    }

    case 'approval_requested':
      n.items.push({ kind: 'gate', seq, callId: e.call_id, tool: e.tool, display: e.display, denied: false })
      break

    case 'approval_resolved': {
      const i = n.items.findLastIndex(it => it.kind === 'gate' && it.callId === e.call_id)
      if (i >= 0) {
        if (e.decision === 'allow') n.items.splice(i, 1) // gate collapses into the tool card that follows
        else n.items[i] = { ...(n.items[i] as GateItem), denied: true }
      }
      break
    }

    case 'context_compacted':
      n.items.push({ kind: 'compacted', seq })
      break

    case 'run_finished':
      finalizeProse(n.items)
      n.status = 'idle' // contract #3: rehydrate emits no status_changed
      if (e.reason === 'cancelled') n.items.push({ kind: 'info', seq, text: 'Run cancelled' })
      if (e.reason === 'interrupted') n.items.push({ kind: 'info', seq, text: 'Interrupted by server restart' })
      break

    case 'error':
      n.items.push({ kind: 'error', seq, message: e.message })
      break

    case 'policy_added':
      break // no stream item; the allowed tool card carries the story
  }
  return n
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pnpm test src/state/reducer.test.ts`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/state/reducer.ts src/state/reducer.test.ts
git commit -m "feat: projection reducer core (durable events, dedupe, tool lifecycle)"
```

---

### Task 7: Projection reducer — streaming deltas, approvals, run lifecycle

Locks the remaining reducer behavior with tests: text deltas, approval allow/deny flows, denied-gate suppression, compaction marker, run_finished reasons, errors.

**Files:**
- Modify: `web/src/state/reducer.ts` (only if a test exposes a gap — the Task 6 file is intended to be complete)
- Test: `web/src/state/reducer.test.ts` (append)

**Interfaces:** unchanged from Task 6.

- [ ] **Step 1: Write the tests**

Append to `web/src/state/reducer.test.ts` (reuses the `ev`/`eph`/`run` helpers):

```ts
describe('reducer: streaming text (contract #4)', () => {
  it('deltas accumulate, final assistant_message replaces them', () => {
    seq = 0
    const s = run([
      eph('text_delta', { text: 'Wor' }),
      eph('text_delta', { text: 'king…' }),
      ev('assistant_message', { text: 'Working on it, done.', tool_calls: [] }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'prose', text: 'Working on it, done.', streaming: false })
  })

  it('deltas followed by a tool-only final leave no empty prose', () => {
    seq = 0
    const s = run([
      eph('text_delta', { text: 'hmm' }),
      ev('assistant_message', { text: '', tool_calls: [{ id: 'c1', name: 'bash', arguments: '{}' }] }),
    ])
    expect(s.items).toHaveLength(0)
  })

  it('a new turn starts a new prose item', () => {
    seq = 0
    const s = run([
      ev('assistant_message', { text: 'first', tool_calls: [] }),
      eph('text_delta', { text: 'second…' }),
    ])
    expect(s.items).toHaveLength(2)
    expect(s.items[1]).toMatchObject({ kind: 'prose', text: 'second…', streaming: true })
  })
})

describe('reducer: approvals', () => {
  it('requested → allow: gate disappears, tool card follows', () => {
    seq = 0
    const s = run([
      ev('approval_requested', { call_id: 'c1', tool: 'bash', display: 'rm -rf build' }),
      ev('approval_resolved', { call_id: 'c1', decision: 'allow' }),
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'rm -rf build' }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: 'ok' }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'tool', status: 'done' })
  })

  it('requested → deny: gate stays denied, denial result event is suppressed', () => {
    seq = 0
    const s = run([
      ev('approval_requested', { call_id: 'c1', tool: 'bash', display: 'rm -rf /' }),
      ev('approval_resolved', { call_id: 'c1', decision: 'deny' }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: 'User denied this action.', is_error: true }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'gate', denied: true, display: 'rm -rf /' })
  })
})

describe('reducer: run lifecycle', () => {
  it('run_finished(completed) sets idle silently', () => {
    seq = 0
    const s = run([
      ev('status_changed', { status: 'running' }),
      ev('run_finished', { reason: 'completed' }),
    ])
    expect(s.status).toBe('idle')
    expect(s.items).toHaveLength(0)
  })

  it('run_finished(interrupted) sets idle and notes it (contract #3)', () => {
    seq = 0
    const s = run([
      ev('status_changed', { status: 'running' }),
      ev('run_finished', { reason: 'interrupted' }),
    ])
    expect(s.status).toBe('idle')
    expect(s.items[0]).toMatchObject({ kind: 'info', text: 'Interrupted by server restart' })
  })

  it('cancelled adds an info line; error events render', () => {
    seq = 0
    const s = run([
      ev('error', { message: 'LLM connection failed' }),
      ev('run_finished', { reason: 'error' }),
      ev('run_finished', { reason: 'cancelled' }),
    ])
    expect(s.items[0]).toMatchObject({ kind: 'error', message: 'LLM connection failed' })
    expect(s.items[1]).toMatchObject({ kind: 'info', text: 'Run cancelled' })
  })

  it('context_compacted adds a divider; steps count tool calls per turn', () => {
    seq = 0
    const s = run([
      ev('user_message', { text: 'go' }),
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'a' }),
      ev('tool_call_started', { call_id: 'c2', tool: 'bash', display: 'b' }),
      ev('context_compacted', { summary: 'sum', upto_seq: 3 }),
      ev('user_message', { text: 'more' }),
    ])
    expect(s.items.map(i => i.kind)).toEqual(['user', 'tool', 'tool', 'compacted', 'user'])
    expect(s.steps).toBe(0) // reset by the second user message
  })
})
```

- [ ] **Step 2: Run tests**

Run: `pnpm test src/state/reducer.test.ts`
Expected: PASS if Task 6's implementation was complete; otherwise fix `reducer.ts` until green (the tests are the specification — do not weaken them).

- [ ] **Step 3: Commit**

```bash
git add src/state/reducer.ts src/state/reducer.test.ts
git commit -m "test: reducer streaming, approval, and run-lifecycle contracts"
```

---

### Task 8: REST client + Zustand store

**Files:**
- Create: `web/src/api.ts`, `web/src/state/store.ts`
- Test: `web/src/state/store.test.ts`

**Interfaces:**
- Produces `web/src/api.ts`:

```ts
export class ApiError extends Error { status: number }
export const api: {
  health(): Promise<{ ok: boolean }>
  models(): Promise<ModelInfo[]>
  sessions(): Promise<SessionMeta[]>
  createSession(body?: { cwd?: string; model?: string; autonomy?: string }): Promise<SessionMeta>
  sendMessage(sid: string, text: string): Promise<void>
  resolveApproval(sid: string, callId: string, decision: 'allow' | 'deny',
                  always?: { pattern: string; scope: 'session' | 'global' }): Promise<void>
  cancel(sid: string): Promise<void>
  setAutonomy(sid: string, autonomy: Autonomy): Promise<void>
  setModel(sid: string, model: string): Promise<void>
  compact(sid: string): Promise<void>          // throws ApiError(409) while running
  rename(sid: string, name: string): Promise<void>
  changesets(sid: string): Promise<Changeset[]>
  revert(sid: string, index: number): Promise<void>
  keepAll(sid: string): Promise<void>
  changesetFile(sid: string, index: number): Promise<{ path: string; content: string }>
  searchFiles(sid: string, q: string): Promise<string[]>
}
```

- Produces `web/src/state/store.ts` (consumed by every component and Task 9):

```ts
export interface DrawerState { open: boolean; changesetIndex: number; view: 'diff' | 'file' | 'blame' }
export interface SessionState {
  id: string
  stream: SessionStream
  drawer: DrawerState
  changesets: Changeset[]
  fileContent: string | null   // drawer File-view cache for the current changeset
}
export interface ForgeState {
  sessions: Record<string, SessionState>
  order: string[]              // creation order, drives the tab row
  activeId: string | null
  models: ModelInfo[]
  healthy: boolean
  connection: 'connecting' | 'open' | 'closed'
  // pure state transitions
  upsertSession(id: string, seed?: SessionMeta): void
  applyEvent(e: WireEvent): void
  setActive(id: string): void
  setConnection(c: ForgeState['connection']): void
  // commands (REST + local state)
  hydrate(): Promise<void>
  newSession(): Promise<void>
  send(text: string): Promise<void>            // active session
  openDrawer(changesetIndex: number): Promise<void>
  setDrawerView(view: DrawerState['view']): Promise<void>
  closeDrawer(): void
  stepDrawer(delta: 1 | -1): Promise<void>
  revert(): Promise<void>
  keepAll(): Promise<void>
  refreshHealth(): Promise<void>
}
export const useForge: UseBoundStore<StoreApi<ForgeState>>
export function cursors(state: ForgeState): Record<string, number>
```

- [ ] **Step 1: Write `api.ts`**

```ts
import type { Autonomy, Changeset, ModelInfo, SessionMeta } from './protocol'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init)
  if (!r.ok) throw new ApiError(r.status, `${init?.method ?? 'GET'} ${path} → ${r.status}`)
  return r.json() as Promise<T>
}

const post = <T,>(path: string, body?: object) =>
  req<T>(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })

export const api = {
  health: () => req<{ ok: boolean }>('/api/health'),
  models: () => req<ModelInfo[]>('/api/models'),
  sessions: () => req<SessionMeta[]>('/api/sessions'),
  createSession: (body: { cwd?: string; model?: string; autonomy?: string } = {}) =>
    post<SessionMeta>('/api/sessions', body),
  sendMessage: (sid: string, text: string) =>
    post<object>(`/api/sessions/${sid}/messages`, { text }).then(() => undefined),
  resolveApproval: (
    sid: string, callId: string, decision: 'allow' | 'deny',
    always?: { pattern: string; scope: 'session' | 'global' },
  ) => post<object>(`/api/sessions/${sid}/approvals/${callId}`, { decision, always }).then(() => undefined),
  cancel: (sid: string) => post<object>(`/api/sessions/${sid}/cancel`).then(() => undefined),
  setAutonomy: (sid: string, autonomy: Autonomy) =>
    post<object>(`/api/sessions/${sid}/autonomy`, { autonomy }).then(() => undefined),
  setModel: (sid: string, model: string) =>
    post<object>(`/api/sessions/${sid}/model`, { model }).then(() => undefined),
  compact: (sid: string) => post<object>(`/api/sessions/${sid}/compact`).then(() => undefined),
  rename: (sid: string, name: string) =>
    req<object>(`/api/sessions/${sid}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name }),
    }).then(() => undefined),
  changesets: (sid: string) => req<Changeset[]>(`/api/sessions/${sid}/changesets`),
  revert: (sid: string, index: number) =>
    post<object>(`/api/sessions/${sid}/changesets/${index}/revert`).then(() => undefined),
  keepAll: (sid: string) => post<object>(`/api/sessions/${sid}/changesets/keep_all`).then(() => undefined),
  changesetFile: (sid: string, index: number) =>
    req<{ path: string; content: string }>(`/api/sessions/${sid}/changesets/${index}/file`),
  searchFiles: (sid: string, q: string) =>
    req<string[]>(`/api/sessions/${sid}/files?q=${encodeURIComponent(q)}`),
}
```

- [ ] **Step 2: Write the failing store tests**

`web/src/state/store.test.ts` — test the pure transitions and the REST-driven actions with a stubbed `fetch`:

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cursors, useForge } from './store'
import type { WireEvent } from '../protocol'

const ev = (type: string, sid: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: sid, ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  vi.restoreAllMocks()
})

describe('store', () => {
  it('applyEvent routes to the right session, creating it on demand', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'one', cwd: '/w', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('user_message', 'aa', 2, { text: 'hi' }))
    const s = useForge.getState()
    expect(s.order).toEqual(['aa'])
    expect(s.sessions['aa'].stream.items).toHaveLength(1)
    expect(s.activeId).toBe('aa') // first session becomes active
  })

  it('cursors reports lastSeq per session', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('user_message', 'aa', 2, { text: 'x' }))
    expect(cursors(useForge.getState())).toEqual({ aa: 2 })
  })

  it('hydrate seeds sessions from REST and loads models/health', async () => {
    const meta = { id: 'aa', name: 'restored', cwd: '/w', model: 'm1', autonomy: 'guarded', status: 'idle' }
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
      ok: true,
      json: async () =>
        url.includes('/models') ? [{ id: 'm1', display_name: 'Model One', context_window: 1 }]
        : url.includes('/health') ? { ok: true }
        : [meta],
    })))
    await useForge.getState().hydrate()
    const s = useForge.getState()
    expect(s.order).toEqual(['aa'])
    expect(s.sessions['aa'].stream).toMatchObject({ name: 'restored', model: 'm1', autonomy: 'guarded', status: 'idle' })
    expect(s.models[0].display_name).toBe('Model One')
    expect(s.healthy).toBe(true)
  })

  it('openDrawer fetches changesets and sets state; closeDrawer keeps index', async () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => [{ index: 0, path: '/w/a.py', added: 1, removed: 0, diff: '', status: 'pending' }],
    })))
    await useForge.getState().openDrawer(0)
    let s = useForge.getState().sessions['aa']
    expect(s.drawer).toMatchObject({ open: true, changesetIndex: 0, view: 'diff' })
    expect(s.changesets).toHaveLength(1)
    useForge.getState().closeDrawer()
    s = useForge.getState().sessions['aa']
    expect(s.drawer.open).toBe(false)
  })
})
```

Run: `pnpm test src/state/store.test.ts` → FAIL (module missing).

- [ ] **Step 3: Implement the store**

`web/src/state/store.ts`:

```ts
import { create } from 'zustand'
import { api } from '../api'
import type { Changeset, ModelInfo, SessionMeta, WireEvent } from '../protocol'
import { emptyStream, reduce, type SessionStream } from './reducer'

export interface DrawerState {
  open: boolean
  changesetIndex: number
  view: 'diff' | 'file' | 'blame'
}

export interface SessionState {
  id: string
  stream: SessionStream
  drawer: DrawerState
  changesets: Changeset[]
  fileContent: string | null
}

function newSessionState(id: string): SessionState {
  return {
    id, stream: emptyStream(),
    drawer: { open: false, changesetIndex: 0, view: 'diff' },
    changesets: [], fileContent: null,
  }
}

function seedFromMeta(state: SessionState, meta: SessionMeta): SessionState {
  return {
    ...state,
    stream: {
      ...state.stream,
      name: meta.name ?? state.stream.name,
      cwd: meta.cwd, model: meta.model,
      autonomy: meta.autonomy ?? 'yolo',
      status: meta.status ?? 'idle',
    },
  }
}

export interface ForgeState {
  sessions: Record<string, SessionState>
  order: string[]
  activeId: string | null
  models: ModelInfo[]
  healthy: boolean
  connection: 'connecting' | 'open' | 'closed'
  upsertSession(id: string, seed?: SessionMeta): void
  applyEvent(e: WireEvent): void
  setActive(id: string): void
  setConnection(c: ForgeState['connection']): void
  hydrate(): Promise<void>
  newSession(): Promise<void>
  send(text: string): Promise<void>
  openDrawer(changesetIndex: number): Promise<void>
  setDrawerView(view: DrawerState['view']): Promise<void>
  closeDrawer(): void
  stepDrawer(delta: 1 | -1): Promise<void>
  revert(): Promise<void>
  keepAll(): Promise<void>
  refreshHealth(): Promise<void>
}

export const useForge = create<ForgeState>()((set, get) => {
  const patchSession = (id: string, patch: Partial<SessionState>) =>
    set(s => ({ sessions: { ...s.sessions, [id]: { ...s.sessions[id], ...patch } } }))

  const active = () => {
    const { activeId, sessions } = get()
    return activeId ? sessions[activeId] : undefined
  }

  const loadDrawerFile = async (id: string, index: number, view: string) => {
    if (view !== 'file') return
    const { content } = await api.changesetFile(id, index)
    patchSession(id, { fileContent: content })
  }

  return {
    sessions: {}, order: [], activeId: null,
    models: [], healthy: false, connection: 'connecting',

    upsertSession: (id, seed) =>
      set(s => {
        const existing = s.sessions[id]
        let session = existing ?? newSessionState(id)
        if (seed) session = seedFromMeta(session, seed)
        return {
          sessions: { ...s.sessions, [id]: session },
          order: existing ? s.order : [...s.order, id],
          activeId: s.activeId ?? id,
        }
      }),

    applyEvent: e => {
      get().upsertSession(e.session_id)
      set(s => {
        const session = s.sessions[e.session_id]
        return {
          sessions: {
            ...s.sessions,
            [e.session_id]: { ...session, stream: reduce(session.stream, e) },
          },
        }
      })
    },

    setActive: id => set({ activeId: id }),
    setConnection: connection => set({ connection }),

    hydrate: async () => {
      const [metas, models, health] = await Promise.all([
        api.sessions(), api.models(), api.health(),
      ])
      for (const m of metas) get().upsertSession(m.id, m)
      set({ models, healthy: health.ok })
    },

    newSession: async () => {
      const meta = await api.createSession()
      get().upsertSession(meta.id, meta)
      set({ activeId: meta.id })
    },

    send: async text => {
      const a = active()
      if (a && text.trim()) await api.sendMessage(a.id, text)
    },

    openDrawer: async changesetIndex => {
      const a = active()
      if (!a) return
      const changesets = await api.changesets(a.id)
      patchSession(a.id, {
        changesets, fileContent: null,
        drawer: { open: true, changesetIndex, view: 'diff' },
      })
    },

    setDrawerView: async view => {
      const a = active()
      if (!a) return
      patchSession(a.id, { drawer: { ...a.drawer, view } })
      await loadDrawerFile(a.id, a.drawer.changesetIndex, view)
    },

    closeDrawer: () => {
      const a = active()
      if (a) patchSession(a.id, { drawer: { ...a.drawer, open: false } })
    },

    stepDrawer: async delta => {
      const a = active()
      if (!a || a.changesets.length === 0) return
      const n = a.changesets.length
      const changesetIndex = (a.drawer.changesetIndex + delta + n) % n
      patchSession(a.id, { drawer: { ...a.drawer, changesetIndex }, fileContent: null })
      await loadDrawerFile(a.id, changesetIndex, a.drawer.view)
    },

    revert: async () => {
      const a = active()
      if (!a) return
      await api.revert(a.id, a.drawer.changesetIndex)
      patchSession(a.id, { changesets: await api.changesets(a.id) })
    },

    keepAll: async () => {
      const a = active()
      if (!a) return
      await api.keepAll(a.id)
      patchSession(a.id, { changesets: await api.changesets(a.id) })
    },

    refreshHealth: async () => {
      try {
        set({ healthy: (await api.health()).ok })
      } catch {
        set({ healthy: false })
      }
    },
  }
})

export function cursors(state: ForgeState): Record<string, number> {
  return Object.fromEntries(state.order.map(id => [id, state.sessions[id].stream.lastSeq]))
}
```

- [ ] **Step 4: Run tests**

Run: `pnpm test` (whole suite)
Expected: all PASS, `pnpm build` clean.

- [ ] **Step 5: Commit**

```bash
git add src/api.ts src/state/store.ts src/state/store.test.ts
git commit -m "feat: REST client and zustand store"
```

---

### Task 9: WebSocket client — cursors, replay, reconnect

**Files:**
- Create: `web/src/ws.ts`
- Test: `web/src/ws.test.ts`

**Interfaces:**
- Produces (consumed by App in Task 17):

```ts
export interface WsOptions {
  url: string
  cursors(): Record<string, number>
  onEvent(e: WireEvent): void
  onStatus(s: 'connecting' | 'open' | 'closed'): void
  minDelayMs?: number   // reconnect backoff floor, default 500 (tests use 1)
}
export function startWs(opts: WsOptions): () => void   // returns stop()
```

Behavior (contract #5): on open, immediately send `{"cursors": …}` computed at that moment (so reconnects replay only the gap); parse each frame as one `WireEvent`; on close, reconnect with doubling backoff capped at 8s unless `stop()` was called. Dedupe is NOT this module's job — the reducer drops replayed seqs.

- [ ] **Step 1: Write the failing tests**

`web/src/ws.test.ts` with a fake `WebSocket`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { startWs } from './ws'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
  }
  send(data: string) { this.sent.push(data) }
  close() { this.onclose?.() }
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.useFakeTimers()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('startWs', () => {
  it('sends cursors as the first frame on open', () => {
    const stop = startWs({
      url: 'ws://x/ws', cursors: () => ({ aa: 7 }),
      onEvent: () => {}, onStatus: () => {},
    })
    const ws = FakeWebSocket.instances[0]
    ws.onopen!()
    expect(JSON.parse(ws.sent[0])).toEqual({ cursors: { aa: 7 } })
    stop()
  })

  it('parses frames into events', () => {
    const events: unknown[] = []
    const stop = startWs({
      url: 'ws://x/ws', cursors: () => ({}),
      onEvent: e => events.push(e), onStatus: () => {},
    })
    const ws = FakeWebSocket.instances[0]
    ws.onopen!()
    ws.onmessage!({ data: '{"type":"user_message","session_id":"aa","seq":1,"ts":0,"text":"hi"}' })
    expect(events[0]).toMatchObject({ type: 'user_message', text: 'hi' })
    stop()
  })

  it('reconnects after close with fresh cursors, and stop() ends it', () => {
    let seq = 3
    const statuses: string[] = []
    const stop = startWs({
      url: 'ws://x/ws', cursors: () => ({ aa: seq }),
      onEvent: () => {}, onStatus: s => statuses.push(s), minDelayMs: 1,
    })
    const first = FakeWebSocket.instances[0]
    first.onopen!()
    seq = 9
    first.onclose!()                    // dropped connection
    vi.advanceTimersByTime(50)          // past backoff
    expect(FakeWebSocket.instances).toHaveLength(2)
    const second = FakeWebSocket.instances[1]
    second.onopen!()
    expect(JSON.parse(second.sent[0])).toEqual({ cursors: { aa: 9 } })
    expect(statuses).toEqual(['connecting', 'open', 'closed', 'connecting', 'open'])
    stop()
    second.onclose!()
    vi.advanceTimersByTime(60_000)
    expect(FakeWebSocket.instances).toHaveLength(2)  // no zombie reconnect
  })
})
```

Run: `pnpm test src/ws.test.ts` → FAIL.

- [ ] **Step 2: Implement**

`web/src/ws.ts`:

```ts
import type { WireEvent } from './protocol'

export interface WsOptions {
  url: string
  cursors(): Record<string, number>
  onEvent(e: WireEvent): void
  onStatus(s: 'connecting' | 'open' | 'closed'): void
  minDelayMs?: number
}

export function startWs(opts: WsOptions): () => void {
  const min = opts.minDelayMs ?? 500
  let delay = min
  let stopped = false
  let ws: WebSocket | null = null

  const connect = () => {
    opts.onStatus('connecting')
    ws = new WebSocket(opts.url)
    ws.onopen = () => {
      delay = min
      // Contract #5: the server blocks until it receives the cursor map.
      ws!.send(JSON.stringify({ cursors: opts.cursors() }))
      opts.onStatus('open')
    }
    ws.onmessage = ev => opts.onEvent(JSON.parse(ev.data as string) as WireEvent)
    ws.onclose = () => {
      opts.onStatus('closed')
      if (stopped) return
      setTimeout(connect, delay)
      delay = Math.min(delay * 2, 8000)
    }
  }

  connect()
  return () => {
    stopped = true
    ws?.close()
  }
}
```

- [ ] **Step 3: Run tests**

Run: `pnpm test`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/ws.ts src/ws.test.ts
git commit -m "feat: websocket client with cursor replay and reconnect"
```

---

### Task 10: TopBar — brand, session tabs, queue pill, cwd

Handoff §"Top bar": h 52, bar gradient, brand tile, segmented session tabs with status dots, `+` button, queue pill, working directory.

**Files:**
- Create: `web/src/components/TopBar.tsx`, `web/src/components/TopBar.module.css`
- Test: `web/src/components/TopBar.test.tsx`

**Interfaces:**
- Consumes `useForge` (order, sessions, activeId, setActive, newSession).
- Status-dot rule: active tab → ACCENT with glow; inactive tabs → `#e0b34b` when the session's status is `running`/`queued`/`attention`, `#3d3d47` when `idle`. Queue pill shows `{n} queued` where n = sessions with status `queued`; hidden when 0. cwd = active session's cwd with `$HOME` shown as `~` (string prefix replace is not possible client-side without knowing home — abbreviate any leading `/Users/<name>` or `/home/<name>` to `~`).

- [ ] **Step 1: Write the failing tests**

`web/src/components/TopBar.test.tsx`:

```tsx
import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import TopBar from './TopBar'

const ev = (type: string, sid: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: sid, ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  const { applyEvent } = useForge.getState()
  applyEvent(ev('session_created', 'aa', 1, { name: 'fix the bug', cwd: '/Users/louis/mygent', model: 'm', autonomy: 'yolo' }))
  applyEvent(ev('session_created', 'bb', 1, { name: 'write docs', cwd: '/w', model: 'm', autonomy: 'yolo' }))
  applyEvent(ev('status_changed', 'bb', 2, { status: 'queued' }))
})

describe('TopBar', () => {
  it('renders a tab per session, active first', () => {
    render(<TopBar />)
    expect(screen.getByRole('tab', { name: /fix the bug/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /write docs/ })).toHaveAttribute('aria-selected', 'false')
  })

  it('clicking a tab switches the active session', async () => {
    render(<TopBar />)
    await userEvent.click(screen.getByRole('tab', { name: /write docs/ }))
    expect(useForge.getState().activeId).toBe('bb')
  })

  it('shows the queue pill count and the active cwd abbreviated', () => {
    render(<TopBar />)
    expect(screen.getByText('1 queued')).toBeInTheDocument()
    expect(screen.getByText('~/mygent')).toBeInTheDocument()
  })

  it('hides the queue pill when nothing is queued', () => {
    useForge.getState().applyEvent(ev('status_changed', 'bb', 3, { status: 'idle' }))
    render(<TopBar />)
    expect(screen.queryByText(/queued/)).not.toBeInTheDocument()
  })
})
```

Run: `pnpm test src/components/TopBar.test.tsx` → FAIL.

- [ ] **Step 2: Implement**

`web/src/components/TopBar.tsx`:

```tsx
import { useForge } from '../state/store'
import s from './TopBar.module.css'

function abbreviate(cwd: string): string {
  return cwd.replace(/^\/(Users|home)\/[^/]+/, '~')
}

export default function TopBar() {
  const order = useForge(st => st.order)
  const sessions = useForge(st => st.sessions)
  const activeId = useForge(st => st.activeId)
  const setActive = useForge(st => st.setActive)
  const newSession = useForge(st => st.newSession)

  const queued = order.filter(id => sessions[id].stream.status === 'queued').length
  const cwd = activeId ? sessions[activeId].stream.cwd : ''

  return (
    <header className={s.bar}>
      <div className={s.brand}>
        <div className={s.logo} />
        <span className={s.name}>Forge</span>
      </div>
      <div className={s.tabs} role="tablist">
        {order.map(id => {
          const st = sessions[id].stream
          const active = id === activeId
          const busy = st.status !== 'idle'
          return (
            <button
              key={id}
              role="tab"
              aria-selected={active}
              className={active ? s.tabActive : s.tab}
              onClick={() => setActive(id)}
            >
              <span
                className={s.dot}
                data-state={active ? 'active' : busy ? 'busy' : 'idle'}
              />
              {st.name}
            </button>
          )
        })}
        <button className={s.plus} aria-label="New session" onClick={() => void newSession()}>
          +
        </button>
      </div>
      <div className={s.right}>
        {queued > 0 && (
          <span className={s.queuePill}>
            <span className={s.queueDot} />
            {queued} queued
          </span>
        )}
        <span className={s.cwd}>{abbreviate(cwd)}</span>
      </div>
    </header>
  )
}
```

`web/src/components/TopBar.module.css`:

```css
.bar {
  height: 52px;
  flex: none;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 18px;
  background: var(--bg-bar);
  border-bottom: 1px solid var(--hair-2);
}

.brand { display: flex; align-items: center; gap: 9px; }
.logo {
  width: 20px; height: 20px; border-radius: 6px;
  background: linear-gradient(135deg, var(--accent), color-mix(in oklab, var(--accent) 60%, #0a0a0c));
  box-shadow: 0 0 12px color-mix(in oklab, var(--accent) 30%, transparent);
}
.name { font-size: 13.5px; font-weight: 600; letter-spacing: -0.01em; }

.tabs {
  display: flex; align-items: center;
  background: rgba(255, 255, 255, 0.04);
  border-radius: var(--r-seg);
  padding: 3px;
}
.tab, .tabActive {
  display: flex; align-items: center; gap: 7px;
  padding: 5px 12px;
  font-size: 12px; font-weight: 500;
  border-radius: var(--r-btn);
  border: 1px solid transparent;
  color: var(--text-secondary);
  max-width: 180px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.tabActive {
  background: var(--bg-raised-1);
  border-color: var(--hair-4);
  color: var(--text-primary);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
}
.tab:hover { border-color: var(--hair-hover); }

.dot { width: 6px; height: 6px; border-radius: 50%; flex: none; }
.dot[data-state='active'] { background: var(--accent); box-shadow: 0 0 6px var(--accent); }
.dot[data-state='busy'] { background: var(--warn-dot); }
.dot[data-state='idle'] { background: #3d3d47; }

.plus { color: var(--text-faint); padding: 5px 10px; font-size: 13px; }
.plus:hover { color: var(--text-secondary); }

.right { margin-left: auto; display: flex; align-items: center; gap: 14px; }
.queuePill {
  display: flex; align-items: center; gap: 6px;
  font-size: 11.5px; color: var(--text-secondary);
  border: 1px solid var(--hair-3);
  border-radius: var(--r-pill);
  padding: 4px 10px;
}
.queueDot { width: 5px; height: 5px; border-radius: 50%; background: var(--warn-dot); }
.cwd { font-family: var(--font-mono); font-size: 11.5px; color: var(--text-faint); }
```

- [ ] **Step 3: Run tests**

Run: `pnpm test`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/components/TopBar.tsx src/components/TopBar.module.css src/components/TopBar.test.tsx
git commit -m "feat: top bar with session tabs, queue pill, cwd"
```

---

### Task 11: ToolCard

Handoff §"Tool card": header with icon tile / display / diff stats / right meta; mono body with tail truncation; "open panel →" for diff-bearing cards. Design ambiguity resolved (record in the report, do not re-litigate): the handoff's separate "auto-approved line" is rendered as right-meta text `auto-approved` on the tool card itself — the engine emits one `tool_call_started(auto_approved=true)` per call, not a separate gate event, so a separate line would duplicate every card. Error state (not drawn in the handoff): `!` glyph, tile bg `var(--danger-bg)`, glyph `var(--danger)`, following the tile pattern.

**Files:**
- Create: `web/src/components/ToolCard.tsx`, `web/src/components/ToolCard.module.css`
- Test: `web/src/components/ToolCard.test.tsx`

**Interfaces:**
- Props (pure component; ChatStream wires the store):

```tsx
interface ToolCardProps {
  item: Extract<StreamItem, { kind: 'tool' }>
  onOpenPanel(changesetIndex: number): void
}
```

- Output body: hidden when output is empty or `(no output)`. When >12 lines, show a truncation line `… {n-12} earlier lines` (ghost color) then the last 12. While running, show the live tail the same way.

- [ ] **Step 1: Write the failing tests**

`web/src/components/ToolCard.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { StreamItem } from '../state/reducer'
import ToolCard from './ToolCard'

type Tool = Extract<StreamItem, { kind: 'tool' }>
const base: Tool = {
  kind: 'tool', seq: 1, callId: 'c1', tool: 'bash', display: 'pytest -q',
  status: 'running', output: '', durationMs: 0, diffStats: null, autoApproved: false,
}

describe('ToolCard', () => {
  it('running: shows ▸ and the display line, no meta yet', () => {
    render(<ToolCard item={base} onOpenPanel={() => {}} />)
    expect(screen.getByText('▸')).toBeInTheDocument()
    expect(screen.getByText('pytest -q')).toBeInTheDocument()
  })

  it('done: shows ✓, duration, and auto-approved meta', () => {
    render(<ToolCard item={{ ...base, status: 'done', durationMs: 1240, autoApproved: true, output: '3 passed' }} onOpenPanel={() => {}} />)
    expect(screen.getByText('✓')).toBeInTheDocument()
    expect(screen.getByText('1.2s')).toBeInTheDocument()
    expect(screen.getByText('auto-approved')).toBeInTheDocument()
    expect(screen.getByText('3 passed')).toBeInTheDocument()
  })

  it('error: shows ! and the output', () => {
    render(<ToolCard item={{ ...base, status: 'error', output: 'boom' }} onOpenPanel={() => {}} />)
    expect(screen.getByText('!')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
  })

  it('truncates long output to the last 12 lines', () => {
    const output = Array.from({ length: 20 }, (_, i) => `line${i + 1}`).join('\n')
    render(<ToolCard item={{ ...base, status: 'done', output }} onOpenPanel={() => {}} />)
    expect(screen.getByText('… 8 earlier lines')).toBeInTheDocument()
    expect(screen.getByText(/line20/)).toBeInTheDocument()
    expect(screen.queryByText(/line1$/m)).not.toBeInTheDocument()
  })

  it('diff card: stats chips and open panel →', async () => {
    const onOpen = vi.fn()
    render(<ToolCard
      item={{ ...base, tool: 'edit_file', display: 'app.py', status: 'done',
              diffStats: { path: '/w/app.py', added: 41, removed: 38, changeset_index: 2 } }}
      onOpenPanel={onOpen}
    />)
    expect(screen.getByText('+41')).toBeInTheDocument()
    expect(screen.getByText('−38')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /open panel/ }))
    expect(onOpen).toHaveBeenCalledWith(2)
  })

  it('hides the body for (no output)', () => {
    render(<ToolCard item={{ ...base, status: 'done', output: '(no output)' }} onOpenPanel={() => {}} />)
    expect(screen.queryByText('(no output)')).not.toBeInTheDocument()
  })
})
```

Run → FAIL.

- [ ] **Step 2: Implement**

`web/src/components/ToolCard.tsx`:

```tsx
import type { StreamItem } from '../state/reducer'
import s from './ToolCard.module.css'

const TAIL = 12

function fmtDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

export default function ToolCard({
  item,
  onOpenPanel,
}: {
  item: Extract<StreamItem, { kind: 'tool' }>
  onOpenPanel(changesetIndex: number): void
}) {
  const glyph = item.status === 'running' ? '▸' : item.status === 'done' ? '✓' : '!'
  const output = item.output === '(no output)' ? '' : item.output
  const lines = output ? output.replace(/\n$/, '').split('\n') : []
  const hidden = Math.max(0, lines.length - TAIL)
  const shown = lines.slice(-TAIL)

  return (
    <div className={s.card}>
      <div className={s.header} data-body={shown.length > 0}>
        <span className={s.tile} data-status={item.status}>{glyph}</span>
        <span className={s.display}>{item.display}</span>
        {item.diffStats && (
          <span className={s.stats}>
            <span className={s.added}>+{item.diffStats.added}</span>
            <span className={s.removed}>−{item.diffStats.removed}</span>
          </span>
        )}
        <span className={s.meta}>
          {item.diffStats && (
            <button className={s.openPanel}
                    onClick={() => onOpenPanel(item.diffStats!.changeset_index)}>
              open panel →
            </button>
          )}
          {item.autoApproved && <span>auto-approved</span>}
          {item.status !== 'running' && item.durationMs > 0 && (
            <span>{fmtDuration(item.durationMs)}</span>
          )}
        </span>
      </div>
      {shown.length > 0 && (
        <pre className={s.body}>
          {hidden > 0 && <div className={s.truncated}>… {hidden} earlier lines</div>}
          {shown.join('\n')}
        </pre>
      )}
    </div>
  )
}
```

`web/src/components/ToolCard.module.css`:

```css
.card {
  background: var(--bg-card);
  border: 1px solid var(--hair-3);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-card);
}

.header {
  display: flex; align-items: center; gap: 9px;
  padding: 9px 13px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
}
.header[data-body='true'] { border-bottom: 1px solid var(--hair-1); }

.tile {
  width: 18px; height: 18px; flex: none;
  display: grid; place-items: center;
  border-radius: var(--r-tile);
  background: color-mix(in oklab, var(--accent) 13%, transparent);
  color: var(--accent);
  font-size: 10px;
}
.tile[data-status='error'] { background: var(--danger-bg); color: var(--danger); }

.display {
  color: var(--text-primary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

.stats { display: flex; gap: 7px; flex: none; }
.added { color: var(--ok); }
.removed { color: var(--danger); }

.meta {
  margin-left: auto; flex: none;
  display: flex; align-items: center; gap: 10px;
  color: var(--text-ghost);
}
.openPanel { color: var(--text-secondary); font-family: var(--font-mono); font-size: 11px; }
.openPanel:hover { color: var(--text-primary); }

.body {
  padding: 10px 15px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.75;
  color: var(--text-muted);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}
.truncated { color: var(--text-ghost); }
```

- [ ] **Step 3: Run tests**

Run: `pnpm test`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/components/ToolCard.tsx src/components/ToolCard.module.css src/components/ToolCard.test.tsx
git commit -m "feat: tool card (running/done/error, output tail, diff stats, open panel)"
```

---

### Task 12: ApprovalGate

Handoff §"Approval gate": amber-tinted card with ⚠ tile, title, mono command, Allow / Deny / Always ⌄. Denied state (after `approval_resolved(deny)`): the gate collapses to a single header-style row `✕ Denied · <display>`.

**Files:**
- Create: `web/src/components/ApprovalGate.tsx`, `web/src/components/ApprovalGate.module.css`
- Test: `web/src/components/ApprovalGate.test.tsx`

**Interfaces:**
- Props (pure; ChatStream wires REST):

```tsx
interface ApprovalGateProps {
  item: Extract<StreamItem, { kind: 'gate' }>
  onResolve(decision: 'allow' | 'deny', always?: { pattern: string; scope: 'session' | 'global' }): void
}
```

- "Always ⌄" opens a dropdown with exactly three options (pattern semantics: engine matches `fnmatch(display, pattern)`):
  1. `Always allow this command (session)` → `{ pattern: item.display, scope: 'session' }`
  2. `Always allow ${item.tool} (session)` → `{ pattern: '*', scope: 'session' }`
  3. `Always allow ${item.tool} (global)` → `{ pattern: '*', scope: 'global' }`
  Picking one resolves with `decision: 'allow'` plus that policy.

- [ ] **Step 1: Write the failing tests**

`web/src/components/ApprovalGate.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { StreamItem } from '../state/reducer'
import ApprovalGate from './ApprovalGate'

type Gate = Extract<StreamItem, { kind: 'gate' }>
const gate: Gate = {
  kind: 'gate', seq: 5, callId: 'c1', tool: 'bash', display: 'rm -rf build', denied: false,
}

describe('ApprovalGate', () => {
  it('renders title, command, and the three buttons', () => {
    render(<ApprovalGate item={gate} onResolve={() => {}} />)
    expect(screen.getByText('Approval required')).toBeInTheDocument()
    expect(screen.getByText('rm -rf build')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Allow' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Always/ })).toBeInTheDocument()
  })

  it('Allow and Deny resolve without a policy', async () => {
    const onResolve = vi.fn()
    render(<ApprovalGate item={gate} onResolve={onResolve} />)
    await userEvent.click(screen.getByRole('button', { name: 'Allow' }))
    expect(onResolve).toHaveBeenCalledWith('allow', undefined)
    await userEvent.click(screen.getByRole('button', { name: 'Deny' }))
    expect(onResolve).toHaveBeenCalledWith('deny', undefined)
  })

  it('Always dropdown resolves allow with the chosen policy', async () => {
    const onResolve = vi.fn()
    render(<ApprovalGate item={gate} onResolve={onResolve} />)
    await userEvent.click(screen.getByRole('button', { name: /Always/ }))
    await userEvent.click(screen.getByText('Always allow bash (session)'))
    expect(onResolve).toHaveBeenCalledWith('allow', { pattern: '*', scope: 'session' })
  })

  it('denied gate renders the collapsed row', () => {
    render(<ApprovalGate item={{ ...gate, denied: true }} onResolve={() => {}} />)
    expect(screen.getByText(/Denied/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow' })).not.toBeInTheDocument()
  })
})
```

Run → FAIL.

- [ ] **Step 2: Implement**

`web/src/components/ApprovalGate.tsx`:

```tsx
import { useState } from 'react'
import type { StreamItem } from '../state/reducer'
import s from './ApprovalGate.module.css'

export default function ApprovalGate({
  item,
  onResolve,
}: {
  item: Extract<StreamItem, { kind: 'gate' }>
  onResolve(decision: 'allow' | 'deny', always?: { pattern: string; scope: 'session' | 'global' }): void
}) {
  const [menuOpen, setMenuOpen] = useState(false)

  if (item.denied) {
    return (
      <div className={s.deniedRow}>
        <span className={s.deniedGlyph}>✕</span>
        <span>Denied</span>
        <span className={s.deniedCmd}>{item.display}</span>
      </div>
    )
  }

  const alwaysOptions = [
    { label: 'Always allow this command (session)', pattern: item.display, scope: 'session' as const },
    { label: `Always allow ${item.tool} (session)`, pattern: '*', scope: 'session' as const },
    { label: `Always allow ${item.tool} (global)`, pattern: '*', scope: 'global' as const },
  ]

  return (
    <div className={s.gate}>
      <span className={s.tile}>⚠</span>
      <div className={s.textCol}>
        <div className={s.title}>Approval required</div>
        <div className={s.command}>{item.display}</div>
      </div>
      <div className={s.actions}>
        <button className={s.allow} onClick={() => onResolve('allow', undefined)}>Allow</button>
        <button className={s.ghost} onClick={() => onResolve('deny', undefined)}>Deny</button>
        <span className={s.alwaysWrap}>
          <button className={s.ghost} onClick={() => setMenuOpen(o => !o)}>Always ⌄</button>
          {menuOpen && (
            <div className={s.menu}>
              {alwaysOptions.map(o => (
                <button
                  key={o.label}
                  className={s.menuItem}
                  onClick={() => {
                    setMenuOpen(false)
                    onResolve('allow', { pattern: o.pattern, scope: o.scope })
                  }}
                >
                  {o.label}
                </button>
              ))}
            </div>
          )}
        </span>
      </div>
    </div>
  )
}
```

`web/src/components/ApprovalGate.module.css`:

```css
.gate {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 16px;
  background: linear-gradient(180deg, #16130a, #111008);
  border: 1px solid rgba(229, 184, 75, 0.25);
  border-radius: var(--r-card);
  box-shadow: 0 0 24px rgba(229, 184, 75, 0.06);
}

.tile {
  width: 30px; height: 30px; flex: none;
  display: grid; place-items: center;
  border-radius: var(--r-btn-lg);
  background: rgba(229, 184, 75, 0.12);
  color: var(--warn);
  font-size: 14px;
}

.textCol { min-width: 0; }
.title { font-size: 12.5px; font-weight: 600; color: var(--warn-title); }
.command {
  font-family: var(--font-mono); font-size: 11.5px; color: var(--text-body);
  margin-top: 3px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

.actions { margin-left: auto; display: flex; align-items: center; gap: 8px; flex: none; }

.allow {
  background: var(--accent); color: var(--ink);
  font-size: 12px; font-weight: 600;
  border-radius: var(--r-btn-lg);
  padding: 6px 14px;
  box-shadow: 0 4px 14px color-mix(in oklab, var(--accent) 30%, transparent),
              inset 0 1px 0 rgba(255, 255, 255, 0.25);
}
.ghost {
  border: 1px solid var(--hair-5);
  color: var(--text-secondary);
  font-size: 12px;
  border-radius: var(--r-btn-lg);
  padding: 6px 12px;
}
.ghost:hover { border-color: var(--hair-hover); }

.alwaysWrap { position: relative; }
.menu {
  position: absolute; right: 0; top: calc(100% + 6px); z-index: 10;
  background: var(--bg-raised-1);
  border: 1px solid var(--hair-4);
  border-radius: var(--r-btn-lg);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
  min-width: 240px;
  padding: 4px;
}
.menuItem {
  display: block; width: 100%; text-align: left;
  font-size: 12px; color: var(--text-body);
  padding: 7px 10px; border-radius: 6px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.menuItem:hover { background: rgba(255, 255, 255, 0.06); }

.deniedRow {
  display: flex; align-items: center; gap: 9px;
  padding: 9px 13px;
  background: var(--bg-card);
  border: 1px solid var(--hair-3);
  border-radius: var(--r-card);
  font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary);
}
.deniedGlyph { color: var(--danger); }
.deniedCmd { color: var(--text-ghost); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 3: Run tests**

Run: `pnpm test`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/components/ApprovalGate.tsx src/components/ApprovalGate.module.css src/components/ApprovalGate.test.tsx
git commit -m "feat: approval gate with allow/deny/always policies"
```

---

### Task 13: ChatStream — bubbles, prose, status line, item routing

Handoff §"Chat stream": 700px centered column, 22px turn gap, right-aligned user bubbles, markdown agent prose, tool cards, gates, glowing status line. Routes each `StreamItem` to its component; auto-scrolls to the bottom while the session is active.

**Files:**
- Create: `web/src/components/ChatStream.tsx`, `web/src/components/ChatStream.module.css`
- Test: `web/src/components/ChatStream.test.tsx`

**Interfaces:**
- Consumes `useForge` (active session's stream), `ToolCard`, `ApprovalGate`, `api.resolveApproval`, `useForge().openDrawer`.
- Item routing: `user` → bubble; `prose` → react-markdown; `tool` → `<ToolCard>`; `gate` → `<ApprovalGate>`; `error` → danger-tinted line; `info` → muted line; `compacted` → centered ghost divider `· context compacted ·`.
- Status line (below the items, only when not idle): `running` → `Working · step {steps}`; `attention` → `Waiting on approval · step {steps}`; `queued` → `Queued — waiting for a slot`. 7px ACCENT dot with `0 0 8px` glow.
- Auto-scroll: on item count change, if the user hasn't scrolled up more than 80px from the bottom, scroll to bottom (`scrollTop = scrollHeight`).

- [ ] **Step 1: Write the failing tests**

`web/src/components/ChatStream.test.tsx`:

```tsx
import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import ChatStream from './ChatStream'

const ev = (type: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: 'aa', ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  const { applyEvent } = useForge.getState()
  applyEvent(ev('session_created', 1, { name: 'n', cwd: '/w', model: 'm', autonomy: 'guarded' }))
})

const apply = (...events: WireEvent[]) => {
  const { applyEvent } = useForge.getState()
  events.forEach(applyEvent)
}

describe('ChatStream', () => {
  it('renders user bubble, markdown prose, tool card, and gate', () => {
    apply(
      ev('user_message', 2, { text: 'fix the bug' }),
      ev('assistant_message', 3, { text: 'Looking at **app.py** now.', tool_calls: [] }),
      ev('tool_call_started', 4, { call_id: 'c1', tool: 'bash', display: 'pytest -q' }),
      ev('approval_requested', 5, { call_id: 'c2', tool: 'bash', display: 'rm -rf build' }),
    )
    render(<ChatStream />)
    expect(screen.getByText('fix the bug')).toBeInTheDocument()
    expect(screen.getByText('app.py')).toBeInTheDocument()   // <strong> from markdown
    expect(screen.getByText('pytest -q')).toBeInTheDocument()
    expect(screen.getByText('Approval required')).toBeInTheDocument()
  })

  it('shows the status line for running/attention/queued, hides when idle', () => {
    apply(
      ev('user_message', 2, { text: 'go' }),
      ev('status_changed', 3, { status: 'running' }),
      ev('tool_call_started', 4, { call_id: 'c1', tool: 'bash', display: 'ls' }),
    )
    const { rerender } = render(<ChatStream />)
    expect(screen.getByText('Working · step 1')).toBeInTheDocument()

    apply(ev('status_changed', 5, { status: 'attention' }))
    rerender(<ChatStream />)
    expect(screen.getByText('Waiting on approval · step 1')).toBeInTheDocument()

    apply(ev('run_finished', 6, { reason: 'completed' }))
    rerender(<ChatStream />)
    expect(screen.queryByText(/step 1/)).not.toBeInTheDocument()
  })

  it('renders error, info, and compaction items', () => {
    apply(
      ev('error', 2, { message: 'LLM unreachable' }),
      ev('run_finished', 3, { reason: 'cancelled' }),
      ev('context_compacted', 4, { summary: 's', upto_seq: 2 }),
    )
    render(<ChatStream />)
    expect(screen.getByText('LLM unreachable')).toBeInTheDocument()
    expect(screen.getByText('Run cancelled')).toBeInTheDocument()
    expect(screen.getByText('· context compacted ·')).toBeInTheDocument()
  })
})
```

Run → FAIL.

- [ ] **Step 2: Implement**

`web/src/components/ChatStream.tsx`:

```tsx
import { useEffect, useRef } from 'react'
import Markdown from 'react-markdown'
import { api } from '../api'
import { useForge } from '../state/store'
import ApprovalGate from './ApprovalGate'
import ToolCard from './ToolCard'
import s from './ChatStream.module.css'

export default function ChatStream() {
  const activeId = useForge(st => st.activeId)
  const session = useForge(st => (st.activeId ? st.sessions[st.activeId] : undefined))
  const openDrawer = useForge(st => st.openDrawer)
  const scroller = useRef<HTMLDivElement>(null)

  const itemCount = session?.stream.items.length ?? 0
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [itemCount, activeId])

  if (!session) return <div ref={scroller} className={s.scroller} />
  const { items, status, steps } = session.stream

  const statusText =
    status === 'running' ? `Working · step ${steps}`
    : status === 'attention' ? `Waiting on approval · step ${steps}`
    : status === 'queued' ? 'Queued — waiting for a slot'
    : null

  return (
    <div ref={scroller} className={s.scroller}>
      <div className={s.column}>
        {items.map((item, i) => {
          switch (item.kind) {
            case 'user':
              return <div key={i} className={s.userRow}><div className={s.userBubble}>{item.text}</div></div>
            case 'prose':
              return <div key={i} className={s.prose}><Markdown>{item.text}</Markdown></div>
            case 'tool':
              return <ToolCard key={i} item={item} onOpenPanel={idx => void openDrawer(idx)} />
            case 'gate':
              return (
                <ApprovalGate
                  key={i}
                  item={item}
                  onResolve={(decision, always) =>
                    void api.resolveApproval(session.id, item.callId, decision, always)}
                />
              )
            case 'error':
              return <div key={i} className={s.errorLine}>{item.message}</div>
            case 'info':
              return <div key={i} className={s.infoLine}>{item.text}</div>
            case 'compacted':
              return <div key={i} className={s.compacted}>· context compacted ·</div>
          }
        })}
        {statusText && (
          <div className={s.statusLine}>
            <span className={s.statusDot} />
            {statusText}
          </div>
        )}
      </div>
    </div>
  )
}
```

`web/src/components/ChatStream.module.css`:

```css
.scroller {
  flex: 1;
  overflow-y: auto;
  background: radial-gradient(1200px 500px at 50% -200px, rgba(255, 255, 255, 0.025), transparent);
}

.column {
  max-width: 700px;
  margin: 0 auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.userRow { display: flex; justify-content: flex-end; }
.userBubble {
  max-width: 500px;
  background: var(--bg-raised-2);
  border: 1px solid var(--hair-2);
  padding: 11px 16px;
  border-radius: 16px 16px 6px 16px;
  font-size: 13.5px;
  line-height: 1.6;
  color: #e3e3e8;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
  white-space: pre-wrap;
  word-break: break-word;
}

.prose {
  font-size: 13.5px;
  line-height: 1.65;
  color: var(--text-body);
}
.prose :global(p + p) { margin-top: 8px; }
.prose :global(code) {
  font-family: var(--font-mono);
  font-size: 12px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 4px;
  padding: 1px 5px;
}
.prose :global(pre) {
  background: var(--bg-card);
  border: 1px solid var(--hair-3);
  border-radius: var(--r-card);
  padding: 10px 15px;
  overflow-x: auto;
  margin: 8px 0;
}
.prose :global(pre code) { background: none; padding: 0; font-size: 11.5px; line-height: 1.75; }

.errorLine { font-size: 12.5px; color: var(--danger-dim); }
.infoLine { font-size: 12px; color: var(--text-muted); }
.compacted { text-align: center; font-size: 11.5px; color: var(--text-ghost); }

.statusLine {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px;
  color: var(--text-muted);
}
.statusDot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 8px var(--accent);
}
```

Note: if the CSS-modules `:global` syntax trips vitest's css handling, move the markdown element styles (`p + p`, `code`, `pre`) into `global.css` under a `.prose-md` class applied to the prose div instead — behavior tests must not depend on styling either way.

- [ ] **Step 3: Run tests**

Run: `pnpm test`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/components/ChatStream.tsx src/components/ChatStream.module.css src/components/ChatStream.test.tsx
git commit -m "feat: chat stream with bubbles, markdown prose, status line"
```

---

### Task 14: Composer — input, send, model pill, chips

Handoff §"Composer": floating card at the bottom of the chat column; growing textarea; footer with `@ files` / `/ commands` chips, model pill, accent send button. Enter sends (Shift+Enter = newline); sending mid-run is steering (same endpoint, no special casing).

**Files:**
- Create: `web/src/components/Composer.tsx`, `web/src/components/Composer.module.css`
- Test: `web/src/components/Composer.test.tsx`

**Interfaces:**
- Consumes `useForge` (send, active session's model/autonomy, models list, healthy).
- Produces: renders `<FilePicker>`/`<CommandPalette>` (Task 15) when the draft triggers them; to keep this task self-contained, the popover mount points are added HERE as no-ops behind props, and Task 15 fills them in. Concretely: Composer owns draft state and exposes two derived values via module-level helpers (exported for tests and Task 15):

```ts
export function paletteQuery(draft: string): string | null   // '/mod' → 'mod'; null when not a palette draft
export function atQuery(draft: string): string | null        // 'see @src/ap' → 'src/ap'; null when no active @token
```

- Model pill text: `{display_name of stream.model, falling back to the raw id} · {autonomy}`; when `healthy === false`, the pill gains a red dot and title "CLIProxyAPI unreachable".
- Send button disabled when the draft is blank.

- [ ] **Step 1: Write the failing tests**

`web/src/components/Composer.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import Composer, { atQuery, paletteQuery } from './Composer'

const ev = (type: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: 'aa', ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.getState().applyEvent(
    ev('session_created', 1, { name: 'n', cwd: '/w', model: 'opus-5', autonomy: 'yolo' }))
  useForge.setState({
    models: [{ id: 'opus-5', display_name: 'opus-5', context_window: 1 }],
    healthy: true,
  })
})

describe('Composer', () => {
  it('Enter sends the draft and clears it', async () => {
    const send = vi.fn(async () => {})
    useForge.setState({ send })
    render(<Composer />)
    const box = screen.getByPlaceholderText('Reply, steer, or queue another task…')
    await userEvent.type(box, 'run the tests{Enter}')
    expect(send).toHaveBeenCalledWith('run the tests')
    expect(box).toHaveValue('')
  })

  it('Shift+Enter inserts a newline instead of sending', async () => {
    const send = vi.fn(async () => {})
    useForge.setState({ send })
    render(<Composer />)
    const box = screen.getByPlaceholderText('Reply, steer, or queue another task…')
    await userEvent.type(box, 'line1{Shift>}{Enter}{/Shift}line2')
    expect(send).not.toHaveBeenCalled()
    expect(box).toHaveValue('line1\nline2')
  })

  it('shows the model pill with autonomy and health', () => {
    render(<Composer />)
    expect(screen.getByText('opus-5 · yolo')).toBeInTheDocument()
    useForge.setState({ healthy: false })
    render(<Composer />)
    expect(screen.getAllByTitle('CLIProxyAPI unreachable').length).toBeGreaterThan(0)
  })
})

describe('draft triggers', () => {
  it('paletteQuery matches only slash-prefixed drafts', () => {
    expect(paletteQuery('/mod')).toBe('mod')
    expect(paletteQuery('/')).toBe('')
    expect(paletteQuery('hello /model')).toBeNull()
  })

  it('atQuery matches a trailing @token', () => {
    expect(atQuery('see @src/ap')).toBe('src/ap')
    expect(atQuery('@')).toBe('')
    expect(atQuery('email me a@b.com ')).toBeNull()  // not at the draft tail
    expect(atQuery('no token')).toBeNull()
  })
})
```

Run → FAIL.

- [ ] **Step 2: Implement**

`web/src/components/Composer.tsx`:

```tsx
import { useRef, useState } from 'react'
import { useForge } from '../state/store'
import CommandPalette from './CommandPalette'
import FilePicker from './FilePicker'
import s from './Composer.module.css'

export function paletteQuery(draft: string): string | null {
  const m = /^\/(\S*)$/.exec(draft)
  return m ? m[1] : null
}

export function atQuery(draft: string): string | null {
  const m = /(?:^|\s)@([\w./-]*)$/.exec(draft)
  return m ? m[1] : null
}

export default function Composer() {
  const [draft, setDraft] = useState('')
  const boxRef = useRef<HTMLTextAreaElement>(null)
  const send = useForge(st => st.send)
  const models = useForge(st => st.models)
  const healthy = useForge(st => st.healthy)
  const stream = useForge(st => (st.activeId ? st.sessions[st.activeId].stream : undefined))

  const modelName =
    models.find(m => m.id === stream?.model)?.display_name ?? stream?.model ?? ''

  const palette = paletteQuery(draft)
  const at = palette === null ? atQuery(draft) : null

  const submit = () => {
    const text = draft.trim()
    if (!text || palette !== null) return
    setDraft('')
    void send(text)
  }

  const autosize = () => {
    const el = boxRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`
  }

  return (
    <div className={s.wrap}>
      <div className={s.card}>
        {palette !== null && (
          <CommandPalette query={palette} onClose={() => setDraft('')} />
        )}
        {at !== null && (
          <FilePicker
            query={at}
            onPick={path => {
              setDraft(d => d.replace(/@[\w./-]*$/, `${path} `))
              boxRef.current?.focus()
            }}
          />
        )}
        <textarea
          ref={boxRef}
          className={s.input}
          rows={1}
          placeholder="Reply, steer, or queue another task…"
          value={draft}
          onChange={e => { setDraft(e.target.value); autosize() }}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
        />
        <div className={s.footer}>
          <span className={s.chip}>@ files</span>
          <span className={s.chip}>/ commands</span>
          <span className={s.spacer} />
          <span
            className={s.modelPill}
            title={healthy ? undefined : 'CLIProxyAPI unreachable'}
          >
            {!healthy && <span className={s.healthDot} />}
            {modelName} · {stream?.autonomy ?? 'yolo'}
          </span>
          <button
            className={s.send}
            aria-label="Send"
            disabled={!draft.trim()}
            onClick={submit}
          >
            ↑
          </button>
        </div>
      </div>
    </div>
  )
}
```

(Task 15 creates `CommandPalette`/`FilePicker`. To keep THIS task green before Task 15 exists, create both files now as minimal placeholders returning `null` with the exact prop signatures from Task 15's Interfaces block — Task 15 replaces the bodies.)

`web/src/components/Composer.module.css`:

```css
.wrap { flex: none; padding: 16px 24px 22px; }
.card {
  position: relative;
  max-width: 700px;
  margin: 0 auto;
  background: var(--bg-composer);
  border: 1px solid var(--hair-5);
  border-radius: var(--r-composer);
  padding: 13px 15px;
  box-shadow: var(--shadow-composer);
}

.input {
  width: 100%;
  resize: none;
  font-size: 13.5px;
  line-height: 1.5;
  color: var(--text-primary);
  max-height: 140px;
}
.input::placeholder { color: var(--text-faint-2); }

.footer { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.chip {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-faint);
  border: 1px solid var(--hair-4);
  border-radius: var(--r-btn);
  padding: 3px 8px;
}
.spacer { flex: 1; }

.modelPill {
  display: flex; align-items: center; gap: 6px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-secondary);
  background: rgba(255, 255, 255, 0.04);
  border-radius: var(--r-pill);
  padding: 4px 10px;
}
.healthDot { width: 5px; height: 5px; border-radius: 50%; background: var(--danger); }

.send {
  width: 28px; height: 28px;
  display: grid; place-items: center;
  border-radius: var(--r-btn-lg);
  background: var(--accent);
  color: var(--ink);
  font-weight: 600;
  box-shadow: 0 4px 12px color-mix(in oklab, var(--accent) 35%, transparent),
              inset 0 1px 0 rgba(255, 255, 255, 0.3);
}
.send:disabled { opacity: 0.4; cursor: default; }
```

- [ ] **Step 3: Run tests**

Run: `pnpm test`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/components/Composer.tsx src/components/Composer.module.css src/components/Composer.test.tsx src/components/CommandPalette.tsx src/components/FilePicker.tsx
git commit -m "feat: composer with send, model pill, trigger detection"
```

---

### Task 15: FilePicker + CommandPalette popovers

`@` opens the fuzzy file picker (REST `/files?q=`, debounced); `/` opens the command palette (`/new`, `/model`, `/autonomy`, `/compact`, `/cancel` — `/cancel` is a deliberate addition beyond the spec's list: the engine supports it and a runaway run needs a stop). Both render as popovers above the composer card; both support click selection (keyboard navigation is post-V1 — do not build it).

**Files:**
- Create (replacing Task 14's placeholders): `web/src/components/FilePicker.tsx`, `web/src/components/CommandPalette.tsx`, `web/src/components/Popover.module.css` (shared styles)
- Test: `web/src/components/FilePicker.test.tsx`, `web/src/components/CommandPalette.test.tsx`

**Interfaces:**
- Props (must match the placeholders Task 14 created):

```tsx
// FilePicker
{ query: string; onPick(path: string): void }
// CommandPalette
{ query: string; onClose(): void }
```

- FilePicker: debounce 120ms, max 8 results, "no matches" ghost row when empty.
- CommandPalette commands and behavior (all act on the ACTIVE session via `api` + store, then `onClose()`):
  - `new` — `useForge().newSession()`
  - `model` — second step: lists `models` from the store; picking one → `api.setModel(sid, id)`
  - `autonomy` — second step: `yolo` / `guarded` → `api.setAutonomy(sid, choice)`
  - `compact` — `api.compact(sid)`; on `ApiError(409)` show a transient inline error row "Session is running — try after the run finishes" instead of closing
  - `cancel` — `api.cancel(sid)`
  Filter by `cmd.startsWith(query)`.

- [ ] **Step 1: Write the failing tests**

`web/src/components/FilePicker.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import FilePicker from './FilePicker'

const created: WireEvent = {
  type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
  name: 'n', cwd: '/w', model: 'm', autonomy: 'yolo',
} as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.getState().applyEvent(created)
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

describe('FilePicker', () => {
  it('debounces, fetches, renders results, picks on click', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ['src/app.py', 'src/api.py'] }))
    vi.stubGlobal('fetch', fetchMock)
    render(<FilePicker query="ap" onPick={() => {}} />)
    await vi.advanceTimersByTimeAsync(200)
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/files?q=ap')
    expect(await screen.findByText('src/app.py')).toBeInTheDocument()
  })

  it('onPick receives the clicked path', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ['src/app.py'] })))
    const onPick = vi.fn()
    render(<FilePicker query="ap" onPick={onPick} />)
    await vi.advanceTimersByTimeAsync(200)
    await userEvent.click(await screen.findByText('src/app.py'))
    expect(onPick).toHaveBeenCalledWith('src/app.py')
  })
})
```

`web/src/components/CommandPalette.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import CommandPalette from './CommandPalette'

const created: WireEvent = {
  type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
  name: 'n', cwd: '/w', model: 'm', autonomy: 'yolo',
} as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.getState().applyEvent(created)
  useForge.setState({ models: [
    { id: 'opus-5', display_name: 'Opus 5', context_window: 1 },
    { id: 'gpt-5', display_name: 'GPT-5', context_window: 1 },
  ] })
})

describe('CommandPalette', () => {
  it('filters by prefix', () => {
    render(<CommandPalette query="co" onClose={() => {}} />)
    expect(screen.getByText('/compact')).toBeInTheDocument()
    expect(screen.queryByText('/model')).not.toBeInTheDocument()
  })

  it('/model steps into the model list and calls the endpoint', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock)
    const onClose = vi.fn()
    render(<CommandPalette query="" onClose={onClose} />)
    await userEvent.click(screen.getByText('/model'))
    await userEvent.click(screen.getByText('GPT-5'))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/model', expect.objectContaining({ method: 'POST' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('/compact surfaces the 409 as an inline error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 409, json: async () => ({}) })))
    const onClose = vi.fn()
    render(<CommandPalette query="" onClose={onClose} />)
    await userEvent.click(screen.getByText('/compact'))
    expect(await screen.findByText(/Session is running/)).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })
})
```

Run → FAIL (placeholders render null).

- [ ] **Step 2: Implement**

`web/src/components/Popover.module.css`:

```css
.popover {
  position: absolute;
  left: 12px; right: 12px;
  bottom: calc(100% + 8px);
  z-index: 20;
  background: var(--bg-raised-1);
  border: 1px solid var(--hair-4);
  border-radius: var(--r-btn-lg);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
  padding: 4px;
  max-height: 260px;
  overflow-y: auto;
}
.row {
  display: flex; align-items: baseline; gap: 10px;
  width: 100%; text-align: left;
  font-size: 12.5px; color: var(--text-body);
  padding: 7px 10px; border-radius: 6px;
}
.row:hover { background: rgba(255, 255, 255, 0.06); }
.rowMono { font-family: var(--font-mono); font-size: 11.5px; }
.hint { margin-left: auto; font-size: 11px; color: var(--text-faint); }
.empty { padding: 7px 10px; font-size: 11.5px; color: var(--text-ghost); }
.error { padding: 7px 10px; font-size: 11.5px; color: var(--danger-dim); }
```

`web/src/components/FilePicker.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { api } from '../api'
import { useForge } from '../state/store'
import s from './Popover.module.css'

export default function FilePicker({
  query,
  onPick,
}: {
  query: string
  onPick(path: string): void
}) {
  const activeId = useForge(st => st.activeId)
  const [results, setResults] = useState<string[]>([])

  useEffect(() => {
    if (!activeId) return
    let live = true
    const t = setTimeout(() => {
      api.searchFiles(activeId, query)
        .then(r => { if (live) setResults(r.slice(0, 8)) })
        .catch(() => { if (live) setResults([]) })
    }, 120)
    return () => { live = false; clearTimeout(t) }
  }, [activeId, query])

  return (
    <div className={s.popover}>
      {results.length === 0 && <div className={s.empty}>no matches</div>}
      {results.map(path => (
        <button key={path} className={`${s.row} ${s.rowMono}`} onClick={() => onPick(path)}>
          {path}
        </button>
      ))}
    </div>
  )
}
```

`web/src/components/CommandPalette.tsx`:

```tsx
import { useState } from 'react'
import { api, ApiError } from '../api'
import { useForge } from '../state/store'
import s from './Popover.module.css'

const COMMANDS = [
  { cmd: 'new', hint: 'start a new session' },
  { cmd: 'model', hint: 'switch model' },
  { cmd: 'autonomy', hint: 'yolo / guarded' },
  { cmd: 'compact', hint: 'compact context now' },
  { cmd: 'cancel', hint: 'stop the current run' },
]

export default function CommandPalette({
  query,
  onClose,
}: {
  query: string
  onClose(): void
}) {
  const [step, setStep] = useState<'root' | 'model' | 'autonomy'>('root')
  const [error, setError] = useState<string | null>(null)
  const activeId = useForge(st => st.activeId)
  const models = useForge(st => st.models)
  const newSession = useForge(st => st.newSession)

  const run = async (fn: () => Promise<void>) => {
    try {
      await fn()
      onClose()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409)
        setError('Session is running — try after the run finishes')
      else setError('Command failed')
    }
  }

  const pick = (cmd: string) => {
    if (!activeId && cmd !== 'new') return
    switch (cmd) {
      case 'new': return void run(() => newSession())
      case 'model': return setStep('model')
      case 'autonomy': return setStep('autonomy')
      case 'compact': return void run(() => api.compact(activeId!))
      case 'cancel': return void run(() => api.cancel(activeId!))
    }
  }

  return (
    <div className={s.popover}>
      {error && <div className={s.error}>{error}</div>}
      {step === 'root' &&
        COMMANDS.filter(c => c.cmd.startsWith(query)).map(c => (
          <button key={c.cmd} className={s.row} onClick={() => pick(c.cmd)}>
            <span className={s.rowMono}>/{c.cmd}</span>
            <span className={s.hint}>{c.hint}</span>
          </button>
        ))}
      {step === 'model' &&
        models.map(m => (
          <button key={m.id} className={s.row}
                  onClick={() => void run(() => api.setModel(activeId!, m.id))}>
            {m.display_name}
            <span className={s.hint}>{m.id}</span>
          </button>
        ))}
      {step === 'autonomy' &&
        (['yolo', 'guarded'] as const).map(a => (
          <button key={a} className={s.row}
                  onClick={() => void run(() => api.setAutonomy(activeId!, a))}>
            {a}
          </button>
        ))}
    </div>
  )
}
```

- [ ] **Step 3: Run tests**

Run: `pnpm test`
Expected: all PASS (including Task 14's composer tests, now with real popovers).

- [ ] **Step 4: Commit**

```bash
git add src/components/FilePicker.tsx src/components/CommandPalette.tsx src/components/Popover.module.css src/components/FilePicker.test.tsx src/components/CommandPalette.test.tsx
git commit -m "feat: @ file picker and / command palette"
```

---

### Task 16: DetailDrawer — diff parser, Diff/File views, footer actions

Handoff §"Detail drawer": 480px right panel; breadcrumb header with stat chips and Diff/File/Blame segmented control; gutter-numbered diff body; footer with pager, Revert, Keep all. Blame is a stub ("Blame — post-V1"). Data: the active session's `changesets[]` (already fetched by `openDrawer` in Task 8) — `changeset.diff` is a unified diff produced by Python `difflib.unified_diff` (`--- a/<name>`, `+++ b/<name>`, `@@ -l,c +l,c @@` hunks).

**Files:**
- Create: `web/src/lib/diff.ts`, `web/src/components/DetailDrawer.tsx`, `web/src/components/DetailDrawer.module.css`
- Test: `web/src/lib/diff.test.ts`, `web/src/components/DetailDrawer.test.tsx`

**Interfaces:**
- Produces `web/src/lib/diff.ts`:

```ts
export interface DiffLine { kind: 'add' | 'del' | 'ctx'; oldNo: number | null; newNo: number | null; text: string }
export interface Hunk { header: string; lines: DiffLine[] }
export function parseUnifiedDiff(diff: string): Hunk[]
```

- DetailDrawer consumes `useForge` (active session's drawer/changesets/fileContent, setDrawerView, closeDrawer, stepDrawer, revert, keepAll).

- [ ] **Step 1: Write the failing diff-parser tests**

`web/src/lib/diff.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { parseUnifiedDiff } from './diff'

const DIFF = `--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 import os
-x = 1
+x = 2
+y = 3
 print(x)
@@ -10,2 +11,2 @@
 tail
-old
+new
`

describe('parseUnifiedDiff', () => {
  it('parses hunks with correct line numbers', () => {
    const hunks = parseUnifiedDiff(DIFF)
    expect(hunks).toHaveLength(2)
    expect(hunks[0].header).toBe('@@ -1,3 +1,4 @@')
    expect(hunks[0].lines).toEqual([
      { kind: 'ctx', oldNo: 1, newNo: 1, text: 'import os' },
      { kind: 'del', oldNo: 2, newNo: null, text: 'x = 1' },
      { kind: 'add', oldNo: null, newNo: 2, text: 'x = 2' },
      { kind: 'add', oldNo: null, newNo: 3, text: 'y = 3' },
      { kind: 'ctx', oldNo: 3, newNo: 4, text: 'print(x)' },
    ])
    expect(hunks[1].lines[1]).toEqual({ kind: 'del', oldNo: 11, newNo: null, text: 'old' })
  })

  it('handles empty and headerless input', () => {
    expect(parseUnifiedDiff('')).toEqual([])
  })
})
```

Run → FAIL. Implement `web/src/lib/diff.ts`:

```ts
export interface DiffLine {
  kind: 'add' | 'del' | 'ctx'
  oldNo: number | null
  newNo: number | null
  text: string
}

export interface Hunk {
  header: string
  lines: DiffLine[]
}

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

export function parseUnifiedDiff(diff: string): Hunk[] {
  const hunks: Hunk[] = []
  let current: Hunk | null = null
  let oldNo = 0
  let newNo = 0

  for (const line of diff.split('\n')) {
    const m = HUNK_RE.exec(line)
    if (m) {
      current = { header: line, lines: [] }
      hunks.push(current)
      oldNo = parseInt(m[1], 10)
      newNo = parseInt(m[2], 10)
      continue
    }
    if (!current || line.startsWith('---') || line.startsWith('+++')) continue
    if (line.startsWith('+')) {
      current.lines.push({ kind: 'add', oldNo: null, newNo: newNo++, text: line.slice(1) })
    } else if (line.startsWith('-')) {
      current.lines.push({ kind: 'del', oldNo: oldNo++, newNo: null, text: line.slice(1) })
    } else if (line.startsWith(' ') || line === '') {
      if (line === '' && current.lines.length === 0) continue
      current.lines.push({ kind: 'ctx', oldNo: oldNo++, newNo: newNo++, text: line.slice(1) })
    }
  }
  // difflib ends with a trailing newline → one spurious empty ctx line; drop it
  for (const h of hunks) {
    const last = h.lines[h.lines.length - 1]
    if (last?.kind === 'ctx' && last.text === '' ) h.lines.pop()
  }
  return hunks
}
```

Run: `pnpm test src/lib/diff.test.ts` → PASS. Commit:

```bash
git add src/lib/diff.ts src/lib/diff.test.ts
git commit -m "feat: unified diff parser"
```

- [ ] **Step 2: Write the failing drawer tests**

`web/src/components/DetailDrawer.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { Changeset, WireEvent } from '../protocol'
import DetailDrawer from './DetailDrawer'

const created: WireEvent = {
  type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
  name: 'n', cwd: '/w', model: 'm', autonomy: 'yolo',
} as unknown as WireEvent

const cs: Changeset[] = [
  { index: 0, path: '/w/src/app.py', added: 2, removed: 1, status: 'pending',
    diff: '--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n import os\n-x = 1\n+x = 2\n+y = 3\n' },
  { index: 1, path: '/w/README.md', added: 1, removed: 0, status: 'pending',
    diff: '--- a/README.md\n+++ b/README.md\n@@ -1,0 +1,1 @@\n+hello\n' },
] as Changeset[]

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.getState().applyEvent(created)
  useForge.setState(s => ({
    sessions: {
      ...s.sessions,
      aa: { ...s.sessions.aa, changesets: cs,
            drawer: { open: true, changesetIndex: 0, view: 'diff' } },
    },
  }))
})

describe('DetailDrawer', () => {
  it('renders breadcrumb, stat chips, and the parsed diff', () => {
    render(<DetailDrawer />)
    expect(screen.getByText('src/')).toBeInTheDocument()
    expect(screen.getByText('app.py')).toBeInTheDocument()
    expect(screen.getByText('+2')).toBeInTheDocument()
    expect(screen.getByText('−1')).toBeInTheDocument()
    expect(screen.getByText('x = 2')).toBeInTheDocument()
    expect(screen.getByText('@@ -1,2 +1,3 @@')).toBeInTheDocument()
  })

  it('footer shows the pager and steps files', async () => {
    render(<DetailDrawer />)
    expect(screen.getByText('1 of 2 files changed')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '›' }))
    expect(useForge.getState().sessions.aa.drawer.changesetIndex).toBe(1)
  })

  it('Revert and Keep all hit the API', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => [] }))
    vi.stubGlobal('fetch', fetchMock)
    render(<DetailDrawer />)
    await userEvent.click(screen.getByRole('button', { name: 'Revert' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/changesets/0/revert', expect.anything())
    await userEvent.click(screen.getByRole('button', { name: 'Keep all' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/changesets/keep_all', expect.anything())
  })

  it('File view renders the cached content; Blame is stubbed', async () => {
    useForge.setState(s => ({
      sessions: { ...s.sessions, aa: { ...s.sessions.aa, fileContent: 'import os\nx = 2\n' } },
    }))
    render(<DetailDrawer />)
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ path: '/w/src/app.py', content: 'import os\nx = 2\n' }) }))
    vi.stubGlobal('fetch', fetchMock)
    await userEvent.click(screen.getByRole('button', { name: 'File' }))
    expect(await screen.findByText(/import os/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Blame' }))
    expect(screen.getByText('Blame — post-V1')).toBeInTheDocument()
  })
})
```

Run → FAIL.

- [ ] **Step 3: Implement the drawer**

`web/src/components/DetailDrawer.tsx`:

```tsx
import { parseUnifiedDiff } from '../lib/diff'
import { useForge } from '../state/store'
import s from './DetailDrawer.module.css'

export default function DetailDrawer() {
  const session = useForge(st => (st.activeId ? st.sessions[st.activeId] : undefined))
  const setDrawerView = useForge(st => st.setDrawerView)
  const closeDrawer = useForge(st => st.closeDrawer)
  const stepDrawer = useForge(st => st.stepDrawer)
  const revert = useForge(st => st.revert)
  const keepAll = useForge(st => st.keepAll)

  if (!session?.drawer.open) return null
  const { changesets, drawer, fileContent } = session
  const cs = changesets[drawer.changesetIndex]
  if (!cs) return null

  const rel = cs.path.startsWith(session.stream.cwd)
    ? cs.path.slice(session.stream.cwd.length).replace(/^\//, '')
    : cs.path
  const slash = rel.lastIndexOf('/')
  const dir = slash >= 0 ? rel.slice(0, slash + 1) : ''
  const base = rel.slice(slash + 1)

  return (
    <aside className={s.drawer}>
      <header className={s.header}>
        <span className={s.dir}>{dir}</span>
        <span className={s.file}>{base}</span>
        <span className={s.chipAdd}>+{cs.added}</span>
        <span className={s.chipDel}>−{cs.removed}</span>
        {cs.status !== 'pending' && <span className={s.chipStatus}>{cs.status}</span>}
        <div className={s.seg}>
          {(['diff', 'file', 'blame'] as const).map(v => (
            <button key={v}
                    className={drawer.view === v ? s.segActive : s.segBtn}
                    onClick={() => void setDrawerView(v)}>
              {v[0].toUpperCase() + v.slice(1)}
            </button>
          ))}
        </div>
        <button className={s.close} aria-label="Close" onClick={closeDrawer}>✕</button>
      </header>

      <div className={s.body}>
        {drawer.view === 'diff' &&
          parseUnifiedDiff(cs.diff).map((hunk, hi) => (
            <div key={hi} className={s.hunk}>
              <div className={s.hunkHeader}>{hunk.header}</div>
              {hunk.lines.map((l, li) => (
                <div key={li} className={s.row} data-kind={l.kind}>
                  <span className={s.gutter}>
                    {l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : (l.newNo ?? '')}
                  </span>
                  <span className={s.code}>{l.text}</span>
                </div>
              ))}
            </div>
          ))}
        {drawer.view === 'file' && (
          fileContent === null
            ? <div className={s.stub}>Loading…</div>
            : <pre className={s.fileView}>{fileContent}</pre>
        )}
        {drawer.view === 'blame' && <div className={s.stub}>Blame — post-V1</div>}
      </div>

      <footer className={s.footer}>
        <button className={s.pager} aria-label="‹" onClick={() => void stepDrawer(-1)}>‹</button>
        <button className={s.pager} aria-label="›" onClick={() => void stepDrawer(1)}>›</button>
        <span className={s.count}>
          {drawer.changesetIndex + 1} of {changesets.length} files changed
        </span>
        <button className={s.ghost} onClick={() => void revert()}>Revert</button>
        <button className={s.keep} onClick={() => void keepAll()}>Keep all</button>
      </footer>
    </aside>
  )
}
```

`web/src/components/DetailDrawer.module.css`:

```css
.drawer {
  width: 480px; flex: none;
  display: flex; flex-direction: column;
  background: var(--bg-drawer);
  border-left: 1px solid var(--hair-2);
  animation: slideIn 240ms ease-out;
}
@keyframes slideIn {
  from { transform: translateX(480px); }
  to { transform: translateX(0); }
}

.header {
  display: flex; align-items: center; gap: 8px;
  padding: 11px 15px;
  border-bottom: 1px solid var(--hair-2);
}
.dir { font-family: var(--font-mono); font-size: 11px; color: var(--text-faint); }
.file { font-size: 12px; color: var(--text-primary); }
.chipAdd, .chipDel, .chipStatus {
  font-family: var(--font-mono); font-size: 10.5px;
  border-radius: var(--r-tile); padding: 2px 6px;
}
.chipAdd { background: var(--ok-bg); color: var(--ok); }
.chipDel { background: var(--danger-bg); color: var(--danger); }
.chipStatus { background: rgba(255, 255, 255, 0.06); color: var(--text-secondary); }

.seg {
  margin-left: auto;
  display: flex;
  background: rgba(255, 255, 255, 0.04);
  border-radius: var(--r-seg);
  padding: 3px;
}
.segBtn, .segActive {
  font-size: 10.5px; padding: 3px 9px;
  border-radius: var(--r-btn);
  border: 1px solid transparent;
  color: var(--text-secondary);
}
.segActive {
  background: var(--bg-raised-1);
  border-color: var(--hair-4);
  color: var(--text-primary);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
}
.close { color: var(--text-faint); font-size: 11px; padding: 2px 4px; }
.close:hover { color: var(--text-secondary); }

.body { flex: 1; overflow: auto; padding: 10px 0; }
.hunk { margin-bottom: 8px; }
.hunkHeader {
  font-family: var(--font-mono); font-size: 11.5px;
  color: var(--text-ghost);
  padding: 2px 15px 2px 56px;
}
.row {
  display: flex;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.8;
}
.row[data-kind='del'] { background: var(--danger-row); color: var(--danger-dim); }
.row[data-kind='add'] { background: var(--ok-row); color: var(--ok-dim); }
.row[data-kind='ctx'] { color: var(--text-muted); }
.gutter {
  width: 44px; flex: none;
  text-align: right; padding-right: 12px;
  color: var(--text-ghost-2);
}
.row[data-kind='del'] .gutter { color: #6e4040; }
.row[data-kind='add'] .gutter { color: #3e6e50; }
.code { white-space: pre; padding-right: 15px; }

.fileView {
  font-family: var(--font-mono); font-size: 11.5px; line-height: 1.8;
  color: var(--text-muted);
  padding: 0 15px; margin: 0;
  white-space: pre-wrap; word-break: break-word;
}
.stub { padding: 20px 15px; font-size: 11.5px; color: var(--text-ghost); }

.footer {
  display: flex; align-items: center; gap: 8px;
  padding: 11px 15px;
  border-top: 1px solid var(--hair-2);
}
.pager { color: var(--text-secondary); font-size: 13px; padding: 2px 7px;
         border: 1px solid var(--hair-5); border-radius: var(--r-btn); }
.count { font-size: 11.5px; color: var(--text-muted); }
.ghost {
  margin-left: auto;
  border: 1px solid var(--hair-5); color: var(--text-secondary);
  font-size: 12px; border-radius: var(--r-btn-lg); padding: 6px 12px;
}
.keep {
  background: var(--bg-raised-3);
  border: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
  font-size: 12px; font-weight: 500;
  border-radius: var(--r-btn-lg); padding: 6px 12px;
}
```

- [ ] **Step 4: Run tests**

Run: `pnpm test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/DetailDrawer.tsx src/components/DetailDrawer.module.css src/components/DetailDrawer.test.tsx
git commit -m "feat: detail drawer with diff/file views, pager, revert/keep"
```

---

### Task 17: App shell — wiring, layout, build & serve, docs

Assemble the frame (TopBar / chat column / drawer), boot hydration + WS + health polling, verify the production build is served by the engine, write the READMEs.

**Files:**
- Modify: `web/src/App.tsx`, `web/src/App.test.tsx`
- Create: `web/src/App.module.css`, `server/README.md`, `web/README.md`
- Modify: `server/Makefile` (add `dev-web` convenience target)

**Interfaces:**
- Consumes everything above. Boot sequence: `hydrate()` → `startWs({ url: wsUrl(), cursors, … })` → 15s health poll. `wsUrl()` = `` `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws` ``.

- [ ] **Step 1: Write the failing test**

Replace `web/src/App.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useForge } from './state/store'
import App from './App'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  constructor(public url: string) { FakeWebSocket.instances.push(this) }
  send() {}
  close() {}
}

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.includes('/models') ? [] : url.includes('/health') ? { ok: true } : [],
  })))
})

describe('App', () => {
  it('boots: hydrates, opens the websocket, renders the frame', async () => {
    render(<App />)
    expect(await screen.findByText('Forge')).toBeInTheDocument()          // brand
    expect(screen.getByPlaceholderText('Reply, steer, or queue another task…')).toBeInTheDocument()
    expect(FakeWebSocket.instances.length).toBeGreaterThan(0)
    expect(FakeWebSocket.instances[0].url).toMatch(/\/ws$/)
  })

  it('renders events pushed through the store', async () => {
    render(<App />)
    useForge.getState().applyEvent({
      type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
      name: 'hello world', cwd: '/w', model: 'm', autonomy: 'yolo',
    } as never)
    expect(await screen.findByRole('tab', { name: /hello world/ })).toBeInTheDocument()
  })
})
```

Run → FAIL.

- [ ] **Step 2: Implement**

`web/src/App.tsx`:

```tsx
import { useEffect } from 'react'
import ChatStream from './components/ChatStream'
import Composer from './components/Composer'
import DetailDrawer from './components/DetailDrawer'
import TopBar from './components/TopBar'
import { cursors, useForge } from './state/store'
import { startWs } from './ws'
import s from './App.module.css'

function wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}

export default function App() {
  useEffect(() => {
    const st = useForge.getState()
    void st.hydrate()
    const stop = startWs({
      url: wsUrl(),
      cursors: () => cursors(useForge.getState()),
      onEvent: e => useForge.getState().applyEvent(e),
      onStatus: c => useForge.getState().setConnection(c),
    })
    const health = setInterval(() => void useForge.getState().refreshHealth(), 15_000)
    return () => { stop(); clearInterval(health) }
  }, [])

  const connection = useForge(st => st.connection)

  return (
    <div className={s.frame}>
      <TopBar />
      {connection !== 'open' && (
        <div className={s.connBanner}>reconnecting…</div>
      )}
      <div className={s.main}>
        <div className={s.chatCol}>
          <ChatStream />
          <Composer />
        </div>
        <DetailDrawer />
      </div>
    </div>
  )
}
```

`web/src/App.module.css`:

```css
.frame {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.main {
  flex: 1;
  display: flex;
  min-height: 0;
}
.chatCol {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.connBanner {
  text-align: center;
  font-size: 11px;
  color: var(--warn);
  background: rgba(229, 184, 75, 0.08);
  padding: 3px;
}
```

Note the StrictMode double-mount: the effect's cleanup must fully stop the WS (Task 9's `stop()` handles it) so tests and dev don't leak sockets.

- [ ] **Step 3: Run tests + build, verify the engine serves the SPA**

```bash
pnpm test && pnpm build
cd ../server && uv run pytest -q
```

Then boot the real thing: `cd server && uv run python -m forge.api.app` and `curl -s http://127.0.0.1:8700/ | head -5` — expect the built `index.html` (the engine mounts `web/dist` when it exists). Kill the server. If CLIProxyAPI isn't running or the api key is unset, `/api/health` returns `{"ok": false}` — that is fine for this check.

- [ ] **Step 4: Docs**

`server/README.md`:

```markdown
# Forge server

Event-sourced agent engine. Requires [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)
running locally (default `http://127.0.0.1:8317/v1`).

## Setup

    uv sync
    mkdir -p ~/.forge

`~/.forge/config.toml` (created with defaults on first boot; the api_key MUST be
set to your real CLIProxyAPI key — the default is a placeholder):

    base_url = "http://127.0.0.1:8317/v1"
    api_key = "sk-..."

## Run

    make dev          # uvicorn on 127.0.0.1:8700 (serves web/dist if built)
    make test         # pytest
    make lint         # ruff
    make export-protocol   # JSON-Schema bundle for web codegen

Env: `FORGE_HOME` overrides `~/.forge`.
```

`web/README.md`:

```markdown
# Forge web

React + Vite SPA for the Forge engine. Design source of truth:
`../design_handoff_agent_workspace/` (card 2a "Stream").

    pnpm install
    pnpm dev            # Vite on :5173, proxies /api and /ws to :8700
    pnpm test           # vitest
    pnpm build          # emits dist/ (served by the engine at :8700)
    pnpm gen:protocol   # regenerate src/protocol/generated.ts from the engine
```

`server/Makefile` — add:

```makefile
dev-web:
	cd ../web && pnpm dev
```

- [ ] **Step 5: Final verification and commit**

```bash
cd web && pnpm test && pnpm build && cd ../server && uv run pytest -q && uv run ruff check .
git add -A
git commit -m "feat: app shell, boot wiring, build/serve verification, docs"
```

Expected: full web suite green, 60+ engine tests green, ruff clean, `dist/` served.

---

## Post-plan note for the controller

- **Visual pass:** component fidelity is enforced by the CSS values transcribed from the handoff README, but a human look at `pnpm dev` against a running engine (real CLIProxyAPI or FakeLLM smoke script) is the final gate — flag it to Louis at the end rather than trying to automate it.
- **Remaining engine hardening** (deliberately NOT in this plan; tracked in `.superpowers/sdd/progress.md`): rehydrate resilience to corrupt logs, malformed-SKILL.md degradation, creation-order by `session_created.ts`, `status_changed` on rehydrate-idle, per-project `/api/skills`.

## Self-review (done at planning time)

- Spec coverage: frontend section fully mapped (store/reducer/WS → Tasks 6–9; TopBar/ChatStream/ToolCard/ApprovalGate/Composer/palette/picker/Drawer → Tasks 10–16; codegen → Task 5; `/model` + `/compact` → Tasks 1–2; Blame stubbed per spec; drawer File view backed by Task 3's endpoint).
- Deliberate scope decisions, recorded so reviewers don't flag them as gaps: `/cancel` palette command added beyond spec (engine supports it; stated in Task 15); auto-approved line folded into ToolCard meta (stated in Task 11); keyboard navigation in popovers is post-V1 (stated in Task 15).
- Type/name consistency: `StreamItem`/`SessionStream`/`reduce`/`emptyStream` (Task 6) match usage in Tasks 8, 11–13; store action names (Task 8) match Tasks 10, 13–17; `parseUnifiedDiff` (Task 16) self-contained; placeholder contract between Tasks 14/15 stated in both.







