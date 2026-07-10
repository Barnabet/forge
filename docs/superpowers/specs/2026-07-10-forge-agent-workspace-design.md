# Forge — Local Agent Workspace: V1 Design

**Date:** 2026-07-10
**Status:** Approved

## Overview

Forge (working name) is a local, personal, super-powerful agent app: a Claude Code / Codex-style
workspace served from the user's own machine. A Python engine runs agent sessions — LLM loop, tool
execution, approvals, skills, parallel sessions, a task queue — and a React SPA renders them using
the approved high-fidelity design in `design_handoff_agent_workspace/` (option **2a "Stream"**:
chat-first, inline tool cards, approval gates in the stream, slide-in detail drawer).

Models are served by **CLIProxyAPI**, already running locally: it wraps the user's Claude Code and
Codex accounts and exposes both model families on one local OpenAI-compatible endpoint. Forge talks
to it with the Python OpenAI SDK (streaming + tool calling). CLIProxyAPI provides model access
only; the harness is entirely Forge's.

## Goals and non-goals (V1)

**In scope:**
- Agent loop with streaming tool calling against CLIProxyAPI (Claude + GPT, per-session model choice)
- Tools: bash, file read/write/edit with diff tracking, glob/grep/list_dir, load_skill
- Autonomy modes: **yolo (default)** and **guarded** (approval gates with Allow / Deny / Always policies)
- Parallel sessions with tabs, statuses, and a FIFO task queue under a concurrency cap
- Claude Code-compatible skills with progressive disclosure
- Event-sourced persistence: sessions survive restarts; reconnects replay
- The full 2a workspace UI: top bar, chat stream, tool cards, gates, composer, detail drawer (Diff + File views)
- Basic automatic context compaction

**Out of scope (V1):**
- Browser / computer use (V2)
- OS-level sandboxing (guarded mode is the safety layer)
- Drawer Blame view (control stays in the UI; needs a `git blame` endpoint later)
- Multi-user, remote access, auth (binds to localhost)

## Architecture

Three processes at runtime:

1. **CLIProxyAPI** (external, already running) — local OpenAI-compatible endpoint serving Claude + GPT.
2. **Forge server** — Python, FastAPI + uvicorn. Owns sessions, agent loops (one asyncio task per
   running session), tool execution, approvals, skills registry, queue, event-log persistence.
3. **Forge UI** — React + Vite + TS SPA. A pure projection of the event stream; no business logic.

**Protocol — hybrid REST + WebSocket:**
- REST for commands: create/list sessions, send user message, resolve approval, cancel run,
  list skills/models, fuzzy file search. Debuggable with curl.
- One multiplexed WebSocket for events, every event tagged with `session_id`, so the top bar can
  show all sessions' statuses and the queue count. On reconnect the client sends its last-seen
  sequence number per session; the server replays the gap.

**State on disk (`~/.forge/`):**
- `config.toml` — CLIProxyAPI base URL + key, model list (id, display name, context window),
  default autonomy, concurrency cap, global policies.
- `sessions/<id>/events.jsonl` — append-only durable event log per session.
- `sessions/<id>/blobs/` — before/after file contents for changesets (drawer diffs, revert).
- `skills/` — global skills library.

### Repo layout (monorepo)

```
mygent/
  server/                  # Python, uv-managed; ruff + pytest
    forge/
      engine/              # agent loop, session actor, event types (Pydantic)
      llm/                 # OpenAI-SDK adapter for CLIProxyAPI + fake LLM for tests
      tools/               # bash, files, search, skill loader
      api/                 # FastAPI routes + WebSocket
      store/               # event log read/write, config, changesets
    tests/
  web/                     # React + Vite + TS; pnpm + vitest
    src/
      protocol/            # TS event types generated from the Pydantic models
      state/               # Zustand store + event projection reducer
      components/          # TopBar, ChatStream, ToolCard, ApprovalGate, Composer, DetailDrawer
  design_handoff_agent_workspace/   # design source of truth for the UI
  docs/superpowers/specs/
```

