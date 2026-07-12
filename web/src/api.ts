import type {
  Autonomy, Changeset, ConfigPatch, Effort, EvaluationDetail, EvaluationSummary,
  ForgeConfig, LeaderboardEntry, Mode, ModelInfo, OrchestratorFacet, Project, SessionMeta, WireEvent,
} from './protocol'
import type { TerminalBuffer, TerminalMeta } from './state/terminals'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export type FsEntry = { name: string; type: 'file' | 'dir'; size: number; mtime: number }

export type IndexStatus = { state: 'indexing' | 'ready' | 'error'; done: number; total: number }

export type WorkspaceSessionInfo = {
  id: string
  name: string
  status: string
  mode: string
  archived: boolean
  last_message_at: number | null
  busy: boolean | null
}

export type WorkspaceActivity = {
  seq: number
  timestamp: number
  session_id: string | null
  author: string
  origin: string
  action: string
  paths: string[]
  note: string | null
}

export type WorkspaceStatus = {
  cwd: string
  sessions: WorkspaceSessionInfo[]
  recent_activity: WorkspaceActivity[]
  current_tree: string | null
  reconciled: boolean
  last_external_paths: string[]
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = init ? await fetch(path, init) : await fetch(path)
  if (!r.ok) throw new ApiError(r.status, `${init?.method ?? 'GET'} ${path} → ${r.status}`)
  return r.json() as Promise<T>
}

const post = <T,>(path: string, body?: object) =>
  req<T>(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })

const del = <T,>(path: string) => req<T>(path, { method: 'DELETE' })

