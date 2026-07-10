# Forge Engine (Server) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Forge server — an event-sourced agent engine (sessions, LLM loop, tools, approvals, queue, skills, memory) exposed over REST + WebSocket, per `docs/superpowers/specs/2026-07-10-forge-agent-workspace-design.md`.

**Architecture:** Append-only per-session event log is the single source of truth; the model context, the UI stream, and restart rehydration are all projections of it. One asyncio `SessionActor` per session runs the agent loop against CLIProxyAPI through a swappable `LLMClient` interface (`FakeLLM` makes the whole engine deterministic in tests). FastAPI serves REST commands and one multiplexed WebSocket of events.

**Tech Stack:** Python ≥3.12, uv, FastAPI + uvicorn, openai (AsyncOpenAI), pydantic v2, PyYAML, pytest + pytest-asyncio (auto mode), ruff.

## Global Constraints

- All engine state lives under a home dir: env var `FORGE_HOME`, default `~/.forge`. Every component takes `home: Path` explicitly — tests pass `tmp_path`.
- CLIProxyAPI default base URL: `http://127.0.0.1:8317/v1`. Forge server port: `8700`.
- Autonomy modes are exactly `"yolo"` (default) and `"guarded"`. Session statuses are exactly `"idle" | "running" | "attention" | "queued"`.
- Default concurrency cap: 3. Default compaction threshold: 75% of the model's context window. Default tool-output persistence cap: 30 000 chars (head 15k + tail 15k).
- Durable events get a per-session monotonic `seq` starting at 1, assigned by the store at append. Ephemeral events have `seq = 0` and are never persisted.
- Python package name is `forge`, in `server/`. Run all commands from `server/` unless stated. Tests: `uv run pytest -q`. Lint: `uv run ruff check .`.
- Event JSON on the wire and on disk is `pydantic.model_dump(mode="json")` — one JSON object per line in `events.jsonl`.
- Type checking is via pydantic at boundaries only; no mypy in V1.

---

### Task 1: Server scaffold + event model

**Files:**
- Create: `server/pyproject.toml`
- Create: `server/forge/__init__.py`, `server/forge/engine/__init__.py`, `server/forge/store/__init__.py`, `server/forge/llm/__init__.py`, `server/forge/tools/__init__.py`, `server/forge/api/__init__.py`, `server/tests/__init__.py` (all empty; `tests/__init__.py` makes cross-test imports like `from tests.test_actor import make_actor` work)
- Create: `server/forge/engine/events.py`
- Test: `server/tests/test_events.py`

**Interfaces:**
- Produces: every event class below; `Event` (discriminated union of durable events); `parse_event(d: dict) -> Event`; `ToolCallSpec(id, name, arguments)`; `DiffStats(path, added, removed, changeset_index)`; ephemeral `TextDelta(session_id, text)` and `OutputChunk(session_id, call_id, text)`.

- [ ] **Step 1: Scaffold the package**

```toml
# server/pyproject.toml
[project]
name = "forge"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "openai>=1.60",
    "pydantic>=2.8",
    "pyyaml>=6.0",
]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "httpx>=0.27", "ruff>=0.6"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

Create the empty `__init__.py` files listed above. Run: `cd server && uv sync` — expect a `.venv` and lockfile.

- [ ] **Step 2: Write the failing test**

```python
# server/tests/test_events.py
from forge.engine.events import UserMessage, ToolCallFinished, parse_event


def test_event_roundtrip():
    e = UserMessage(session_id="s1", ts=1.0, text="hi")
    d = e.model_dump(mode="json")
    assert d["type"] == "user_message" and d["seq"] == 0
    assert parse_event(d) == e


def test_discriminated_parse():
    d = {"seq": 3, "session_id": "s1", "ts": 2.0, "type": "tool_call_finished",
         "call_id": "c1", "tool": "bash", "output": "ok", "is_error": False,
         "duration_ms": 12, "diff_stats": None}
    e = parse_event(d)
    assert isinstance(e, ToolCallFinished) and e.seq == 3 and e.output == "ok"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_events.py -v` — Expected: FAIL (ModuleNotFoundError: forge.engine.events).

- [ ] **Step 4: Implement the event model**

```python
# server/forge/engine/events.py
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

Autonomy = Literal["yolo", "guarded"]
Status = Literal["idle", "running", "attention", "queued"]
RunReason = Literal["completed", "cancelled", "interrupted", "error"]


class ToolCallSpec(BaseModel):
    id: str
    name: str
    arguments: str  # raw JSON string, as OpenAI supplies it


class DiffStats(BaseModel):
    path: str
    added: int
    removed: int
    changeset_index: int


class BaseEvent(BaseModel):
    seq: int = 0  # assigned by EventLog.append; 0 = not yet persisted
    session_id: str
    ts: float


class SessionCreated(BaseEvent):
    type: Literal["session_created"] = "session_created"
    name: str
    cwd: str
    model: str
    autonomy: Autonomy


class SessionRenamed(BaseEvent):
    type: Literal["session_renamed"] = "session_renamed"
    name: str


class StatusChanged(BaseEvent):
    type: Literal["status_changed"] = "status_changed"
    status: Status


class AutonomyChanged(BaseEvent):
    type: Literal["autonomy_changed"] = "autonomy_changed"
    autonomy: Autonomy


class UserMessage(BaseEvent):
    type: Literal["user_message"] = "user_message"
    text: str


class AssistantMessage(BaseEvent):
    type: Literal["assistant_message"] = "assistant_message"
    text: str
    tool_calls: list[ToolCallSpec] = []


class ToolCallStarted(BaseEvent):
    type: Literal["tool_call_started"] = "tool_call_started"
    call_id: str
    tool: str
    display: str
    auto_approved: bool = False


class ToolCallFinished(BaseEvent):
    type: Literal["tool_call_finished"] = "tool_call_finished"
    call_id: str
    tool: str
    output: str
    is_error: bool = False
    duration_ms: int = 0
    diff_stats: DiffStats | None = None


class ApprovalRequested(BaseEvent):
    type: Literal["approval_requested"] = "approval_requested"
    call_id: str
    tool: str
    display: str


class ApprovalResolved(BaseEvent):
    type: Literal["approval_resolved"] = "approval_resolved"
    call_id: str
    decision: Literal["allow", "deny"]


class PolicyAdded(BaseEvent):
    type: Literal["policy_added"] = "policy_added"
    tool: str
    pattern: str
    scope: Literal["session", "global"]


class ContextCompacted(BaseEvent):
    type: Literal["context_compacted"] = "context_compacted"
    summary: str
    upto_seq: int


class RunFinished(BaseEvent):
    type: Literal["run_finished"] = "run_finished"
    reason: RunReason


class ErrorEvent(BaseEvent):
    type: Literal["error"] = "error"
    message: str


Event = Annotated[
    Union[
        SessionCreated, SessionRenamed, StatusChanged, AutonomyChanged,
        UserMessage, AssistantMessage, ToolCallStarted, ToolCallFinished,
        ApprovalRequested, ApprovalResolved, PolicyAdded, ContextCompacted,
        RunFinished, ErrorEvent,
    ],
    Field(discriminator="type"),
]

_adapter: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(d: dict) -> Event:
    return _adapter.validate_python(d)


# Ephemeral (WebSocket-only, never persisted; seq stays 0)
class TextDelta(BaseModel):
    seq: int = 0
    session_id: str
    type: Literal["text_delta"] = "text_delta"
    text: str


class OutputChunk(BaseModel):
    seq: int = 0
    session_id: str
    type: Literal["output_chunk"] = "output_chunk"
    call_id: str
    text: str
```

- [ ] **Step 5: Run tests, lint, commit**

Run: `uv run pytest tests/test_events.py -v` — Expected: 2 PASS. `uv run ruff check .` — clean.

```bash
git add server/
git commit -m "feat(engine): scaffold server package and event model"
```

---

### Task 2: EventLog — JSONL store with sequence numbers

**Files:**
- Create: `server/forge/store/eventlog.py`
- Test: `server/tests/test_eventlog.py`

**Interfaces:**
- Consumes: `parse_event`, event classes from Task 1.
- Produces: `EventLog(path: Path)` with `.append(event) -> Event` (assigns next seq, persists, returns the stamped copy), `.read(after_seq: int = 0) -> list[Event]`, `.last_seq: int` (property).

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_eventlog.py
from forge.engine.events import UserMessage
from forge.store.eventlog import EventLog


def test_append_assigns_seq_and_persists(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    e1 = log.append(UserMessage(session_id="s1", ts=1.0, text="a"))
    e2 = log.append(UserMessage(session_id="s1", ts=2.0, text="b"))
    assert (e1.seq, e2.seq) == (1, 2) and log.last_seq == 2

    reloaded = EventLog(tmp_path / "events.jsonl")
    assert [e.text for e in reloaded.read()] == ["a", "b"]
    assert reloaded.last_seq == 2
    assert [e.seq for e in reloaded.read(after_seq=1)] == [2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eventlog.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

```python
# server/forge/store/eventlog.py
from __future__ import annotations

import json
from pathlib import Path

from forge.engine.events import Event, parse_event


