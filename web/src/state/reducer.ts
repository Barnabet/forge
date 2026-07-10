import { seqOf, type Autonomy, type DiffStats, type Effort, type Status, type WireEvent } from '../protocol'

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
  projectId: string | null
  archived: boolean
  effort: Effort
}

export function emptyStream(): SessionStream {
  return {
    lastSeq: 0, items: [], name: 'New session', cwd: '', model: '',
    autonomy: 'yolo', status: 'idle', steps: 0,
    projectId: null, archived: false, effort: 'default',
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