export const api = {
  health: () => req<{ ok: boolean }>('/api/health'),
  models: () => req<ModelInfo[]>('/api/models'),
  getConfig: () => req<ForgeConfig>('/api/config'),
  updateConfig: (body: ConfigPatch) =>
    req<ForgeConfig>('/api/config', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }),
  sessions: () => req<SessionMeta[]>('/api/sessions'),
  events: (sid: string, after: number) =>
    req<WireEvent[]>(`/api/sessions/${sid}/events?after=${after}`),
  createSession: (
    body: {
      cwd?: string; model?: string; autonomy?: string; project_id?: string; effort?: string
    } = {},
  ) => post<SessionMeta>('/api/sessions', body),
  sendMessage: (sid: string, text: string, images: string[] = []) =>
    post<object>(`/api/sessions/${sid}/messages`, { text, images }).then(() => undefined),
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
  workspaceStatus: (sid: string, limit = 20) =>
    req<WorkspaceStatus>(`/api/sessions/${sid}/workspace/status?limit=${limit}`),
  projects: () => req<Project[]>('/api/projects'),
  indexStatus: () => req<Record<string, IndexStatus>>('/api/index'),
  createProject: (body: {
    name: string; cwd: string; default_model?: string;
    default_autonomy?: string; default_effort?: string
  }) => post<Project>('/api/projects', body),
  deleteProject: (pid: string) => del<object>(`/api/projects/${pid}`).then(() => undefined),
  recentDirs: () => req<string[]>('/api/recent_dirs'),
  markRead: (sid: string) =>
    post<object>(`/api/sessions/${sid}/read`).then(() => undefined),
  archiveSession: (sid: string) =>
    post<object>(`/api/sessions/${sid}/archive`).then(() => undefined),
  unarchiveSession: (sid: string) =>
    post<object>(`/api/sessions/${sid}/unarchive`).then(() => undefined),
  deleteSession: (sid: string) =>
    del<object>(`/api/sessions/${sid}`).then(() => undefined),
  setEffort: (sid: string, effort: Effort) =>
    post<object>(`/api/sessions/${sid}/effort`, { effort }).then(() => undefined),
  setMode: (sid: string, mode: Mode) =>
    post<object>(`/api/sessions/${sid}/mode`, { mode }).then(() => undefined),
  resolvePlan: (sid: string, callId: string, decision: 'approve' | 'revise', feedback = '') =>
    post<object>(`/api/sessions/${sid}/plan/${callId}`, { decision, feedback }).then(() => undefined),
  // Atomic rewind/edit-and-resend. Omit text/images for rewind-only; supply
  // them (edit) to truncate and post a replacement in one request.
  rewind: (
    sid: string, targetUserSeq: number, edit?: { text: string; images?: string[] },
  ) =>
    post<object>(`/api/sessions/${sid}/rewind`, edit
      ? { target_user_seq: targetUserSeq, text: edit.text, images: edit.images ?? [] }
      : { target_user_seq: targetUserSeq },
    ).then(() => undefined),

  listTerminals: (sid: string) =>
    req<TerminalMeta[]>(`/api/sessions/${sid}/terminals`),
  readTerminal: (sid: string, tid: string, after: number) =>
    req<TerminalBuffer & { terminal_id: string }>(
      `/api/sessions/${sid}/terminals/${tid}?after=${after}`),
  writeTerminal: (sid: string, tid: string, data: string) =>
    post<object>(`/api/sessions/${sid}/terminals/${tid}/input`, { data }).then(() => undefined),
  resizeTerminal: (sid: string, tid: string, cols: number, rows: number) =>
    post<object>(`/api/sessions/${sid}/terminals/${tid}/resize`, { cols, rows }).then(() => undefined),
  signalTerminal: (sid: string, tid: string, signal: string) =>
    post<object>(`/api/sessions/${sid}/terminals/${tid}/signal`, { signal }).then(() => undefined),
  closeTerminal: (sid: string, tid: string) =>
    post<object>(`/api/sessions/${sid}/terminals/${tid}/close`).then(() => undefined),

  fsBrowse: (path?: string) =>
    req<{ path: string; parent: string | null; entries: FsEntry[] }>(
      `/api/fs/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  fsPick: (path?: string) =>
    req<{ path: string | null }>(
      `/api/fs/pick${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  fsList: (sid: string, path: string) =>
    req<{ entries: FsEntry[] }>(`/api/sessions/${sid}/fs/list?path=${encodeURIComponent(path)}`),
  fsFileUrl: (sid: string, path: string) =>
    `/api/sessions/${sid}/fs/file?path=${encodeURIComponent(path)}`,
  fsReadText: async (sid: string, path: string): Promise<string> => {
    const url = `/api/sessions/${sid}/fs/file?path=${encodeURIComponent(path)}`
    const r = await fetch(url)
    if (!r.ok) throw new ApiError(r.status, `GET ${url} → ${r.status}`)
    return r.text()
  },
  fsMkdir: (sid: string, path: string) =>
    post<object>(`/api/sessions/${sid}/fs/mkdir`, { path }).then(() => undefined),
  fsTouch: (sid: string, path: string) =>
    post<object>(`/api/sessions/${sid}/fs/touch`, { path }).then(() => undefined),
  fsMove: (sid: string, src: string, dst: string) =>
    post<object>(`/api/sessions/${sid}/fs/move`, { src, dst }).then(() => undefined),
  fsDelete: (sid: string, path: string) =>
    post<object>(`/api/sessions/${sid}/fs/delete`, { path }).then(() => undefined),
  subagentLeaderboard: (orchestratorModel?: string) =>
    req<LeaderboardEntry[]>(`/api/subagents/leaderboard${orchestratorModel ? `?orchestrator_model=${encodeURIComponent(orchestratorModel)}` : ''}`),
  subagentOrchestrators: () =>
    req<OrchestratorFacet[]>('/api/subagents/orchestrators'),
  subagentEvaluations: (limit = 50, offset = 0, orchestratorModel?: string) =>
    req<EvaluationSummary[]>(`/api/subagents/evaluations?limit=${limit}&offset=${offset}${orchestratorModel ? `&orchestrator_model=${encodeURIComponent(orchestratorModel)}` : ''}`),
  subagentEvaluation: (id: string) =>
    req<EvaluationDetail>(`/api/subagents/evaluations/${encodeURIComponent(id)}`),

  fsUpload: (sid: string, dir: string, files: File[]) => {
    const fd = new FormData()
    fd.append('dir', dir)
    for (const f of files) fd.append('files', f)
    return req<object>(`/api/sessions/${sid}/fs/upload`, { method: 'POST', body: fd }).then(() => undefined)
  },
}
