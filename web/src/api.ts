import type { Autonomy, Changeset, ModelInfo, SessionMeta } from './protocol'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
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
