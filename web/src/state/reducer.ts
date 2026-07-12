import { seqOf, type Autonomy, type DiffStats, type Effort, type Mode, type Status, type TodoStatus, type WireEvent } from '../protocol'
import {
  applyTerminalOutput, emptyTerminals, upsertTerminalState,
  type SessionTerminals,
} from './terminals'

export interface TodoItem {
  text: string
  status: TodoStatus
}

export interface SubagentWorker {
  worker: number
  task: string
  mode: 'read' | 'write'
  // 'blocked': a write worker waiting on the shared write lock (another write
  // worker is editing the tree), distinct from 'queued' (waiting for a slot).
  state: 'queued' | 'running' | 'blocked' | 'done' | 'error'
  activity: string[] // recent tool lines, newest last
  activityCount: number // all tool lines received, including ones no longer retained
  report: string
}

export interface SubagentCrew {
  callId: string
  workers: SubagentWorker[]
  // newest tool line across the whole crew, for the collapsed top-bar feed
  lastActivity: { worker: number; line: string } | null
}

export type StreamItem =
  // pending: steering sent while the run was live — the model only receives
  // it on its next completion; rendered ghosted (and pinned to the bottom of
  // the stream, see ChatStream) until then.
  | { kind: 'user'; seq: number; text: string; images: string[]; pending?: boolean;
      // Workspace checkpoint captured before this message; the rewind target.
      // null on logs predating checkpoints — such bubbles can't be rewound to.
      checkpoint?: string | null }
  | { kind: 'prose'; seq: number; text: string; streaming: boolean }
  | { kind: 'tool'; seq: number; callId: string; tool: string; display: string;
      status: 'running' | 'done' | 'error'; output: string; durationMs: number;
      diffStats: DiffStats | null; autoApproved: boolean; images: string[];
      // Announced from the live stream before arguments finished; superseded
      // by tool_call_started (or a gate) and pruned if the turn drops it.
      pending?: boolean }
  | { kind: 'gate'; seq: number; callId: string; tool: string; display: string; denied: boolean }
  | { kind: 'plan'; seq: number; callId: string; plan: string;
      state: 'pending' | 'approved' | 'revising'; feedback: string }
  | { kind: 'error'; seq: number; message: string }
  | { kind: 'info'; seq: number; text: string }
  | { kind: 'compacted'; seq: number }

type ToolItem = Extract<StreamItem, { kind: 'tool' }>
type GateItem = Extract<StreamItem, { kind: 'gate' }>
type PlanItem = Extract<StreamItem, { kind: 'plan' }>

export interface SessionStream {
  lastSeq: number
  items: StreamItem[]
  name: string
  cwd: string
  model: string
  autonomy: Autonomy
  status: Status
  steps: number
  projectId: string | null
  archived: boolean
  effort: Effort
  mode: Mode
  todos: TodoItem[]
  lastTs: number // epoch seconds of the most recent message (0 = unknown)
  usageTokens: number
  memoryState: 'running' | 'written' | 'unchanged' | 'error' | null
  // True while a context-compaction pass is in flight (manual or automatic).
  // Ephemeral: never persisted, so it clears on reload.
  compacting: boolean
  // Number of summary sections completed (0..9) and the current section's name,
  // driving a determinate progress display. Reset when compaction ends.
  compactionPhase: number
  compactionLabel: string
  subagents: SubagentCrew | null
  // Durable "session pill" state driving the sidebar completion badge.
  // lastRunReason records how the most recent run ended; unread is true while a
  // successful completion hasn't been seen by the user (cleared by opening the
  // session or a run_acknowledged). lastRunSeq is the run_finished seq of the
  // last completed run — used to match run_acknowledged against the right run so
  // a stale ack can't clear a fresher unread. All reconstruct from replay.
  lastRunReason: 'completed' | 'cancelled' | 'interrupted' | 'error' | null
  unread: boolean
  lastRunSeq: number
  // PTY terminals for this session, keyed by id with a stable open order. Fed
  // by durable terminal_state (metadata/lifecycle) and ephemeral
  // terminal_output (byte stream); see state/terminals.ts.
  terminals: SessionTerminals
  // Epoch ms marking the start of the CURRENT visible "Thinking" span, or null
  // when Thinking isn't showing. Persisted per session so the timer survives a
  // conversation switch; a real disappearance (tool/prose/status) clears it and
  // a later return starts a fresh interval.
  thinkingSince: number | null
}