**One type system:** Pydantic event models are the source of truth; a codegen script exports JSON
Schema → TypeScript into `web/src/protocol/`. No hand-synced protocol.

## Event model

The append-only event log is the single source of truth. Three things are projections of it: the
UI stream, the model's message history, and session state after a restart.

**Durable events** (persisted with a per-session sequence number, replayed):
`session_created`, `session_renamed`, `status_changed`, `autonomy_changed`, `user_message`,
`assistant_message`, `tool_call_started`, `tool_call_finished` (output truncated head+tail if
huge), `approval_requested`, `approval_resolved`, `policy_added`, `context_compacted`,
`run_finished` (completed | cancelled | interrupted | error), `error`.

**Ephemeral events** (WS only, never persisted): assistant text deltas and live tool-output
chunks, referencing the in-flight durable event's id. Replay shows consolidated finals only.

**Context projection:** the OpenAI `messages` array is rebuilt from the log each turn —
`user_message` → user role; `assistant_message` + its tool calls → assistant role;
tool outputs → tool-role messages; a denied approval → a tool result stating the user denied the
action. After compaction, projection uses the summary event plus turns after it.

## Engine core

**Agent loop** (one asyncio task per active session):

```
while run is active:
    messages = project_to_openai_messages(log)
    stream LLM response (deltas → WS; accumulate)
    if tool_calls:
        for each call:
            classify → in guarded, maybe emit approval_requested and await a Future
            execute tool, streaming output chunks
            append tool_call_finished
        continue
    else:
        run_finished → status idle
```

- **Steering:** a user message arriving mid-run is injected into `messages` before the next LLM
  call (queued until the current tool finishes).
- **Cancellation:** cancels the asyncio task, kills the tool's process group, emits
  `run_finished(cancelled)`. The log stays consistent; the next message resumes.
- **Compaction:** track token usage from API responses; past ~75% of the model's context window,
  summarize older turns (same model) into a `context_compacted` event.

**Model adapter (`llm/`):** wraps `AsyncOpenAI` at the CLIProxyAPI base URL. Assembles streamed
tool calls from deltas, retries transient errors with backoff, reads per-model config, tracks
usage per session. The system prompt is a harness template: identity, environment (OS, cwd, date),
skills index, behavioral guidelines. A **FakeLLM** implementation of the same interface replays
scripted responses for tests.

## Tools and approvals

**Tool interface:** `spec` (name, description, JSON-Schema params → OpenAI function definition),
`display(args)` (tool-card header line), `classify(args)` (gated or not), `run(args, ctx)` (async,
yields output chunks; ctx = cwd, event emitter, cancellation token).

**V1 tools:**

| Tool | Notes |
|---|---|
| `bash` | Per-call subprocess in session cwd, own process group, 2-min default timeout, streamed output |
| `read_file` | Offset/limit, line numbers |
| `write_file`, `edit_file` | Edit = exact-string replace. Both record a changeset entry: path, before/after blobs, unified diff, ± stats — powers card diff stats, drawer Diff/File views, Revert / Keep all |
| `glob`, `grep`, `list_dir` | ripgrep-backed grep with Python fallback |
| `load_skill` | Pulls a skill body into context (see Skills) |

**Approvals — deliberately simple:**
- Read-only tools (`read_file`, `glob`, `grep`, `list_dir`, `load_skill`): never gated, any mode.
- Everything else (`bash`, `write_file`, `edit_file`):
  - **yolo (default):** runs immediately; the stream shows the design's "auto-approved" line.
  - **guarded:** emits an approval gate — **Allow** (run once), **Deny** (model gets a "user
    denied" tool result and re-plans), **Always ⌄** (standing policy: this exact command, a glob
    like `pytest *`, or all edits in this workspace — session-scoped or global; global policies
    persist to `config.toml`; all emit `policy_added`). Matching is tool + glob on the display line.