class EventLog:
    """Append-only JSONL log of durable events for one session."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[Event] = []
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    self._events.append(parse_event(json.loads(line)))

    @property
    def last_seq(self) -> int:
        return self._events[-1].seq if self._events else 0

    def append(self, event) -> Event:
        stamped = event.model_copy(update={"seq": self.last_seq + 1})
        with self.path.open("a") as f:
            f.write(json.dumps(stamped.model_dump(mode="json")) + "\n")
        self._events.append(stamped)
        return stamped

    def read(self, after_seq: int = 0) -> list[Event]:
        return [e for e in self._events if e.seq > after_seq]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_eventlog.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/forge/store/eventlog.py server/tests/test_eventlog.py
git commit -m "feat(store): JSONL event log with per-session sequence numbers"
```

---

### Task 3: Config + policies

**Files:**
- Create: `server/forge/store/config.py`
- Test: `server/tests/test_config.py`

**Interfaces:**
- Produces: `Policy(tool, pattern)`; `ModelConfig(id, display_name, context_window)`; `ForgeConfig(base_url, api_key, models, default_model, default_autonomy, max_concurrent, policies)`; `load_config(home: Path) -> ForgeConfig` (reads `<home>/config.toml`, defaults if absent); `save_global_policy(home: Path, policy: Policy) -> None`; `policy_matches(policies: list[Policy], tool: str, display: str) -> bool` (fnmatch on the display line).

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_config.py
from forge.store.config import Policy, load_config, policy_matches, save_global_policy


def test_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.base_url == "http://127.0.0.1:8317/v1"
    assert cfg.default_autonomy == "yolo" and cfg.max_concurrent == 3
    assert cfg.default_model == cfg.models[0].id


def test_load_toml(tmp_path):
    (tmp_path / "config.toml").write_text(
        'base_url = "http://localhost:9999/v1"\n'
        'default_model = "gpt-5.2"\n'
        "max_concurrent = 2\n\n"
        "[[models]]\n"
        'id = "gpt-5.2"\ndisplay_name = "gpt-5.2"\ncontext_window = 272000\n\n'
        "[[policies]]\n"
        'tool = "bash"\npattern = "pytest*"\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.max_concurrent == 2 and cfg.models[0].context_window == 272000
    assert cfg.policies == [Policy(tool="bash", pattern="pytest*")]


def test_policy_matching_and_persist(tmp_path):
    pols = [Policy(tool="bash", pattern="pytest*")]
    assert policy_matches(pols, "bash", "pytest -q tests/")
    assert not policy_matches(pols, "bash", "rm -rf /")
    assert not policy_matches(pols, "edit_file", "pytest.ini")

    save_global_policy(tmp_path, Policy(tool="edit_file", pattern="*"))
    assert Policy(tool="edit_file", pattern="*") in load_config(tmp_path).policies
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/store/config.py
from __future__ import annotations

import tomllib
from fnmatch import fnmatch
from pathlib import Path

from pydantic import BaseModel


class Policy(BaseModel):
    tool: str
    pattern: str


class ModelConfig(BaseModel):
    id: str
    display_name: str
    context_window: int = 200_000


DEFAULT_MODELS = [
    ModelConfig(id="claude-sonnet-4-5", display_name="sonnet-4.5"),
    ModelConfig(id="gpt-5.2", display_name="gpt-5.2", context_window=272_000),
]


class ForgeConfig(BaseModel):
    base_url: str = "http://127.0.0.1:8317/v1"
    api_key: str = "sk-forge"
    models: list[ModelConfig] = DEFAULT_MODELS
    default_model: str = ""
    default_autonomy: str = "yolo"
    max_concurrent: int = 3
    policies: list[Policy] = []

    def context_window(self, model_id: str) -> int:
        for m in self.models:
            if m.id == model_id:
                return m.context_window
        return 200_000


def load_config(home: Path) -> ForgeConfig:
    path = home / "config.toml"
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    cfg = ForgeConfig.model_validate(data)
    if not cfg.default_model:
        cfg.default_model = cfg.models[0].id
    return cfg


def save_global_policy(home: Path, policy: Policy) -> None:
    """Append a policy as TOML; crude but config.toml stays human-owned."""
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    block = f'\n[[policies]]\ntool = "{policy.tool}"\npattern = "{policy.pattern}"\n'
    path.write_text((path.read_text() if path.exists() else "") + block)


def policy_matches(policies: list[Policy], tool: str, display: str) -> bool:
    return any(p.tool == tool and fnmatch(display, p.pattern) for p in policies)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_config.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/forge/store/config.py server/tests/test_config.py
git commit -m "feat(store): config loading and approval policies"
```

---

### Task 4: Context projection — event log → OpenAI messages

**Files:**
- Create: `server/forge/engine/projection.py`
- Test: `server/tests/test_projection.py`

**Interfaces:**
- Consumes: event classes from Task 1.
- Produces: `to_messages(events: list[Event], system_prompt: str) -> list[dict]`; `dangling_call_ids(events) -> list[tuple[str, str]]` (unanswered `(call_id, tool)` pairs, used by the actor to close them on cancel/rehydrate).

Rules: `user_message` → user role; `assistant_message` → assistant role (with `tool_calls` in OpenAI shape when present; `content` becomes `None` if empty); `tool_call_finished` → tool role. A user message arriving while tool calls are unanswered is buffered and flushed right after the block's last tool result (keeps OpenAI's required assistant→tool ordering). The latest `context_compacted` drops all events with `seq <= upto_seq` and injects the summary as the first user message.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_projection.py
from forge.engine.events import (
    AssistantMessage, ContextCompacted, ToolCallFinished, ToolCallSpec, UserMessage,
)
from forge.engine.projection import dangling_call_ids, to_messages

S = dict(session_id="s1", ts=0.0)


def test_basic_turn_with_tools():
    events = [
        UserMessage(seq=1, **S, text="run tests"),
        AssistantMessage(seq=2, **S, text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "pytest"}')]),
        ToolCallFinished(seq=3, **S, call_id="c1", tool="bash", output="1 passed"),
        AssistantMessage(seq=4, **S, text="All green."),
    ]
    msgs = to_messages(events, "SYS")
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1] == {"role": "user", "content": "run tests"}
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] is None
    assert msgs[2]["tool_calls"][0] == {
        "id": "c1", "type": "function",
        "function": {"name": "bash", "arguments": '{"command": "pytest"}'}}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "1 passed"}
    assert msgs[4] == {"role": "assistant", "content": "All green."}


def test_steering_message_lands_after_open_tool_block():
    events = [
        UserMessage(seq=1, **S, text="go"),
        AssistantMessage(seq=2, **S, text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments="{}")]),
        UserMessage(seq=3, **S, text="also update docs"),  # arrived mid-tool
        ToolCallFinished(seq=4, **S, call_id="c1", tool="bash", output="ok"),
    ]
    msgs = to_messages(events, "SYS")
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "user"]
    assert msgs[4]["content"] == "also update docs"


def test_compaction_cuts_and_injects_summary():
    events = [
        UserMessage(seq=1, **S, text="old"),
        AssistantMessage(seq=2, **S, text="old reply"),
        ContextCompacted(seq=3, **S, summary="did old things", upto_seq=2),
        UserMessage(seq=4, **S, text="new"),
    ]
    msgs = to_messages(events, "SYS")
    assert msgs[1]["role"] == "user" and "did old things" in msgs[1]["content"]
    assert msgs[2] == {"role": "user", "content": "new"}
    assert len(msgs) == 3


def test_dangling_call_ids():
    events = [
        AssistantMessage(seq=1, **S, text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments="{}"),
            ToolCallSpec(id="c2", name="read_file", arguments="{}")]),
        ToolCallFinished(seq=2, **S, call_id="c1", tool="bash", output="ok"),
    ]
    assert dangling_call_ids(events) == [("c2", "read_file")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_projection.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/engine/projection.py
from __future__ import annotations

from forge.engine.events import Event

SUMMARY_PREFIX = "[Summary of the conversation so far]\n"


def to_messages(events: list[Event], system_prompt: str) -> list[dict]:
    summary, cut = None, 0
    for e in events:
        if e.type == "context_compacted":
            summary, cut = e.summary, e.upto_seq

    msgs: list[dict] = [{"role": "system", "content": system_prompt}]
    if summary is not None:
        msgs.append({"role": "user", "content": SUMMARY_PREFIX + summary})

    pending_users: list[str] = []
    open_calls = 0
    for e in events:
        if e.seq <= cut:
            continue
        if e.type == "user_message":
            if open_calls:
                pending_users.append(e.text)
            else:
                msgs.append({"role": "user", "content": e.text})
        elif e.type == "assistant_message":
            m: dict = {"role": "assistant", "content": e.text or None}
            if e.tool_calls:
                m["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": c.arguments}}
                    for c in e.tool_calls
                ]
                open_calls = len(e.tool_calls)
            msgs.append(m)
        elif e.type == "tool_call_finished":
            msgs.append({"role": "tool", "tool_call_id": e.call_id, "content": e.output})
            open_calls = max(0, open_calls - 1)
            if open_calls == 0 and pending_users:
                msgs += [{"role": "user", "content": t} for t in pending_users]
                pending_users.clear()
    msgs += [{"role": "user", "content": t} for t in pending_users]
    return msgs


def dangling_call_ids(events: list[Event]) -> list[tuple[str, str]]:
    """(call_id, tool) for assistant tool calls that never got a result."""
    finished = {e.call_id for e in events if e.type == "tool_call_finished"}
    out = []
    for e in events:
        if e.type == "assistant_message":
            out += [(c.id, c.name) for c in e.tool_calls if c.id not in finished]
    return [(cid, tool) for cid, tool in out if cid not in finished]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_projection.py -v` — Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add server/forge/engine/projection.py server/tests/test_projection.py
git commit -m "feat(engine): project event log into OpenAI messages"
```

---

### Task 5: LLM interface + FakeLLM

**Files:**
- Create: `server/forge/llm/base.py`
- Create: `server/forge/llm/fake.py`
- Test: `server/tests/test_fake_llm.py`

**Interfaces:**
- Produces: `CompletionResult(text: str, tool_calls: list[ToolCallSpec], usage_tokens: int)`; `LLMError(Exception)`; `LLMClient` protocol with `async complete(model: str, messages: list[dict], tools: list[dict], on_text_delta: Callable[[str], Awaitable[None]]) -> CompletionResult` and `async healthy() -> bool`; `FakeLLM(script: list[CompletionResult | Exception], delay: float = 0.0)` recording `.calls: list[list[dict]]` — `delay` makes each `complete()` await that long, so tests can interleave commands with an in-flight run.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_fake_llm.py
import pytest

from forge.engine.events import ToolCallSpec
from forge.llm.base import CompletionResult, LLMError
from forge.llm.fake import FakeLLM


async def test_fake_llm_pops_script_and_streams():
    fake = FakeLLM([CompletionResult(text="hello", tool_calls=[], usage_tokens=10)])
    deltas: list[str] = []

    async def on_delta(t: str):
        deltas.append(t)

    r = await fake.complete("m", [{"role": "user", "content": "hi"}], [], on_delta)
    assert r.text == "hello" and deltas == ["hello"]
    assert fake.calls[0][0]["content"] == "hi"
    assert await fake.healthy()


async def test_fake_llm_raises_scripted_errors():
    fake = FakeLLM([LLMError("boom")])

    async def on_delta(t: str): ...

    with pytest.raises(LLMError):
        await fake.complete("m", [], [], on_delta)


def test_completion_result_holds_tool_calls():
    r = CompletionResult(
        text="", tool_calls=[ToolCallSpec(id="c1", name="bash", arguments="{}")],
        usage_tokens=5)
    assert r.tool_calls[0].name == "bash"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fake_llm.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/llm/base.py
from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from pydantic import BaseModel

from forge.engine.events import ToolCallSpec

OnTextDelta = Callable[[str], Awaitable[None]]


class CompletionResult(BaseModel):
    text: str
    tool_calls: list[ToolCallSpec] = []
    usage_tokens: int = 0


class LLMError(Exception):
    """Raised when the model call fails after retries."""


class LLMClient(Protocol):
    async def complete(
        self, model: str, messages: list[dict], tools: list[dict],
        on_text_delta: OnTextDelta,
    ) -> CompletionResult: ...

    async def healthy(self) -> bool: ...
```

```python
# server/forge/llm/fake.py
from __future__ import annotations

import asyncio

from forge.llm.base import CompletionResult, OnTextDelta


class FakeLLM:
    """Scripted LLM for deterministic end-to-end engine tests."""

    def __init__(self, script: list[CompletionResult | Exception], delay: float = 0.0):
        self.script = list(script)
        self.delay = delay
        self.calls: list[list[dict]] = []

    async def complete(
        self, model: str, messages: list[dict], tools: list[dict],
        on_text_delta: OnTextDelta,
    ) -> CompletionResult:
        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if item.text:
            await on_text_delta(item.text)
        return item

    async def healthy(self) -> bool:
        return True
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_fake_llm.py -v` — Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add server/forge/llm/ server/tests/test_fake_llm.py
git commit -m "feat(llm): LLMClient interface and scripted FakeLLM"
```

---

### Task 6: OpenAI adapter for CLIProxyAPI

**Files:**
- Create: `server/forge/llm/openai_client.py`
- Test: `server/tests/test_openai_client.py`

**Interfaces:**
- Consumes: `CompletionResult`, `LLMError`, `OnTextDelta` from Task 5.
- Produces: `OpenAILLM(base_url: str, api_key: str, retry_delays: tuple[float, ...] = (1, 2, 4))` implementing `LLMClient`. Streams via `chat.completions.create(stream=True, stream_options={"include_usage": True})`; assembles tool-call deltas by index; retries `APIConnectionError`, `RateLimitError`, `InternalServerError`; `healthy()` pings `models.list()`.

- [ ] **Step 1: Write the failing test** (fake the AsyncOpenAI client — no network)

```python
# server/tests/test_openai_client.py
from types import SimpleNamespace as NS

import pytest
from openai import APIConnectionError

from forge.llm.base import LLMError
from forge.llm.openai_client import OpenAILLM


def chunk(content=None, tool_calls=None, usage=None):
    choice = NS(delta=NS(content=content, tool_calls=tool_calls))
    return NS(choices=[choice] if content or tool_calls else [], usage=usage)


def tc(index, id=None, name=None, arguments=None):
    return NS(index=index, id=id,
              function=NS(name=name, arguments=arguments))


class FakeStream:
    def __init__(self, chunks): self._chunks = list(chunks)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self._chunks: raise StopAsyncIteration
        return self._chunks.pop(0)


def make_llm(responses):
    """responses: list of chunk-lists or exceptions, one per create() call."""
    llm = OpenAILLM("http://x/v1", "k", retry_delays=(0,))
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeStream(item)

    llm.client = NS(chat=NS(completions=NS(create=create)))
    return llm, calls


async def test_assembles_text_and_tool_calls():
    llm, calls = make_llm([[
        chunk(content="Hel"), chunk(content="lo"),
        chunk(tool_calls=[tc(0, id="c1", name="bash", arguments='{"comm')]),
        chunk(tool_calls=[tc(0, arguments='and": "ls"}')]),
        chunk(usage=NS(total_tokens=42)),
    ]])
    deltas = []

    async def on_delta(t): deltas.append(t)

    r = await llm.complete("m", [{"role": "user", "content": "hi"}],
                           [{"type": "function"}], on_delta)
    assert r.text == "Hello" and deltas == ["Hel", "lo"]
    assert r.tool_calls[0].id == "c1"
    assert r.tool_calls[0].arguments == '{"command": "ls"}'
    assert r.usage_tokens == 42
    assert calls[0]["stream"] is True and "tools" in calls[0]


async def test_retries_then_raises_llm_error():
    conn_err = APIConnectionError(request=NS())
    llm, calls = make_llm([conn_err, conn_err])

    async def on_delta(t): ...

    with pytest.raises(LLMError):
        await llm.complete("m", [], [], on_delta)
    assert len(calls) == 2  # first try + one retry (retry_delays=(0,))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_openai_client.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/llm/openai_client.py
from __future__ import annotations

import asyncio

from openai import APIConnectionError, AsyncOpenAI, InternalServerError, RateLimitError

from forge.engine.events import ToolCallSpec
from forge.llm.base import CompletionResult, LLMError, OnTextDelta

RETRYABLE = (APIConnectionError, RateLimitError, InternalServerError)


class OpenAILLM:
    def __init__(self, base_url: str, api_key: str,
                 retry_delays: tuple[float, ...] = (1, 2, 4)):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.retry_delays = retry_delays

    async def complete(self, model: str, messages: list[dict], tools: list[dict],
                       on_text_delta: OnTextDelta) -> CompletionResult:
        last: Exception | None = None
        for delay in (0, *self.retry_delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self._stream_once(model, messages, tools, on_text_delta)
            except RETRYABLE as e:
                last = e
        raise LLMError(f"LLM call failed after retries: {last}")

    async def _stream_once(self, model, messages, tools, on_text_delta):
        kwargs: dict = {"model": model, "messages": messages, "stream": True,
                        "stream_options": {"include_usage": True}}
        if tools:
            kwargs["tools"] = tools
        stream = await self.client.chat.completions.create(**kwargs)

        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        usage = 0
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage.total_tokens
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            if d is None:
                continue
            if d.content:
                text_parts.append(d.content)
                await on_text_delta(d.content)
            for tc in d.tool_calls or []:
                c = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    c["id"] = tc.id
                if tc.function and tc.function.name:
                    c["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    c["arguments"] += tc.function.arguments
        return CompletionResult(
            text="".join(text_parts),
            tool_calls=[ToolCallSpec(**calls[i]) for i in sorted(calls)],
            usage_tokens=usage,
        )

    async def healthy(self) -> bool:
        try:
            await self.client.models.list()
            return True
        except Exception:
            return False
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_openai_client.py -v` — Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add server/forge/llm/openai_client.py server/tests/test_openai_client.py
git commit -m "feat(llm): streaming OpenAI adapter for CLIProxyAPI with retries"
```

---

### Task 7: Tool framework + read-only tools

**Files:**
- Create: `server/forge/tools/base.py`
- Create: `server/forge/tools/files_read.py`
- Create: `server/forge/tools/search.py`
- Test: `server/tests/test_read_tools.py`

**Interfaces:**
- Produces:
  - `ToolResult(output: str, is_error: bool = False, diff_stats: DiffStats | None = None)`
  - `ToolContext(cwd: Path, emit_chunk: Callable[[str], None], changesets: "ChangesetStore | None")` (plain dataclass; `emit_chunk` defaults to no-op)
  - `Tool` ABC: attrs `name: str`, `description: str`, `params: dict` (JSON Schema), `read_only: bool = False`; methods `display(args: dict) -> str`, `async run(args: dict, ctx: ToolContext) -> ToolResult`
  - `openai_spec(tool: Tool) -> dict` → `{"type": "function", "function": {"name", "description", "parameters"}}`
  - `truncate_middle(s: str, max_chars: int = 30_000) -> str`
  - Tools: `ReadFileTool` (`path`, optional `offset`, `limit`; 1-indexed `cat -n` style output), `ListDirTool` (`path` optional, default `.`; dirs get `/` suffix), `GlobTool` (`pattern`; sorted, cap 200), `GrepTool` (`pattern`, optional `path`; uses `rg -n --no-heading` when available, pure-Python fallback; cap 100 lines). All `read_only = True`. Relative paths resolve against `ctx.cwd`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_read_tools.py
from forge.tools.base import ToolContext, openai_spec, truncate_middle
from forge.tools.files_read import ReadFileTool
from forge.tools.search import GlobTool, GrepTool, ListDirTool


def ctx(tmp_path):
    return ToolContext(cwd=tmp_path)


async def test_read_file_numbers_lines(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nbeta\n")
    r = await ReadFileTool().run({"path": "a.txt"}, ctx(tmp_path))
    assert not r.is_error and r.output == "     1\talpha\n     2\tbeta"


async def test_read_file_missing_is_error(tmp_path):
    r = await ReadFileTool().run({"path": "nope.txt"}, ctx(tmp_path))
    assert r.is_error and "not found" in r.output.lower()


async def test_glob_grep_listdir(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "x.py").write_text("def needle(): pass\n")
    (tmp_path / "y.md").write_text("nothing\n")
    g = await GlobTool().run({"pattern": "**/*.py"}, ctx(tmp_path))
    assert "pkg/x.py" in g.output
    s = await GrepTool().run({"pattern": "needle"}, ctx(tmp_path))
    assert "x.py" in s.output and "1" in s.output
    ls = await ListDirTool().run({}, ctx(tmp_path))
    assert "pkg/" in ls.output and "y.md" in ls.output


def test_spec_and_truncation():
    spec = openai_spec(ReadFileTool())
    assert spec["type"] == "function" and spec["function"]["name"] == "read_file"
    long = "x" * 50_000
    t = truncate_middle(long, max_chars=1000)
    assert len(t) < 1200 and "truncated" in t


def test_display():
    assert ReadFileTool().display({"path": "a.txt"}) == "a.txt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_read_tools.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/tools/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pydantic import BaseModel

from forge.engine.events import DiffStats

if TYPE_CHECKING:
    from forge.store.changesets import ChangesetStore


class ToolResult(BaseModel):
    output: str
    is_error: bool = False
    diff_stats: DiffStats | None = None


@dataclass
class ToolContext:
    cwd: Path
    emit_chunk: Callable[[str], None] = field(default=lambda _t: None)
    changesets: "ChangesetStore | None" = None

    def resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self.cwd / p)


class Tool(ABC):
    name: str
    description: str
    params: dict
    read_only: bool = False

    def display(self, args: dict) -> str:
        return args.get("path") or args.get("command") or self.name

    @abstractmethod
    async def run(self, args: dict, ctx: ToolContext) -> ToolResult: ...


def openai_spec(tool: Tool) -> dict:
    return {"type": "function", "function": {
        "name": tool.name, "description": tool.description, "parameters": tool.params}}


def truncate_middle(s: str, max_chars: int = 30_000) -> str:
    if len(s) <= max_chars:
        return s
    half = max_chars // 2
    return f"{s[:half]}\n… [{len(s) - max_chars} chars truncated] …\n{s[-half:]}"
```

```python
# server/forge/tools/files_read.py
from __future__ import annotations

from forge.tools.base import Tool, ToolContext, ToolResult, truncate_middle


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a file. Returns 1-indexed, line-numbered content."
    params = {"type": "object", "properties": {
        "path": {"type": "string"},
        "offset": {"type": "integer", "description": "1-indexed first line"},
        "limit": {"type": "integer", "description": "max lines (default 2000)"},
    }, "required": ["path"]}
    read_only = True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args["path"])
        if not path.is_file():
            return ToolResult(output=f"File not found: {args['path']}", is_error=True)
        lines = path.read_text(errors="replace").splitlines()
        start = max(args.get("offset", 1), 1)
        limit = args.get("limit", 2000)
        window = lines[start - 1:start - 1 + limit]
        body = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(window, start))
        return ToolResult(output=truncate_middle(body))
```

```python
# server/forge/tools/search.py
from __future__ import annotations

import asyncio
import re
import shutil

from forge.tools.base import Tool, ToolContext, ToolResult, truncate_middle

SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}


class ListDirTool(Tool):
    name = "list_dir"
    description = "List directory entries; directories have a trailing /."
    params = {"type": "object", "properties": {"path": {"type": "string"}}}
    read_only = True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args.get("path", "."))
        if not path.is_dir():
            return ToolResult(output=f"Not a directory: {path}", is_error=True)
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        return ToolResult(output="\n".join(entries) or "(empty)")


class GlobTool(Tool):
    name = "glob"
    description = "Find files by glob pattern relative to the working directory."
    params = {"type": "object", "properties": {"pattern": {"type": "string"}},
              "required": ["pattern"]}
    read_only = True

    def display(self, args: dict) -> str:
        return args.get("pattern", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        hits = [str(p.relative_to(ctx.cwd)) for p in ctx.cwd.glob(args["pattern"])
                if not any(part in SKIP_DIRS for part in p.parts)]
        hits = sorted(hits)[:200]
        return ToolResult(output="\n".join(hits) or "No matches.")


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with a regex. Output: path:line:text."
    params = {"type": "object", "properties": {
        "pattern": {"type": "string"}, "path": {"type": "string"}},
        "required": ["pattern"]}
    read_only = True

    def display(self, args: dict) -> str:
        return args.get("pattern", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        root = ctx.resolve(args.get("path", "."))
        if shutil.which("rg"):
            proc = await asyncio.create_subprocess_exec(
                "rg", "-n", "--no-heading", "--max-count", "100",
                args["pattern"], str(root),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await proc.communicate()
            text = out.decode(errors="replace").strip()
            return ToolResult(output=truncate_middle(text) or "No matches.")
        # Python fallback
        rx = re.compile(args["pattern"])
        lines: list[str] = []
        files = [root] if root.is_file() else [
            p for p in root.rglob("*")
            if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)]
        for f in files:
            try:
                for i, line in enumerate(f.read_text().splitlines(), 1):
                    if rx.search(line):
                        lines.append(f"{f.relative_to(ctx.cwd)}:{i}:{line}")
                        if len(lines) >= 100:
                            raise StopIteration
            except (UnicodeDecodeError, StopIteration, ValueError):
                continue
        return ToolResult(output="\n".join(lines) or "No matches.")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_read_tools.py -v` — Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add server/forge/tools/ server/tests/test_read_tools.py
git commit -m "feat(tools): tool framework and read-only file/search tools"
```

---

### Task 8: Changesets + write_file / edit_file

**Files:**
- Create: `server/forge/store/changesets.py`
- Create: `server/forge/tools/files_write.py`
- Test: `server/tests/test_write_tools.py`

**Interfaces:**
- Consumes: `Tool`, `ToolContext`, `ToolResult`, `DiffStats`.
- Produces:
  - `Changeset(index: int, path: str, added: int, removed: int, diff: str, status: Literal["pending","kept","reverted"])`
  - `ChangesetStore(dir: Path)` with `.record(path: Path, before: str | None, after: str) -> Changeset` (writes `blobs/{index}.before` / `.after`, unified diff via `difflib`), `.list() -> list[Changeset]`, `.get(index) -> Changeset`, `.revert(index) -> None` (restores before-blob; deletes the file if `before is None`), `.keep_all() -> None` (marks all pending as kept). State file: `<dir>/changesets.jsonl` (rewritten whole on status change — small file, fine).
  - `WriteFileTool` (`path`, `content`), `EditFileTool` (`path`, `old_string`, `new_string`, optional `replace_all`; errors if `old_string` missing or ambiguous without `replace_all`). Both record a changeset and return `diff_stats`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_write_tools.py
from forge.store.changesets import ChangesetStore
from forge.tools.base import ToolContext
from forge.tools.files_write import EditFileTool, WriteFileTool


def ctx(tmp_path):
    return ToolContext(cwd=tmp_path / "ws",
                       changesets=ChangesetStore(tmp_path / "cs"))


async def test_write_then_edit_records_changesets(tmp_path):
    (tmp_path / "ws").mkdir()
    c = ctx(tmp_path)
    r1 = await WriteFileTool().run({"path": "a.py", "content": "x = 1\n"}, c)
    assert not r1.is_error and r1.diff_stats.added == 1 and r1.diff_stats.removed == 0
    r2 = await EditFileTool().run(
        {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, c)
    assert not r2.is_error
    assert (tmp_path / "ws" / "a.py").read_text() == "x = 2\n"
    sets = c.changesets.list()
    assert len(sets) == 2 and sets[1].added == 1 and sets[1].removed == 1
    assert "-x = 1" in sets[1].diff and "+x = 2" in sets[1].diff


async def test_edit_requires_unique_match(tmp_path):
    (tmp_path / "ws").mkdir()
    c = ctx(tmp_path)
    (tmp_path / "ws" / "b.py").write_text("y = 0\ny = 0\n")
    r = await EditFileTool().run(
        {"path": "b.py", "old_string": "y = 0", "new_string": "y = 9"}, c)
    assert r.is_error and "2 times" in r.output
    r2 = await EditFileTool().run(
        {"path": "b.py", "old_string": "y = 0", "new_string": "y = 9",
         "replace_all": True}, c)
    assert not r2.is_error
    assert (tmp_path / "ws" / "b.py").read_text() == "y = 9\ny = 9\n"


async def test_revert_and_keep_all(tmp_path):
    (tmp_path / "ws").mkdir()
    c = ctx(tmp_path)
    await WriteFileTool().run({"path": "new.txt", "content": "hello\n"}, c)
    c.changesets.revert(0)
    assert not (tmp_path / "ws" / "new.txt").exists()
    assert c.changesets.get(0).status == "reverted"
    await WriteFileTool().run({"path": "k.txt", "content": "keep\n"}, c)
    c.changesets.keep_all()
    assert c.changesets.get(1).status == "kept"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_write_tools.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/store/changesets.py
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
```

```python
# server/forge/tools/files_write.py
from __future__ import annotations

from forge.tools.base import Tool, ToolContext, ToolResult


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a file with the given content."
    params = {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"]}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args["path"])
        before = path.read_text() if path.is_file() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"])
        cs = ctx.changesets.record(path, before, args["content"])
        stats = _stats(cs)
        return ToolResult(output=f"Wrote {args['path']} (+{cs.added}/−{cs.removed})",
                          diff_stats=stats)


class EditFileTool(Tool):
    name = "edit_file"
    description = ("Replace old_string with new_string in a file. old_string must "
                   "match exactly once unless replace_all is true.")
    params = {"type": "object", "properties": {
        "path": {"type": "string"}, "old_string": {"type": "string"},
        "new_string": {"type": "string"}, "replace_all": {"type": "boolean"}},
        "required": ["path", "old_string", "new_string"]}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args["path"])
        if not path.is_file():
            return ToolResult(output=f"File not found: {args['path']}", is_error=True)
        before = path.read_text()
        count = before.count(args["old_string"])
        if count == 0:
            return ToolResult(output="old_string not found in file", is_error=True)
        if count > 1 and not args.get("replace_all"):
            return ToolResult(
                output=f"old_string occurs {count} times; pass replace_all or add context",
                is_error=True)
        after = before.replace(args["old_string"], args["new_string"])
        path.write_text(after)
        cs = ctx.changesets.record(path, before, after)
        return ToolResult(output=f"Edited {args['path']} (+{cs.added}/−{cs.removed})",
                          diff_stats=_stats(cs))


def _stats(cs):
    from forge.engine.events import DiffStats
    return DiffStats(path=cs.path, added=cs.added, removed=cs.removed,
                     changeset_index=cs.index)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_write_tools.py -v` — Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add server/forge/store/changesets.py server/forge/tools/files_write.py server/tests/test_write_tools.py
git commit -m "feat(tools): write/edit tools with changeset tracking and revert"
```

---

### Task 9: bash tool

**Files:**
- Create: `server/forge/tools/bash.py`
- Test: `server/tests/test_bash_tool.py`

**Interfaces:**
- Consumes: `Tool`, `ToolContext`, `ToolResult`, `truncate_middle`.
- Produces: `BashTool(timeout_s: float = 120)` — runs `args["command"]` via `asyncio.create_subprocess_shell` in `ctx.cwd`, `start_new_session=True` (own process group), stderr merged into stdout, streams chunks through `ctx.emit_chunk`, kills the process group on timeout or cancellation, output persisted through `truncate_middle`. Non-zero exit → `is_error=True` with `(exit {code})` appended.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_bash_tool.py
import asyncio
import time

from forge.tools.base import ToolContext
from forge.tools.bash import BashTool


async def test_runs_and_streams(tmp_path):
    chunks: list[str] = []
    ctx = ToolContext(cwd=tmp_path, emit_chunk=chunks.append)
    r = await BashTool().run({"command": "echo hi && echo err >&2"}, ctx)
    assert not r.is_error
    assert "hi" in r.output and "err" in r.output  # stderr merged
    assert "".join(chunks) == r.output


async def test_nonzero_exit_is_error(tmp_path):
    r = await BashTool().run({"command": "exit 3"}, ToolContext(cwd=tmp_path))
    assert r.is_error and "(exit 3)" in r.output


async def test_timeout_kills_process_group(tmp_path):
    start = time.monotonic()
    r = await BashTool(timeout_s=0.3).run({"command": "sleep 30"}, ToolContext(cwd=tmp_path))
    assert time.monotonic() - start < 5
    assert r.is_error and "timed out" in r.output.lower()


async def test_cancellation_kills_process(tmp_path):
    task = asyncio.create_task(
        BashTool().run({"command": "sleep 30"}, ToolContext(cwd=tmp_path)))
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bash_tool.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/tools/bash.py
from __future__ import annotations

import asyncio
import os
import signal

from forge.tools.base import Tool, ToolContext, ToolResult, truncate_middle


class BashTool(Tool):
    name = "bash"
    description = ("Run a shell command in the session working directory. "
                   "stdout and stderr are merged.")
    params = {"type": "object", "properties": {"command": {"type": "string"}},
              "required": ["command"]}

    def __init__(self, timeout_s: float = 120):
        self.timeout_s = timeout_s

    def display(self, args: dict) -> str:
        return args.get("command", "bash")

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        proc = await asyncio.create_subprocess_shell(
            args["command"], cwd=str(ctx.cwd),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        parts: list[str] = []
        try:
            async with asyncio.timeout(self.timeout_s):
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    text = chunk.decode(errors="replace")
                    parts.append(text)
                    ctx.emit_chunk(text)
                await proc.wait()
        except TimeoutError:
            _kill(proc)
            out = truncate_middle("".join(parts))
            return ToolResult(
                output=f"{out}\nCommand timed out after {self.timeout_s}s", is_error=True)
        except asyncio.CancelledError:
            _kill(proc)
            raise
        out = truncate_middle("".join(parts)).rstrip("\n")
        if proc.returncode != 0:
            return ToolResult(output=f"{out}\n(exit {proc.returncode})", is_error=True)
        return ToolResult(output=out)


def _kill(proc) -> None:
    if proc.returncode is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_bash_tool.py -v` — Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add server/forge/tools/bash.py server/tests/test_bash_tool.py
git commit -m "feat(tools): bash tool with streaming, timeout, and group kill"
```

---

### Task 10: EventBus + Scheduler

**Files:**
- Create: `server/forge/engine/bus.py`
- Create: `server/forge/engine/scheduler.py`
- Test: `server/tests/test_bus_scheduler.py`

**Interfaces:**
- Produces:
  - `EventBus` — `.subscribe() -> asyncio.Queue`, `.unsubscribe(q)`, `.publish(event)` (put_nowait to all subscribers; accepts durable or ephemeral models).
  - `Scheduler(max_concurrent: int)` — `slot(on_queued: Callable[[], None])` async context manager: calls `on_queued()` if it must wait, then acquires; FIFO via `asyncio.Semaphore`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_bus_scheduler.py
import asyncio

from forge.engine.bus import EventBus
from forge.engine.events import TextDelta
from forge.engine.scheduler import Scheduler


async def test_bus_fans_out():
    bus = EventBus()
    q1, q2 = bus.subscribe(), bus.subscribe()
    bus.publish(TextDelta(session_id="s1", text="x"))
    assert (await q1.get()).text == "x" and (await q2.get()).text == "x"
    bus.unsubscribe(q2)
    bus.publish(TextDelta(session_id="s1", text="y"))
    assert q2.empty()


async def test_scheduler_queues_beyond_cap():
    sched = Scheduler(max_concurrent=1)
    order: list[str] = []

    async def job(name: str, hold: float):
        def on_queued(): order.append(f"{name}:queued")
        async with sched.slot(on_queued):
            order.append(f"{name}:run")
            await asyncio.sleep(hold)

    t1 = asyncio.create_task(job("a", 0.2))
    await asyncio.sleep(0.05)
    t2 = asyncio.create_task(job("b", 0))
    await asyncio.gather(t1, t2)
    assert order == ["a:run", "b:queued", "b:run"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bus_scheduler.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/engine/bus.py
from __future__ import annotations

import asyncio


class EventBus:
    def __init__(self):
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event) -> None:
        for q in self._subs:
            q.put_nowait(event)
```

```python
# server/forge/engine/scheduler.py
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Callable


class Scheduler:
    def __init__(self, max_concurrent: int):
        self._sem = asyncio.Semaphore(max_concurrent)

    @asynccontextmanager
    async def slot(self, on_queued: Callable[[], None]):
        if self._sem.locked():
            on_queued()
        async with self._sem:
            yield
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_bus_scheduler.py -v` — Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add server/forge/engine/bus.py server/forge/engine/scheduler.py server/tests/test_bus_scheduler.py
git commit -m "feat(engine): event bus and run scheduler"
```

---

### Task 11: SessionActor — the agent loop

**Files:**
- Create: `server/forge/engine/actor.py`
- Create: `server/forge/tools/registry.py`
- Test: `server/tests/test_actor.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `SessionMeta(id, name, cwd, model, autonomy, status)` (pydantic; `status` defaults `"idle"`, `name` defaults `"New session"`)
  - `default_tools(skill_dirs: list[Path]) -> dict[str, Tool]` — registry of all Task 7-9 tools (+ `load_skill` after Task 14; registry takes `skill_dirs` now, `LoadSkillTool` is added in Task 14).
  - `SessionActor(meta: SessionMeta, home: Path, config: ForgeConfig, llm: LLMClient, bus: EventBus, scheduler: Scheduler, system_prompt_fn: Callable[[SessionMeta], str])` with:
    - `.log: EventLog` (at `<home>/sessions/<id>/events.jsonl`), `.changesets: ChangesetStore` (at `<home>/sessions/<id>/`)
    - `.emit(event) -> Event` (stamp via log + publish durable), `.publish_ephemeral(event)`
    - `async .post_message(text: str)` — appends `UserMessage`; auto-names session from first message (truncate 40 chars, emit `SessionRenamed`); starts `_run` task if not running (steering otherwise)
    - `async .resolve_approval(call_id, decision, always: dict | None)` (Task 12)
    - `.cancel()`, `.set_autonomy(a)`, `.session_policies: list[Policy]`
    - `.run_task: asyncio.Task | None`
  - Loop behavior (this task, with FakeLLM): text-only run → `assistant_message` + `run_finished(completed)` + statuses `running→idle`; tool run → executes each call (started/finished events, `auto_approved=True` for non-read-only in yolo), loops; tool `is_error` result feeds back to the model and the run continues; `LLMError` → `error` event + `run_finished(error)`; malformed tool JSON args → error tool result, run continues; a user message landing between iterations is consumed before finishing (loop re-checks for newer `user_message` events before exiting).

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_actor.py
import asyncio
from pathlib import Path

import pytest

from forge.engine.actor import SessionActor, SessionMeta
from forge.engine.bus import EventBus
from forge.engine.events import ToolCallSpec
from forge.engine.scheduler import Scheduler
from forge.llm.base import CompletionResult, LLMError
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig


def make_actor(tmp_path, script, autonomy="yolo", delay=0.0):
    meta = SessionMeta(id="s1", cwd=str(tmp_path / "ws"), model="m", autonomy=autonomy)
    (tmp_path / "ws").mkdir(exist_ok=True)
    llm = FakeLLM(script, delay=delay)
    actor = SessionActor(
        meta=meta, home=tmp_path / "home", config=ForgeConfig(),
        llm=llm, bus=EventBus(), scheduler=Scheduler(3),
        system_prompt_fn=lambda m: "SYS")
    return actor, llm


def types(actor):
    return [e.type for e in actor.log.read()]


async def wait_idle(actor):
    await asyncio.wait_for(actor.run_task, timeout=5)


async def test_text_only_run(tmp_path):
    actor, llm = make_actor(tmp_path, [CompletionResult(text="hi!", usage_tokens=10)])
    await actor.post_message("hello")
    await wait_idle(actor)
    assert types(actor) == [
        "user_message", "session_renamed", "status_changed",  # running
        "assistant_message", "run_finished", "status_changed"]  # idle
    assert actor.meta.name == "hello" and actor.meta.status == "idle"
    assert llm.calls[0][0]["content"] == "SYS"


async def test_tool_run_yolo_auto_approves(tmp_path):
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo ok"}')],
            usage_tokens=10),
        CompletionResult(text="done", usage_tokens=20),
    ])
    await actor.post_message("run echo")
    await wait_idle(actor)
    evs = actor.log.read()
    started = next(e for e in evs if e.type == "tool_call_started")
    finished = next(e for e in evs if e.type == "tool_call_finished")
    assert started.auto_approved and started.display == "echo ok"
    assert "ok" in finished.output and not finished.is_error
    # second LLM call saw the tool result
    assert llm.calls[1][-1]["role"] == "tool"


async def test_tool_error_feeds_back_and_run_continues(tmp_path):
    actor, _ = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="read_file", arguments='{"path": "nope"}')],
            usage_tokens=10),
        CompletionResult(text="recovered", usage_tokens=20),
    ])
    await actor.post_message("go")
    await wait_idle(actor)
    evs = actor.log.read()
    fin = next(e for e in evs if e.type == "tool_call_finished")
    assert fin.is_error
    assert evs[-2].type == "run_finished" and evs[-2].reason == "completed"


async def test_llm_error_ends_run_with_error(tmp_path):
    actor, _ = make_actor(tmp_path, [LLMError("proxy down")])
    await actor.post_message("go")
    await wait_idle(actor)
    assert "error" in types(actor)
    fin = [e for e in actor.log.read() if e.type == "run_finished"]
    assert fin[0].reason == "error" and actor.meta.status == "idle"


async def test_message_during_final_stream_is_consumed(tmp_path):
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="first", usage_tokens=10),
        CompletionResult(text="second", usage_tokens=20),
    ], delay=0.1)  # hold each completion in flight
    await actor.post_message("one")
    await asyncio.sleep(0.05)          # let the run start its first LLM call
    await actor.post_message("two")    # lands mid-flight → must not be dropped
    await wait_idle(actor)
    assert len(llm.calls) == 2  # loop continued instead of finishing
    assert [e.text for e in actor.log.read() if e.type == "assistant_message"] == [
        "first", "second"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_actor.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/tools/registry.py
from __future__ import annotations

from pathlib import Path

from forge.tools.base import Tool
from forge.tools.bash import BashTool
from forge.tools.files_read import ReadFileTool
from forge.tools.files_write import EditFileTool, WriteFileTool
from forge.tools.search import GlobTool, GrepTool, ListDirTool


def default_tools(skill_dirs: list[Path]) -> dict[str, Tool]:
    tools: list[Tool] = [
        BashTool(), ReadFileTool(), WriteFileTool(), EditFileTool(),
        GlobTool(), GrepTool(), ListDirTool(),
    ]
    # LoadSkillTool(skill_dirs) is appended here in the skills task
    return {t.name: t for t in tools}
```

```python
# server/forge/engine/actor.py
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from forge.engine.bus import EventBus
from forge.engine.events import (
    ApprovalRequested, ApprovalResolved, AssistantMessage, Autonomy,
    AutonomyChanged, ContextCompacted, ErrorEvent, OutputChunk, PolicyAdded,
    RunFinished, SessionRenamed, Status, StatusChanged, TextDelta,
    ToolCallFinished, ToolCallSpec, ToolCallStarted, UserMessage,
)
from forge.engine.projection import dangling_call_ids, to_messages
from forge.engine.scheduler import Scheduler
from forge.llm.base import LLMClient, LLMError
from forge.store.changesets import ChangesetStore
from forge.store.config import ForgeConfig, Policy, policy_matches, save_global_policy
from forge.store.eventlog import EventLog
from forge.tools.base import ToolContext, openai_spec
from forge.tools.registry import default_tools

COMPACT_THRESHOLD = 0.75


class SessionMeta(BaseModel):
    id: str
    name: str = "New session"
    cwd: str
    model: str
    autonomy: Autonomy = "yolo"
    status: Status = "idle"


class SessionActor:
    def __init__(self, meta: SessionMeta, home: Path, config: ForgeConfig,
                 llm: LLMClient, bus: EventBus, scheduler: Scheduler,
                 system_prompt_fn: Callable[[SessionMeta], str]):
        self.meta = meta
        self.home = home
        self.config = config
        self.llm = llm
        self.bus = bus
        self.scheduler = scheduler
        self.system_prompt_fn = system_prompt_fn
        sdir = home / "sessions" / meta.id
        self.log = EventLog(sdir / "events.jsonl")
        self.changesets = ChangesetStore(sdir)
        self.tools = default_tools(
            [home / "skills", Path(meta.cwd) / ".forge" / "skills"])
        self.session_policies: list[Policy] = []
        self.run_task: asyncio.Task | None = None
        self._approvals: dict[str, asyncio.Future] = {}

    # -- event helpers ------------------------------------------------------
    def emit(self, event):
        stamped = self.log.append(event)
        self.bus.publish(stamped)
        return stamped

    def publish_ephemeral(self, event) -> None:
        self.bus.publish(event)

    def _e(self, cls, **kw):
        return cls(session_id=self.meta.id, ts=time.time(), **kw)

    def _set_status(self, status: Status) -> None:
        if self.meta.status != status:
            self.meta.status = status
            self.emit(self._e(StatusChanged, status=status))

    # -- commands ------------------------------------------------------------
    async def post_message(self, text: str) -> None:
        self.emit(self._e(UserMessage, text=text))
        if self.meta.name == "New session":
            self.meta.name = text[:40]
            self.emit(self._e(SessionRenamed, name=self.meta.name))
        if self.run_task is None or self.run_task.done():
            self.run_task = asyncio.create_task(self._run())

    def set_autonomy(self, autonomy: Autonomy) -> None:
        self.meta.autonomy = autonomy
        self.emit(self._e(AutonomyChanged, autonomy=autonomy))

    def cancel(self) -> None:
        if self.run_task and not self.run_task.done():
            self.run_task.cancel()

    async def resolve_approval(self, call_id: str, decision: str,
                               always: dict | None = None) -> None:
        fut = self._approvals.pop(call_id, None)
        if fut and not fut.done():
            fut.set_result((decision, always))

    # -- run loop -------------------------------------------------------------
    async def _run(self) -> None:
        async with self.scheduler.slot(lambda: self._set_status("queued")):
            self._set_status("running")
            try:
                await self._loop()
                self.emit(self._e(RunFinished, reason="completed"))
            except asyncio.CancelledError:
                self._close_dangling("Cancelled by user")
                self.emit(self._e(RunFinished, reason="cancelled"))
            except LLMError as e:
                self.emit(self._e(ErrorEvent, message=str(e)))
                self.emit(self._e(RunFinished, reason="error"))
            finally:
                self._set_status("idle")

    async def _loop(self) -> None:
        while True:
            start_seq = self.log.last_seq

            async def on_delta(text: str) -> None:
                self.publish_ephemeral(self._e(TextDelta, text=text))

            result = await self.llm.complete(
                self.meta.model,
                to_messages(self.log.read(), self.system_prompt_fn(self.meta)),
                [openai_spec(t) for t in self.tools.values()],
                on_delta)
            self.emit(self._e(AssistantMessage, text=result.text,
                              tool_calls=result.tool_calls))
            if not result.tool_calls:
                if any(e.type == "user_message" and e.seq > start_seq
                       for e in self.log.read(after_seq=start_seq)):
                    continue  # steering arrived during final stream
                return
            for call in result.tool_calls:
                await self._execute_call(call)
            await self._maybe_compact(result.usage_tokens)

    async def _execute_call(self, call: ToolCallSpec) -> None:
        tool = self.tools.get(call.name)
        if tool is None:
            self.emit(self._e(ToolCallFinished, call_id=call.id, tool=call.name,
                              output=f"Unknown tool: {call.name}", is_error=True))
            return
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as e:
            self.emit(self._e(ToolCallFinished, call_id=call.id, tool=call.name,
                              output=f"Invalid tool arguments JSON: {e}", is_error=True))
            return
        display = tool.display(args)

        auto = False
        if not tool.read_only:
            policies = self.config.policies + self.session_policies
            if policy_matches(policies, call.name, display):
                auto = True
            elif self.meta.autonomy == "yolo":
                auto = True
            else:
                allowed = await self._gate(call, display)
                if not allowed:
                    return

        self.emit(self._e(ToolCallStarted, call_id=call.id, tool=call.name,
                          display=display, auto_approved=auto))
        ctx = ToolContext(
            cwd=Path(self.meta.cwd),
            emit_chunk=lambda t: self.publish_ephemeral(
                self._e(OutputChunk, call_id=call.id, text=t)),
            changesets=self.changesets)
        started = time.monotonic()
        try:
            result = await tool.run(args, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # tool bug → feed back, don't kill the run
            result_output, is_error, stats = f"Tool crashed: {e!r}", True, None
        else:
            result_output, is_error, stats = result.output, result.is_error, result.diff_stats
        self.emit(self._e(
            ToolCallFinished, call_id=call.id, tool=call.name,
            output=result_output or "(no output)", is_error=is_error,
            duration_ms=int((time.monotonic() - started) * 1000), diff_stats=stats))

    async def _gate(self, call: ToolCallSpec, display: str) -> bool:
        self.emit(self._e(ApprovalRequested, call_id=call.id, tool=call.name,
                          display=display))
        self._set_status("attention")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._approvals[call.id] = fut
        try:
            decision, always = await fut
        finally:
            self._approvals.pop(call.id, None)
            self._set_status("running")
        self.emit(self._e(ApprovalResolved, call_id=call.id, decision=decision))
        if always:
            policy = Policy(tool=call.name, pattern=always["pattern"])
            scope = always.get("scope", "session")
            if scope == "global":
                save_global_policy(self.home, policy)
                self.config.policies.append(policy)
            else:
                self.session_policies.append(policy)
            self.emit(self._e(PolicyAdded, tool=policy.tool, pattern=policy.pattern,
                              scope=scope))
        if decision == "deny":
            self.emit(self._e(ToolCallFinished, call_id=call.id, tool=call.name,
                              output="User denied this action.", is_error=True))
            return False
        return True

    async def _maybe_compact(self, usage_tokens: int) -> None:
        window = self.config.context_window(self.meta.model)
        if usage_tokens <= COMPACT_THRESHOLD * window:
            return
        msgs = to_messages(self.log.read(), "")[1:]  # drop system stub
        transcript = "\n".join(
            f"{m['role'].upper()}: {m.get('content') or m.get('tool_calls', '')}"
            for m in msgs)[-200_000:]

        async def no_delta(_: str) -> None: ...

        summary = await self.llm.complete(
            self.meta.model,
            [{"role": "user", "content":
              "Summarize this agent session so far for continuation. Include the "
              "original task, key decisions, files touched, current progress, and "
              "immediate next steps.\n\n" + transcript}],
            [], no_delta)
        self.emit(self._e(ContextCompacted, summary=summary.text,
                          upto_seq=self.log.last_seq))

    def _close_dangling(self, reason: str) -> None:
        for call_id, tool in dangling_call_ids(self.log.read()):
            self.emit(self._e(ToolCallFinished, call_id=call_id, tool=tool,
                              output=f"[{reason} — no result]", is_error=True))
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_actor.py -v` — Expected: 5 PASS. Then full suite: `uv run pytest -q` — all green.

- [ ] **Step 5: Commit**

```bash
git add server/forge/engine/actor.py server/forge/tools/registry.py server/tests/test_actor.py
git commit -m "feat(engine): session actor with agent loop, steering, error recovery"
```

---

### Task 12: Approvals in guarded mode + cancel

**Files:**
- Modify: none expected (Task 11 already implements `_gate`, `resolve_approval`, `cancel`) — this task **proves** the behavior with tests and fixes whatever falls out.
- Test: `server/tests/test_approvals.py`

**Interfaces:**
- Consumes: `SessionActor` from Task 11.
- Produces: verified guarded-mode semantics (gate event, attention status, allow/deny paths, Always policies incl. global persistence, cancel closing dangling calls).

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_approvals.py
import asyncio

from forge.engine.events import ToolCallSpec
from forge.llm.base import CompletionResult
from forge.store.config import load_config

from tests.test_actor import make_actor, wait_idle

BASH_CALL = CompletionResult(text="", tool_calls=[
    ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo hi"}')],
    usage_tokens=10)
DONE = CompletionResult(text="done", usage_tokens=20)


async def pump_until(actor, etype, timeout=5):
    async with asyncio.timeout(timeout):
        while not any(e.type == etype for e in actor.log.read()):
            await asyncio.sleep(0.01)


async def test_guarded_allow(tmp_path):
    actor, _ = make_actor(tmp_path, [BASH_CALL, DONE], autonomy="guarded")
    await actor.post_message("go")
    await pump_until(actor, "approval_requested")
    assert actor.meta.status == "attention"
    await actor.resolve_approval("c1", "allow")
    await wait_idle(actor)
    evs = actor.log.read()
    assert next(e for e in evs if e.type == "approval_resolved").decision == "allow"
    started = next(e for e in evs if e.type == "tool_call_started")
    assert not started.auto_approved


async def test_guarded_deny_feeds_model(tmp_path):
    actor, llm = make_actor(tmp_path, [BASH_CALL, DONE], autonomy="guarded")
    await actor.post_message("go")
    await pump_until(actor, "approval_requested")
    await actor.resolve_approval("c1", "deny")
    await wait_idle(actor)
    fin = next(e for e in actor.log.read() if e.type == "tool_call_finished")
    assert fin.is_error and "denied" in fin.output.lower()
    assert llm.calls[1][-1]["role"] == "tool"  # denial visible to model
    assert not any(e.type == "tool_call_started" for e in actor.log.read())


async def test_always_global_persists_policy(tmp_path):
    actor, _ = make_actor(tmp_path, [BASH_CALL, DONE, BASH_CALL, DONE],
                          autonomy="guarded")
    await actor.post_message("go")
    await pump_until(actor, "approval_requested")
    await actor.resolve_approval(
        "c1", "allow", always={"pattern": "echo *", "scope": "global"})
    await wait_idle(actor)
    assert any(e.type == "policy_added" and e.scope == "global"
               for e in actor.log.read())
    assert any(p.pattern == "echo *" for p in load_config(tmp_path / "home").policies)
    # second identical call sails through without a gate
    await actor.post_message("again")
    await wait_idle(actor)
    assert sum(1 for e in actor.log.read() if e.type == "approval_requested") == 1


async def test_cancel_mid_gate_closes_dangling(tmp_path):
    actor, _ = make_actor(tmp_path, [BASH_CALL], autonomy="guarded")
    await actor.post_message("go")
    await pump_until(actor, "approval_requested")
    actor.cancel()
    await asyncio.wait_for(asyncio.gather(actor.run_task, return_exceptions=True), 5)
    evs = actor.log.read()
    assert any(e.type == "run_finished" and e.reason == "cancelled" for e in evs)
    fin = next(e for e in evs if e.type == "tool_call_finished")
    assert "cancelled" in fin.output.lower()
    assert actor.meta.status == "idle"
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_approvals.py -v` — Expected: mostly PASS if Task 11 is correct; FIX any failures in `actor.py` (likely spots: gate future cancellation on task cancel, status transitions).

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q` — Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add server/tests/test_approvals.py server/forge/engine/actor.py
git commit -m "test(engine): prove guarded approvals, Always policies, cancel semantics"
```

---

### Task 13: Compaction end-to-end

**Files:**
- Modify: `server/forge/engine/actor.py` (only if tests reveal gaps)
- Test: `server/tests/test_compaction.py`

**Interfaces:**
- Consumes: `SessionActor`, `FakeLLM`, `ContextCompacted`, `to_messages`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_compaction.py
from forge.engine.events import ToolCallSpec
from forge.engine.projection import to_messages
from forge.llm.base import CompletionResult
from forge.store.config import ForgeConfig, ModelConfig

from tests.test_actor import make_actor, wait_idle


async def test_compaction_triggers_and_projection_shrinks(tmp_path):
    # tiny context window so the first tool turn crosses 75%
    actor, llm = make_actor(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo x"}')],
            usage_tokens=90),                                   # 90 > 0.75 * 100
        CompletionResult(text="summary of session", usage_tokens=5),  # summarizer call
        CompletionResult(text="done", usage_tokens=10),
    ])
    actor.config = ForgeConfig(models=[ModelConfig(id="m", context_window=100)])
    await actor.post_message("go")
    await wait_idle(actor)
    evs = actor.log.read()
    comp = next(e for e in evs if e.type == "context_compacted")
    assert comp.summary == "summary of session"
    # the LLM call AFTER compaction starts from the summary, not raw history
    final_msgs = llm.calls[2]
    assert "summary of session" in final_msgs[1]["content"]
    assert len(final_msgs) == 2  # system + summary-as-user only
    # projection helper agrees
    msgs = to_messages(evs, "SYS")
    assert not any(m["role"] == "tool" for m in msgs)
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_compaction.py -v` — Expected: PASS if Task 11's `_maybe_compact` is right; fix `actor.py` if not (watch: summarizer call must not stream deltas to the UI; `upto_seq` must be captured **after** tool results are appended).

- [ ] **Step 3: Full suite + commit**

Run: `uv run pytest -q` — all green.

```bash
git add server/tests/test_compaction.py server/forge/engine/actor.py
git commit -m "test(engine): context compaction end-to-end"
```

---

### Task 14: Skills, memory, system prompt

**Files:**
- Create: `server/forge/engine/skills.py`
- Create: `server/forge/tools/skills_tool.py`
- Create: `server/forge/engine/sysprompt.py`
- Modify: `server/forge/tools/registry.py` (add `LoadSkillTool(skill_dirs)`)
- Test: `server/tests/test_skills_sysprompt.py`

**Interfaces:**
- Produces:
  - `SkillMeta(name, description, path)`; `discover_skills(dirs: list[Path]) -> list[SkillMeta]` — each subdir containing `SKILL.md` with YAML frontmatter (`name`, `description`); later dirs override earlier on name collision (pass `[global, project]`).
  - `LoadSkillTool(dirs: list[Path])` — `read_only = True`; `run({"name": ...})` re-discovers, returns SKILL.md body (frontmatter stripped) plus a listing of bundled files.
  - `build_system_prompt(meta: SessionMeta, home: Path) -> str` — sections: identity + environment (OS, cwd, date, model); global `<home>/FORGE.md`; project `<cwd>/FORGE.md` else `<cwd>/AGENTS.md`; memory index `<home>/memory/MEMORY.md` + how-to-maintain-memory instructions (save durable facts as one file each under `<home>/memory/`, keep `MEMORY.md` index current, update rather than duplicate); skills index (name — description per line, with instruction to call `load_skill`); behavioral guidelines.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_skills_sysprompt.py
from forge.engine.actor import SessionMeta
from forge.engine.skills import discover_skills
from forge.engine.sysprompt import build_system_prompt
from forge.tools.base import ToolContext
from forge.tools.skills_tool import LoadSkillTool


def make_skill(root, name, desc="does things", body="Step one."):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n")
    (d / "helper.sh").write_text("echo hi\n")
    return d


def test_discovery_project_overrides_global(tmp_path):
    make_skill(tmp_path / "g", "deploy", desc="global deploy")
    make_skill(tmp_path / "p", "deploy", desc="project deploy")
    make_skill(tmp_path / "g", "review")
    skills = discover_skills([tmp_path / "g", tmp_path / "p"])
    by_name = {s.name: s for s in skills}
    assert by_name["deploy"].description == "project deploy"
    assert set(by_name) == {"deploy", "review"}


async def test_load_skill_returns_body_and_files(tmp_path):
    make_skill(tmp_path / "g", "deploy", body="Run helper.sh first.")
    tool = LoadSkillTool([tmp_path / "g"])
    r = await tool.run({"name": "deploy"}, ToolContext(cwd=tmp_path))
    assert "Run helper.sh first." in r.output and "helper.sh" in r.output
    assert "---" not in r.output  # frontmatter stripped
    missing = await tool.run({"name": "nope"}, ToolContext(cwd=tmp_path))
    assert missing.is_error


def test_system_prompt_sections(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    cwd.mkdir()
    (home / "memory").mkdir(parents=True)
    (home / "FORGE.md").write_text("Global rule: be terse.")
    (cwd / "AGENTS.md").write_text("Project rule: use uv.")
    (home / "memory" / "MEMORY.md").write_text("- user prefers pnpm")
    make_skill(home / "skills", "deploy", desc="ship it")
    meta = SessionMeta(id="s1", cwd=str(cwd), model="m")
    sp = build_system_prompt(meta, home)
    for needle in ["Global rule: be terse.", "Project rule: use uv.",
                   "user prefers pnpm", "deploy", "ship it", "load_skill",
                   str(cwd)]:
        assert needle in sp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_skills_sysprompt.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/engine/skills.py
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class SkillMeta(BaseModel):
    name: str
    description: str
    path: str  # directory containing SKILL.md


def parse_skill_md(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns ({}, text) if no frontmatter."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return yaml.safe_load(parts[1]) or {}, parts[2].strip()
    return {}, text.strip()


def discover_skills(dirs: list[Path]) -> list[SkillMeta]:
    found: dict[str, SkillMeta] = {}
    for root in dirs:  # later dirs override earlier
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            md = d / "SKILL.md"
            if not md.is_file():
                continue
            fm, _ = parse_skill_md(md.read_text())
            name = fm.get("name", d.name)
            found[name] = SkillMeta(
                name=name, description=fm.get("description", ""), path=str(d))
    return list(found.values())
```

```python
# server/forge/tools/skills_tool.py
from __future__ import annotations

from pathlib import Path

from forge.engine.skills import discover_skills, parse_skill_md
from forge.tools.base import Tool, ToolContext, ToolResult


class LoadSkillTool(Tool):
    name = "load_skill"
    description = "Load the full instructions of a skill by name."
    params = {"type": "object", "properties": {"name": {"type": "string"}},
              "required": ["name"]}
    read_only = True

    def __init__(self, dirs: list[Path]):
        self.dirs = dirs

    def display(self, args: dict) -> str:
        return args.get("name", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        for s in discover_skills(self.dirs):
            if s.name == args["name"]:
                d = Path(s.path)
                _, body = parse_skill_md((d / "SKILL.md").read_text())
                extras = sorted(p.name for p in d.iterdir() if p.name != "SKILL.md")
                files = f"\n\nBundled files in {d}: {', '.join(extras)}" if extras else ""
                return ToolResult(output=body + files)
        return ToolResult(output=f"No skill named {args['name']!r}", is_error=True)
```

```python
# server/forge/engine/sysprompt.py
from __future__ import annotations

import platform
from datetime import date
from pathlib import Path

from forge.engine.skills import discover_skills

GUIDELINES = """\
## Guidelines
- Prefer the dedicated tools (read_file, edit_file, glob, grep) over bash equivalents.
- Verify your work: run tests or re-read files after changing them.
- Be concise in prose; the user sees your text between tool calls.
- When a task teaches you a durable fact about the user or a project, save it to memory."""

MEMORY_HOWTO = """\
## Memory
Your persistent memory lives at {mem_dir}. The index below is loaded every session.
Save one durable fact per markdown file in that directory and keep {mem_dir}/MEMORY.md
updated with one line per fact. Update or delete stale facts rather than duplicating.