export function emptyStream(): SessionStream {
  return {
    lastSeq: 0, items: [], name: 'New session', cwd: '', model: '',
    autonomy: 'yolo', status: 'idle', steps: 0,
    projectId: null, archived: false, effort: 'default',
    mode: 'act', todos: [], lastTs: 0, usageTokens: 0, memoryState: null,
    compacting: false, compactionPhase: 0, compactionLabel: '', subagents: null,
    terminals: emptyTerminals(),
    thinkingSince: null,
    lastRunReason: null, unread: false, lastRunSeq: 0,
  }
}

// Mirrors ChatStream's "Thinking" gate: the status line only fills the silence
// (silent reasoning) while running — never while text streams, a tool visibly
// runs, or right after prose lands.
function showsThinking(s: SessionStream): boolean {
  if (s.status !== 'running') return false
  const hasLive = s.items.some(it =>
    (it.kind === 'prose' && it.streaming) || (it.kind === 'tool' && it.status === 'running'))
  if (hasLive) return false
  return s.items[s.items.length - 1]?.kind !== 'prose'
}

function finalizeProse(items: StreamItem[]): void {
  const i = items.findLastIndex(it => it.kind === 'prose' && it.streaming)
  if (i >= 0) items[i] = { ...(items[i] as Extract<StreamItem, { kind: 'prose' }>), streaming: false }
}

function prunePending(items: StreamItem[], keep?: (callId: string) => boolean): StreamItem[] {
  return items.filter(it => it.kind !== 'tool' || !it.pending || (keep?.(it.callId) ?? false))
}

// Un-ghost every steering bubble a completion with this context_seq consumed,
// AND move it to where it actually entered the context: the projection defers
// steering past the in-flight iteration's tool results, so the bubble hops over
// output the model produced before seeing it (durable items with
// seq <= context_seq) and stops before any later user message or the reply's
// own items (seq 0 or > context_seq). Runs on both the ephemeral
// steering_consumed (fired when the request goes out) and the durable
// assistant_message (replay), so live and rehydrate land identically.
function unghostSteering(items: StreamItem[], ctxSeq: number): void {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind !== 'user' || !it.pending || it.seq > ctxSeq) continue
    let j = i + 1
    while (j < items.length) {
      const nx = items[j]
      if (nx.kind === 'user' || nx.seq === 0 || nx.seq > ctxSeq) break
      j++
    }
    items.splice(i, 1)
    items.splice(j - 1, 0, { ...it, pending: false })
  }
}

// A crew is keyed by its parent spawn call: a different call_id means a fresh
// spawn, so the previous crew is discarded rather than merged into.
function crewFor(s: SessionStream, callId: string): SubagentCrew {
  return s.subagents && s.subagents.callId === callId
    ? { ...s.subagents, workers: [...s.subagents.workers] }
    : { callId, workers: [], lastActivity: null }
}

function upsertWorker(crew: SubagentCrew, next: SubagentWorker): void {
  const i = crew.workers.findIndex(w => w.worker === next.worker)
  if (i >= 0) crew.workers[i] = next
  else {
    crew.workers.push(next)
    crew.workers.sort((a, b) => a.worker - b.worker)
  }
}

