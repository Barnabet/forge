import { seqOf, type Autonomy, type DiffStats, type Effort, type Mode, type Status, type TodoStatus, type WireEvent } from '../protocol'

export interface TodoItem {
  text: string
  status: TodoStatus
}

export type StreamItem =
  | { kind: 'user'; seq: number; text: string; images: string[] }
  | { kind: 'prose'; seq: number; text: string; streaming: boolean }
  | { kind: 'tool'; seq: number; callId: string; tool: string; display: string;
      status: 'running' | 'done' | 'error'; output: string; durationMs: number;
      diffStats: DiffStats | null; autoApproved: boolean;
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
  usageTokens: number
}

export function emptyStream(): SessionStream {
  return {
    lastSeq: 0, items: [], name: 'New session', cwd: '', model: '',
    autonomy: 'yolo', status: 'idle', steps: 0,
    projectId: null, archived: false, effort: 'default',
    mode: 'act', todos: [], usageTokens: 0,
  }
}

function finalizeProse(items: StreamItem[]): void {
  const i = items.findLastIndex(it => it.kind === 'prose' && it.streaming)
  if (i >= 0) items[i] = { ...(items[i] as Extract<StreamItem, { kind: 'prose' }>), streaming: false }
}

function prunePending(items: StreamItem[], keep?: (callId: string) => boolean): StreamItem[] {
  return items.filter(it => it.kind !== 'tool' || !it.pending || (keep?.(it.callId) ?? false))
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

    case 'user_message':
      finalizeProse(n.items)
      n.items.push({ kind: 'user', seq, text: e.text, images: e.images ?? [] })
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
      // Keep the latest nonzero usage (old V1 logs carry no usage_tokens).
      if ((e.usage_tokens ?? 0) > 0) n.usageTokens = e.usage_tokens ?? 0
      // The durable tool_calls list is authoritative: drop pending lines a
      // retried stream announced but the final completion didn't keep.
      const kept = new Set((e.tool_calls ?? []).map(c => c.id))
      n.items = prunePending(n.items, id => kept.has(id))
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
        autoApproved: false, pending: true,
      })
      n.steps += 1
      break
    }

    case 'tool_call_started': {
      const item: StreamItem = {
        kind: 'tool', seq, callId: e.call_id, tool: e.tool, display: e.display,
        status: 'running', output: '', durationMs: 0, diffStats: null,
        autoApproved: e.auto_approved ?? false,
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

    case 'context_compacted':
      n.items.push({ kind: 'compacted', seq })
      break

    case 'run_finished':
      finalizeProse(n.items)
      n.items = prunePending(n.items) // a dead run leaves no ghost tool lines
      n.status = 'idle' // contract #3: rehydrate emits no status_changed
      if (e.reason === 'cancelled') n.items.push({ kind: 'info', seq, text: 'Run cancelled' })
      if (e.reason === 'interrupted') n.items.push({ kind: 'info', seq, text: 'Interrupted by server restart' })
      break

    case 'error':
      n.items = prunePending(n.items)
      n.items.push({ kind: 'error', seq, message: e.message })
      break

    case 'policy_added':
      break // no stream item; the allowed tool card carries the story
  }
  return n
}