### Memory index
{index}"""


def _read(path: Path) -> str:
    return path.read_text().strip() if path.is_file() else ""


def build_system_prompt(meta, home: Path) -> str:
    cwd = Path(meta.cwd)
    parts = [
        "You are Forge, a capable local agent operating on the user's machine "
        "with shell and file access.",
        f"## Environment\nOS: {platform.system()} · cwd: {cwd} · "
        f"date: {date.today().isoformat()} · model: {meta.model}",
    ]
    if g := _read(home / "FORGE.md"):
        parts.append("## Global instructions\n" + g)
    if p := (_read(cwd / "FORGE.md") or _read(cwd / "AGENTS.md")):
        parts.append("## Project instructions\n" + p)
    mem_dir = home / "memory"
    parts.append(MEMORY_HOWTO.format(
        mem_dir=mem_dir, index=_read(mem_dir / "MEMORY.md") or "(empty)"))
    skills = discover_skills([home / "skills", cwd / ".forge" / "skills"])
    if skills:
        lines = "\n".join(f"- {s.name} — {s.description}" for s in skills)
        parts.append("## Skills\nCall load_skill(name) before tasks a skill covers.\n"
                     + lines)
    parts.append(GUIDELINES)
    return "\n\n".join(parts)
```

In `server/forge/tools/registry.py`, add the import and append the tool:

```python
from forge.tools.skills_tool import LoadSkillTool
```

and change the tool list to end with `LoadSkillTool(skill_dirs),` (replace the placeholder comment).

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_skills_sysprompt.py -v` — Expected: 3 PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
git add server/forge/engine/skills.py server/forge/tools/skills_tool.py server/forge/engine/sysprompt.py server/forge/tools/registry.py server/tests/test_skills_sysprompt.py
git commit -m "feat(engine): skills discovery, load_skill tool, system prompt with memory"
```

---

### Task 15: SessionManager + rehydration

**Files:**
- Create: `server/forge/engine/manager.py`
- Test: `server/tests/test_manager.py`

**Interfaces:**
- Consumes: `SessionActor`, `SessionMeta`, `build_system_prompt`, `ForgeConfig`, `EventBus`, `Scheduler`, `EventLog`, `dangling_call_ids`.
- Produces: `SessionManager(home: Path, config: ForgeConfig, llm: LLMClient, bus: EventBus)` with:
  - `.scheduler: Scheduler(config.max_concurrent)`
  - `.create(cwd: str | None = None, model: str | None = None, autonomy: str | None = None) -> SessionActor` — id = `uuid4().hex[:8]`; cwd defaults to the most recently created session's cwd, else `Path.home()`; emits `session_created`
  - `.get(session_id) -> SessionActor` (KeyError if unknown)
  - `.list() -> list[SessionMeta]`
  - `.rehydrate() -> None` — scans `<home>/sessions/*/events.jsonl`; rebuilds each `SessionMeta` by replaying `session_created` / `session_renamed` / `autonomy_changed`; if the log ends mid-run (a `user_message`/`assistant_message`/`tool_call_started` after the last `run_finished`), closes dangling calls with `"[Interrupted by server restart — no result]"` and appends `run_finished(interrupted)`; status always `idle` after rehydrate.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_manager.py
from forge.engine.bus import EventBus
from forge.engine.manager import SessionManager
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig

from tests.test_actor import wait_idle


def make_manager(tmp_path, script=()):
    return SessionManager(home=tmp_path / "home", config=ForgeConfig(),
                          llm=FakeLLM(list(script)), bus=EventBus())


def test_create_defaults_cascade(tmp_path):
    mgr = make_manager(tmp_path)
    a = mgr.create(cwd=str(tmp_path))
    b = mgr.create()  # inherits previous session's cwd
    assert b.meta.cwd == str(tmp_path)
    assert a.meta.autonomy == "yolo" and a.meta.model == ForgeConfig().default_model
    assert {m.id for m in mgr.list()} == {a.meta.id, b.meta.id}
    assert a.log.read()[0].type == "session_created"


async def test_rehydrate_restores_and_marks_interrupted(tmp_path):
    mgr = make_manager(tmp_path, [CompletionResult(text="ok", usage_tokens=1)])
    a = mgr.create(cwd=str(tmp_path))
    await a.post_message("do the thing")
    await wait_idle(a)
    # simulate a crash mid-run on a second session: log ends right after a
    # user_message with no run_finished (create() already emitted session_created)
    from forge.engine.events import UserMessage

    b = mgr.create()
    b.emit(UserMessage(session_id=b.meta.id, ts=0.0, text="crashed mid-run"))

    mgr2 = SessionManager(home=tmp_path / "home", config=ForgeConfig(),
                          llm=FakeLLM([]), bus=EventBus())
    mgr2.rehydrate()
    metas = {m.id: m for m in mgr2.list()}
    assert metas[a.meta.id].name == "do the thing"
    assert metas[a.meta.id].status == "idle"
    evs = mgr2.get(b.meta.id).log.read()
    assert evs[-1].type == "run_finished" and evs[-1].reason == "interrupted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_manager.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/engine/manager.py
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
               autonomy: str | None = None) -> SessionActor:
        if cwd is None:
            last = self.actors.get(self._creation_order[-1]) if self._creation_order else None
            cwd = last.meta.cwd if last else str(Path.home())
        meta = SessionMeta(
            id=uuid4().hex[:8], cwd=cwd,
            model=model or self.config.default_model,
            autonomy=autonomy or self.config.default_autonomy)
        actor = self._make_actor(meta)
        actor.emit(SessionCreated(
            session_id=meta.id, ts=time.time(), name=meta.name, cwd=meta.cwd,
            model=meta.model, autonomy=meta.autonomy))
        return actor

    def get(self, session_id: str) -> SessionActor:
        return self.actors[session_id]

    def list(self) -> list[SessionMeta]:
        return [self.actors[i].meta for i in self._creation_order]

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
                                   model=e.model, autonomy=e.autonomy)
            elif meta and e.type == "session_renamed":
                meta.name = e.name
            elif meta and e.type == "autonomy_changed":
                meta.autonomy = e.autonomy
        if meta:
            meta.status = "idle"
        return meta
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_manager.py -v` — Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
git add server/forge/engine/manager.py server/tests/test_manager.py
git commit -m "feat(engine): session manager with restart rehydration"
```

---

### Task 16: FastAPI app — REST, WebSocket, integration

**Files:**
- Create: `server/forge/api/app.py`
- Create: `server/forge/api/schemas.py`
- Create: `server/forge/protocol_export.py`
- Create: `server/Makefile`
- Test: `server/tests/test_api.py`

**Interfaces:**
- Produces: `create_app(home: Path, config: ForgeConfig, llm: LLMClient) -> FastAPI` (stores `SessionManager` on `app.state.manager`, calls `rehydrate()` at startup). Routes:
  - `GET /api/health` → `{"ok": bool}` (from `llm.healthy()`)
  - `GET /api/models` → `[{id, display_name, context_window}]`
  - `GET /api/sessions` → `[SessionMeta]`; `POST /api/sessions` body `{cwd?, model?, autonomy?}` → `SessionMeta`
  - `POST /api/sessions/{sid}/messages` body `{text}` → 202
  - `POST /api/sessions/{sid}/approvals/{call_id}` body `{decision: "allow"|"deny", always?: {pattern, scope}}` → 200
  - `POST /api/sessions/{sid}/cancel` → 200; `POST /api/sessions/{sid}/autonomy` body `{autonomy}` → 200; `PATCH /api/sessions/{sid}` body `{name}` → 200
  - `GET /api/sessions/{sid}/events?after=0` → durable events (hydration)
  - `GET /api/sessions/{sid}/changesets` → list; `POST .../changesets/{i}/revert`; `POST .../changesets/keep_all`
  - `GET /api/sessions/{sid}/files?q=` → fuzzy path search in session cwd (subsequence match, `SKIP_DIRS` skipped, ranked by path length, cap 50)
  - `GET /api/skills` → `[SkillMeta]` for the default dirs
  - `WS /ws` — client sends `{"cursors": {sid: last_seq}}` once after connect; server replays missed durable events for each cursor, then live-streams everything from the bus. Unknown session ids in cursors are ignored.
  - Static: if `web/dist` exists (path `../web/dist` relative to `server/`), mount it at `/`.
- `python -m forge.protocol_export` prints a JSON Schema bundle (events + SessionMeta + Changeset) for the web app's codegen.

- [ ] **Step 1: Write the failing integration tests**

```python
# server/tests/test_api.py
import json

from starlette.testclient import TestClient

from forge.api.app import create_app
from forge.engine.events import ToolCallSpec
from forge.llm.base import CompletionResult
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig, ModelConfig


def make_client(tmp_path, script, max_concurrent=3):
    cfg = ForgeConfig(models=[ModelConfig(id="m", display_name="m")],
                      default_model="m", max_concurrent=max_concurrent)
    app = create_app(home=tmp_path / "home", config=cfg, llm=FakeLLM(script))
    return TestClient(app)


def drain_until(ws, etype, limit=200):
    for _ in range(limit):
        e = json.loads(ws.receive_text())
        if e["type"] == etype:
            return e
    raise AssertionError(f"never saw {etype}")


def test_full_run_over_ws(tmp_path):
    client = make_client(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo hi"}')],
            usage_tokens=1),
        CompletionResult(text="done", usage_tokens=2),
    ])
    with client:
        sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"cursors": {sid: 0}}))
            r = client.post(f"/api/sessions/{sid}/messages", json={"text": "go"})
            assert r.status_code == 202
            started = drain_until(ws, "tool_call_started")
            assert started["auto_approved"] is True  # yolo default
            drain_until(ws, "run_finished")
        sessions = client.get("/api/sessions").json()
        assert sessions[0]["name"] == "go" and sessions[0]["status"] == "idle"
        events = client.get(f"/api/sessions/{sid}/events").json()
        assert any(e["type"] == "assistant_message" and e["text"] == "done"
                   for e in events)


def test_ws_replay_from_cursor(tmp_path):
    client = make_client(tmp_path, [CompletionResult(text="hi", usage_tokens=1)])
    with client:
        sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
        client.post(f"/api/sessions/{sid}/messages", json={"text": "hello"})
        import time
        for _ in range(100):  # wait for run to finish
            if client.get("/api/sessions").json()[0]["status"] == "idle":
                break
            time.sleep(0.05)
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"cursors": {sid: 0}}))
            e = json.loads(ws.receive_text())
            assert e["seq"] == 1 and e["type"] == "user_message"  # replayed


def test_guarded_approval_roundtrip(tmp_path):
    client = make_client(tmp_path, [
        CompletionResult(text="", tool_calls=[
            ToolCallSpec(id="c1", name="bash", arguments='{"command": "echo hi"}')],
            usage_tokens=1),
        CompletionResult(text="done", usage_tokens=2),
    ])
    with client:
        sid = client.post("/api/sessions", json={
            "cwd": str(tmp_path), "autonomy": "guarded"}).json()["id"]
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"cursors": {sid: 0}}))
            client.post(f"/api/sessions/{sid}/messages", json={"text": "go"})
            gate = drain_until(ws, "approval_requested")
            r = client.post(
                f"/api/sessions/{sid}/approvals/{gate['call_id']}",
                json={"decision": "allow"})
            assert r.status_code == 200
            drain_until(ws, "run_finished")


def test_file_search_and_misc_endpoints(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main_app.py").write_text("x")
    client = make_client(tmp_path, [])
    with client:
        sid = client.post("/api/sessions", json={"cwd": str(tmp_path)}).json()["id"]
        hits = client.get(f"/api/sessions/{sid}/files", params={"q": "mnpy"}).json()
        assert "src/main_app.py" in hits
        assert client.get("/api/health").json() == {"ok": True}
        assert client.get("/api/models").json()[0]["id"] == "m"
        assert client.get("/api/skills").json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# server/forge/api/schemas.py
from __future__ import annotations

from pydantic import BaseModel


class CreateSession(BaseModel):
    cwd: str | None = None
    model: str | None = None
    autonomy: str | None = None


class PostMessage(BaseModel):
    text: str


class AlwaysPolicy(BaseModel):
    pattern: str
    scope: str = "session"


class ResolveApproval(BaseModel):
    decision: str  # "allow" | "deny"
    always: AlwaysPolicy | None = None


class SetAutonomy(BaseModel):
    autonomy: str


class RenameSession(BaseModel):
    name: str
```

```python
# server/forge/api/app.py
from __future__ import annotations

import json
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
    app = FastAPI(title="Forge")
    bus = EventBus()
    manager = SessionManager(home=home, config=config, llm=llm, bus=bus)
    app.state.manager = manager

    @app.on_event("startup")
    async def startup():
        manager.rehydrate()

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
```

```python
# server/forge/protocol_export.py
"""Print a JSON Schema bundle of the wire protocol for web codegen."""
from __future__ import annotations

import json

from pydantic import TypeAdapter

from forge.engine.actor import SessionMeta
from forge.engine.events import Event, OutputChunk, TextDelta
from forge.store.changesets import Changeset

if __name__ == "__main__":
    bundle = {
        "event": TypeAdapter(Event).json_schema(),
        "text_delta": TextDelta.model_json_schema(),
        "output_chunk": OutputChunk.model_json_schema(),
        "session_meta": SessionMeta.model_json_schema(),
        "changeset": Changeset.model_json_schema(),
    }
    print(json.dumps(bundle, indent=2))
```

```makefile
# server/Makefile
.PHONY: dev test lint export-protocol

dev:
	uv run python -m forge.api.app

test:
	uv run pytest -q

lint:
	uv run ruff check .

export-protocol:
	uv run python -m forge.protocol_export > ../web/src/protocol/schema.json
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_api.py -v` — Expected: 4 PASS. Full suite: `uv run pytest -q` — all green. Lint: `uv run ruff check .` — clean.

- [ ] **Step 5: Smoke-test against the real CLIProxyAPI**

Run: `uv run python -m forge.api.app` then in another terminal:

```bash
curl -s http://127.0.0.1:8700/api/health          # {"ok": true} if CLIProxyAPI is up
SID=$(curl -s -X POST http://127.0.0.1:8700/api/sessions -H 'content-type: application/json' -d '{"cwd": "/tmp"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s -X POST http://127.0.0.1:8700/api/sessions/$SID/messages -H 'content-type: application/json' -d '{"text": "List the files in your cwd, then say hi"}'
sleep 8 && curl -s http://127.0.0.1:8700/api/sessions/$SID/events | python3 -m json.tool | tail -40
```

Expected: events show a `tool_call_started` (list_dir or bash) and a final `assistant_message`. If `/api/health` is false, check CLIProxyAPI is running on `:8317` and `~/.forge/config.toml` model ids match what `curl http://127.0.0.1:8317/v1/models` reports.

- [ ] **Step 6: Commit**

```bash
git add server/forge/api/ server/forge/protocol_export.py server/Makefile server/tests/test_api.py
git commit -m "feat(api): REST + WebSocket API, protocol export, live smoke test"
```

---

## Self-review checklist (run after Task 16)

- Full suite green: `uv run pytest -q`
- Lint clean: `uv run ruff check .`
- Spec sections all covered: events/store (T1-2), config/policies (T3), projection (T4), LLM (T5-6), tools (T7-9), bus/scheduler (T10), loop/steering/cancel (T11-12), compaction (T13), skills/memory/sysprompt (T14), sessions/rehydration (T15), API/WS/queue-over-API (T16).
- The web UI plan (`2026-07-10-forge-web.md`) is written next, consuming `make export-protocol` output and this API.