export function reduce(s: SessionStream, e: WireEvent): SessionStream {
  const seq = seqOf(e)
  if (seq !== 0 && seq <= s.lastSeq) return s // replay/live overlap: drop duplicates
  const n: SessionStream = { ...s, items: [...s.items] }
  if (seq !== 0) n.lastSeq = seq

  switch (e.type) {
    case 'session_created':
      n.name = e.name; n.cwd = e.cwd; n.model = e.model; n.autonomy = e.autonomy
      n.projectId = e.project_id ?? null
      n.effort = e.effort ?? 'default'
      n.lastTs = e.ts
      break
    case 'session_archived':
      n.archived = true
      break
    case 'session_unarchived':
      n.archived = false
      break
    case 'effort_changed':
      n.effort = e.effort
      break
    case 'mode_changed':
      n.mode = e.mode
      break
    case 'todos_updated':
      n.todos = e.todos.map(t => ({ text: t.text, status: t.status ?? 'pending' }))
      break

    case 'plan_proposed': {
      // The plan card takes over the pending tool line's story (like a gate).
      const i = n.items.findLastIndex(
        it => it.kind === 'tool' && it.callId === e.call_id && it.pending)
      if (i >= 0) n.items.splice(i, 1)
      n.items.push({ kind: 'plan', seq, callId: e.call_id, plan: e.plan,
                     state: 'pending', feedback: '' })
      break
    }

    case 'plan_resolved': {
      const i = n.items.findLastIndex(it => it.kind === 'plan' && it.callId === e.call_id)
      if (i >= 0) {
        n.items[i] = {
          ...(n.items[i] as PlanItem),
          state: e.decision === 'approve' ? 'approved' : 'revising',
          feedback: e.feedback ?? '',
        }
      }
      break
    }
    case 'session_deleted':
      break // the store intercepts this in applyEvent; never reduced into a stream
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

    case 'user_message': {
      n.lastTs = e.ts
      finalizeProse(n.items)
      // Steering: a message sent while the run is live is only appended to the
      // model's context on its next completion. Ghost it until then, and keep
      // the in-flight run's state (crew, step count) intact.
      const steering = e.steering ?? false
      n.items.push({ kind: 'user', seq, text: e.text, images: e.images ?? [],
                     checkpoint: e.workspace_checkpoint ?? null,
                     ...(steering ? { pending: true } : {}) })
      if (!steering) {
        n.steps = 0
        if (n.memoryState !== 'running') n.memoryState = null // stale badge from the last run
        n.subagents = null // last run's crew is history once a new prompt arrives
      }
      break
    }

    case 'message_checkpointed': {
      // The workspace snapshot completed after the bubble was published; attach
      // it so Edit/Rewind become actionable.
      const idx = n.items.findIndex(it => it.kind === 'user' && it.seq === e.user_seq)
      if (idx !== -1) {
        const it = n.items[idx] as Extract<StreamItem, { kind: 'user' }>
        n.items[idx] = { ...it, checkpoint: e.checkpoint }
      }
      break
    }

    case 'text_delta': {
      const last = n.items[n.items.length - 1]
      if (last?.kind === 'prose' && last.streaming)
        n.items[n.items.length - 1] = { ...last, text: last.text + e.text }
      else n.items.push({ kind: 'prose', seq: 0, text: e.text, streaming: true })
      break
    }

    case 'assistant_message': {
      n.lastTs = e.ts
      // Keep the latest nonzero usage (old V1 logs carry no usage_tokens).
      if ((e.usage_tokens ?? 0) > 0) n.usageTokens = e.usage_tokens ?? 0
      // The durable tool_calls list is authoritative: drop pending lines a
      // retried stream announced but the final completion didn't keep.
      const kept = new Set((e.tool_calls ?? []).map(c => c.id))
      n.items = prunePending(n.items, id => kept.has(id))
      // Un-ghost/relocate any steering this completion consumed. Usually the
      // ephemeral steering_consumed already did it when the request went out;
      // this covers replay/rehydrate, where that ephemeral event was dropped.
      unghostSteering(n.items, e.context_seq ?? 0)
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

    case 'tool_call_pending': {
      if (n.items.some(it => it.kind === 'tool' && it.callId === e.call_id)) break
      n.items.push({
        kind: 'tool', seq: 0, callId: e.call_id, tool: e.tool, display: '',
        status: 'running', output: '', durationMs: 0, diffStats: null,
        autoApproved: false, images: [], pending: true,
      })
      n.steps += 1
      break
    }

    case 'tool_call_started': {
      const item: StreamItem = {
        kind: 'tool', seq, callId: e.call_id, tool: e.tool, display: e.display,
        status: 'running', output: '', durationMs: 0, diffStats: null,
        autoApproved: e.auto_approved ?? false, images: [],
      }
      const i = n.items.findLastIndex(
        it => it.kind === 'tool' && it.callId === e.call_id && it.pending)
      if (i >= 0) {
        n.items[i] = item // upgrade the pending line in place; step already counted
      } else {
        n.items.push(item)
        n.steps += 1
      }
      break
    }

    case 'output_chunk': {
      const i = n.items.findLastIndex(it => it.kind === 'tool' && it.callId === e.call_id)
      const it = n.items[i]
      if (it?.kind === 'tool' && it.status === 'running')
        n.items[i] = { ...it, output: it.output + e.text }
      break
    }

    case 'tool_call_finished': {
      const status = (e.is_error ?? false) ? 'error' as const : 'done' as const
      // propose_plan results: the plan card already tells the story. A dangling
      // close (cancel/restart while gated) removes a still-pending card and
      // falls through to the error tool line.
      const p = n.items.findLastIndex(it => it.kind === 'plan' && it.callId === e.call_id)
      if (p >= 0) {
        if (status !== 'error') break
        if ((n.items[p] as PlanItem).state === 'pending') n.items.splice(p, 1)
        else break
      }
      const i = n.items.findLastIndex(it => it.kind === 'tool' && it.callId === e.call_id)
      if (i >= 0) {
        n.items[i] = {
          ...(n.items[i] as ToolItem), status, output: e.output,
          durationMs: e.duration_ms ?? 0, diffStats: (e.diff_stats as DiffStats | null) ?? null,
          images: (e.images as string[] | undefined) ?? [],
        }
      } else if (!n.items.some(it => it.kind === 'gate' && it.callId === e.call_id && it.denied)) {
        // finished-without-started (contract #2); denied gates already tell the story
        n.items.push({
          kind: 'tool', seq, callId: e.call_id, tool: e.tool, display: e.tool,
          status, output: e.output, durationMs: e.duration_ms ?? 0,
          diffStats: (e.diff_stats as DiffStats | null) ?? null, autoApproved: false,
          images: (e.images as string[] | undefined) ?? [],
        })
      }
      break
    }

    case 'approval_requested': {
      // The gate takes over the pending line's story (deny would otherwise
      // leave both a denied gate and an orphaned tool line).
      const i = n.items.findLastIndex(
        it => it.kind === 'tool' && it.callId === e.call_id && it.pending)
      if (i >= 0) n.items.splice(i, 1)
      n.items.push({ kind: 'gate', seq, callId: e.call_id, tool: e.tool, display: e.display, denied: false })
      break
    }

    case 'approval_resolved': {
      const i = n.items.findLastIndex(it => it.kind === 'gate' && it.callId === e.call_id)
      if (i >= 0) {
        if (e.decision === 'allow') n.items.splice(i, 1) // gate collapses into the tool card that follows
        else n.items[i] = { ...(n.items[i] as GateItem), denied: true }
      }
      break
    }

    case 'history_rewound': {
      // Truncate the active branch to just before target_user_seq. The raw log
      // stays append-only (lastSeq advances to the marker), but every durable
      // item at or after the target — and every ephemeral/live seq-0 item —
      // leaves the active view. Run-local state that belonged to the discarded
      // branch resets; session settings and the cursor are preserved.
      n.items = n.items.filter(it => it.seq !== 0 && it.seq < e.target_user_seq)
      n.steps = 0
      n.subagents = null
      n.todos = []
      n.memoryState = null
      n.compacting = false
      n.usageTokens = 0
      n.thinkingSince = null
      // The completed run that set the pill state lived on the branch we just
      // discarded; drop the stale badge. A fresh run after the rewind re-sets it,
      // and the server's meta stays authoritative on the next hydrate.
      n.lastRunReason = null
      n.unread = false
      n.lastRunSeq = 0
      break
    }

    case 'context_compacted':
      n.items.push({ kind: 'compacted', seq })
      break

    case 'run_finished':
      finalizeProse(n.items)
      n.items = prunePending(n.items) // a dead run leaves no ghost tool lines
      n.status = 'idle' // contract #3: rehydrate emits no status_changed
      // Cancel/interrupt can strand workers mid-flight; close their lanes.
      if (n.subagents?.workers.some(w => w.state === 'queued' || w.state === 'running')) {
        n.subagents = {
          ...n.subagents,
          workers: n.subagents.workers.map(w =>
            w.state === 'queued' || w.state === 'running' ? { ...w, state: 'error' } : w),
        }
      }
      if (e.reason === 'cancelled') n.items.push({ kind: 'info', seq, text: 'Run cancelled' })
      if (e.reason === 'interrupted') n.items.push({ kind: 'info', seq, text: 'Interrupted by server restart' })
      // Session-pill state. A successful completion is unread only while the
      // engine still says so (e.unread); it clears once acknowledged. Any
      // non-success outcome clears unread and just records the reason. lastRunSeq
      // tracks the completed run so a later run_acknowledged matches the right
      // one across replay/races.
      n.lastRunReason = e.reason
      if (e.reason === 'completed') {
        n.lastRunSeq = seq
        n.unread = e.unread ?? false
      } else {
        n.unread = false
      }
      break

    case 'run_acknowledged':
      // Idempotent read watermark: clear unread only when this ack targets the
      // completion we're currently showing as unread. A stale ack from an
      // abandoned branch (run_seq !== our latest completed run) is ignored, so a
      // fresher unread survives.
      if (e.run_seq === n.lastRunSeq) n.unread = false
      break

    case 'error':
      n.items = prunePending(n.items)
      n.items.push({ kind: 'error', seq, message: e.message })
      break

    case 'memory_update':
      n.memoryState = e.state
      break

    case 'compaction':
      n.compacting = e.state === 'running'
      n.compactionPhase = e.state === 'running' ? (e.phase ?? 0) : 0
      n.compactionLabel = e.state === 'running' ? (e.label ?? '') : ''
      break

    case 'subagent_state': {
      // Durable lifecycle snapshot: survives refresh/reconnect and, replayed
      // from empty, reconstructs the crew panel on its own. It never carries
      // activity, so preserve whatever the ephemeral feed already collected.
      const crew = crewFor(n, e.call_id)
      const prev = crew.workers.find(w => w.worker === e.worker) ?? null
      upsertWorker(crew, {
        worker: e.worker, task: e.task, mode: e.mode ?? 'read', state: e.state,
        activity: prev?.activity ?? [],
        activityCount: prev?.activityCount ?? prev?.activity.length ?? 0,
        report: e.report || (prev?.report ?? ''),
      })
      n.subagents = crew
      break
    }

    case 'subagent_update': {
      // Ephemeral live progress: appends activity and, for backward/live
      // compatibility, still accepts full lifecycle payloads. A running-only
      // activity tick must not stomp durable state/report already recorded.
      const crew = crewFor(n, e.call_id)
      const prev = crew.workers.find(w => w.worker === e.worker) ?? null
      const activity = e.activity
        ? [...(prev?.activity ?? []), e.activity].slice(-8)
        : prev?.activity ?? []
      const activityCount = (prev?.activityCount ?? prev?.activity.length ?? 0)
        + (e.activity ? 1 : 0)
      if (e.activity) crew.lastActivity = { worker: e.worker, line: e.activity }
      // A durable terminal state (done/error) must not regress to running/queued
      // if a straggling activity tick arrives after it.
      const terminal = prev?.state === 'done' || prev?.state === 'error'
      const state = terminal
        && (e.state === 'running' || e.state === 'queued' || e.state === 'blocked')
        ? prev.state : e.state
      upsertWorker(crew, {
        worker: e.worker, task: e.task, mode: e.mode ?? 'read', state,
        activity, activityCount, report: e.report || (prev?.report ?? ''),
      })
      n.subagents = crew
      break
    }

    case 'policy_added':
      break // no stream item; the allowed tool card carries the story

    case 'memory_recalled':
      break // snippets ride below the user message model-side; no UI item

    case 'steering_consumed':
      // The next completion's request just went out with this steering in its
      // context; un-ghost now instead of waiting for the reply a turn later.
      unghostSteering(n.items, e.context_seq)
      break

    case 'terminal_state':
      // Durable metadata/lifecycle. Offsets are never touched here, so a
      // replayed older snapshot can't regress output the ephemeral stream
      // advanced. (Same-or-lower seq snapshots are already dropped above.)
      n.terminals = upsertTerminalState(n.terminals, e)
      break

    case 'terminal_output':
      // Ephemeral byte stream (seq 0): offset-aware append/dedupe/gap-detect.
      n.terminals = applyTerminalOutput(n.terminals, e)
      break
  }

  // Maintain the current Thinking interval (epoch ms) off the resulting stream.
  // Events carry ts in epoch SECONDS, so scale to ms for a consistent unit.
  switch (e.type) {
    case 'status_changed':
      // Starts the interval when the run goes live into silent reasoning;
      // any non-running status clears it.
      n.thinkingSince = showsThinking(n) ? e.ts * 1000 : null
      break
    case 'tool_call_finished':
      // A tool wrapping up mid-run returns to silence: start a fresh anchor.
      // Otherwise (another tool still running, or the run ended) clear it.
      n.thinkingSince = showsThinking(n) ? e.ts * 1000 : null
      break
    case 'text_delta':
    case 'assistant_message':
    case 'tool_call_pending':
    case 'tool_call_started':
    case 'approval_requested':
    case 'plan_proposed':
    case 'run_finished':
    case 'error':
      // Visible activity (or a finished run) hides Thinking: end the interval.
      n.thinkingSince = null
      break
  }

  return n
}
