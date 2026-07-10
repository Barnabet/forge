import type {
  Changeset, Event, OutputChunk, Project, SessionDeleted, SessionMeta, TextDelta,
} from './generated'

export type DurableEvent = Event
export type WireEvent = Event | TextDelta | OutputChunk | SessionDeleted
export type { Changeset, OutputChunk, Project, SessionDeleted, SessionMeta, TextDelta }

// Pydantic defaults make autonomy/status optional in the generated SessionMeta;
// NonNullable recovers the closed unions the rest of the app consumes.
export type Autonomy = NonNullable<SessionMeta['autonomy']>
export type Status = NonNullable<SessionMeta['status']>
export type Effort = NonNullable<SessionMeta['effort']>

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