- While a gate is open the session status is `attention` (amber dot); the loop awaits a Future
  resolved by the REST approval endpoint.

## Sessions, parallelism, queue

- Each session = a **SessionActor**: own event log, cwd, model, autonomy, inbox of commands
  (user message, approval, cancel). Status machine: `idle → running → attention → idle`, plus
  `queued` — exactly the tab-dot states in the design.
- A **SessionManager** rehydrates actors from `~/.forge/sessions/` at startup (tabs survive
  restarts) and enforces the concurrency cap (config, default 3) on simultaneously running loops.
- **Queue:** starting a run past the cap sets the session to `queued`; a FIFO scheduler dispatches
  as slots free. The top-bar pill shows the queued count.
- The composer always talks to the active session (steer mid-run or start its next run).
  "Queue another task" = new tab (`+`) and prompt it; at the cap it queues.
- Session names auto-generate from the first message; renameable.

## Skills

- **Format:** Claude Code-compatible — a directory per skill with `SKILL.md` (YAML frontmatter:
  `name`, `description`) plus optional bundled files the body references.
- **Locations:** `~/.forge/skills/` (global) and `<cwd>/.forge/skills/` (per-project); project
  wins on name collision. The user's existing `~/.claude/skills` can be symlinked in.
- **Progressive disclosure:** at session start the server indexes name + description into the
  system prompt; the model calls `load_skill(name)` (never gated) to load a body when relevant.
- The agent can author new skills with its own file tools — no extra machinery.

## Frontend

- React + Vite + TS, Zustand. Store: `sessions` (meta + events + drawer state), `activeSessionId`,
  queue count, connection status. Hydrated by REST, fed by the WS.
- A **projection reducer** folds events into the design's `stream[]` items (userMessage /
  agentProse / toolCall / approvalRequest); ephemeral deltas update the in-flight item in place
  (live streaming text and terminal output).
- Components map 1:1 to handoff 2a: `TopBar` (brand, session tabs, queue pill, cwd), `ChatStream`
  (user bubbles, agent prose as markdown via react-markdown, `ToolCard`, `ApprovalGate`,
  auto-approved line, status line), `Composer` (`@` fuzzy file picker via REST; `/` palette:
  `/model`, `/autonomy`, `/compact`, `/new`; model pill e.g. `opus-5 · yolo`; send), `DetailDrawer`
  (Diff + File views; Blame stubbed).
- **Styling:** the handoff token sheet as CSS custom properties (ACCENT via `color-mix`, exact
  colors/radii/shadows/type scale from the README), CSS modules, Geist + Geist Mono self-hosted
  via Fontsource. Pixel-perfect to card 2a.

## Error handling

- **LLM errors:** adapter retries with backoff; exhausted → `error` event in the stream, session
  idle; the user prompts again to retry.
- **Tool failures/timeouts:** captured as failed tool results and fed back to the model, which
  self-corrects; the run does not die.
- **Disconnects:** client auto-reconnects with last-seen sequence per session; server replays.
- **Server crash mid-run:** rehydration detects a log ending mid-run and synthesizes
  `run_finished(interrupted)`; the next message resumes cleanly.
- **CLIProxyAPI down:** health check surfaced on the model pill; errors render in the stream.

## Testing

- **Engine end-to-end with FakeLLM** (scripted tool calls/text, zero model calls), via FastAPI
  test client + WS, asserting event sequences: simple run, approval allow/deny, steering mid-run,
  cancel, queue dispatch, restart rehydration, compaction trigger.
- **Unit:** context projection (log → messages), policy matching, tool executors in tmpdirs,
  truncation. pytest + pytest-asyncio.
- **Web:** vitest on the projection reducer; component-state tests for ToolCard/ApprovalGate.
- Dev loop: `make dev` runs uvicorn + Vite; otherwise the server serves the built SPA.

## Future work (post-V1)

Browser/computer use; OS sandboxing; drawer Blame view; richer `/` commands; scheduled/recurring
tasks; multi-changeset history in the drawer pager.
