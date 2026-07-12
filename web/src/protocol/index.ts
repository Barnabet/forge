import type {
  Changeset, CompactionState, Event, FileIndexProgress, MemoryUpdate, OutputChunk,
  Project, SessionDeleted, SessionMeta, SteeringConsumed, SubagentUpdate,
  TerminalOutput, TextDelta, Todo, ToolCallPending,
} from './generated'

export type DurableEvent = Event
export type WireEvent =
  | Event | TextDelta | OutputChunk | SessionDeleted | ToolCallPending | MemoryUpdate
  | SubagentUpdate | SteeringConsumed | CompactionState | TerminalOutput
  | FileIndexProgress
export type {
  Changeset, CompactionState, FileIndexProgress, MemoryUpdate, OutputChunk, Project,
  SessionDeleted, SessionMeta, SteeringConsumed, SubagentUpdate, TerminalOutput,
  TextDelta, Todo, ToolCallPending,
}

// Pydantic defaults make autonomy/status optional in the generated SessionMeta;
// NonNullable recovers the closed unions the rest of the app consumes.
export type Autonomy = NonNullable<SessionMeta['autonomy']>
export type Status = NonNullable<SessionMeta['status']>
export type Effort = NonNullable<SessionMeta['effort']>
export type Mode = NonNullable<SessionMeta['mode']>
export type TodoStatus = NonNullable<Todo['status']>

export type { DiffStats } from './generated'

export interface ModelInfo {
  id: string
  display_name: string
  context_window: number
}

/** Server configuration surfaced by GET /api/config. Hand-written (not generated). */
export interface ForgeConfig {
  base_url: string
  api_key: string
  default_model: string
  default_autonomy: 'yolo' | 'guarded'
  max_concurrent: number
  max_resident_sessions: number
  serper_api_key: string
  firecrawl_api_key: string
  openrouter_api_key: string
  embedding_model: string
  image_model: string
  memory_similarity_threshold: number
  max_subagents: number
  subagent_max_turns: number
  subagent_model: string
  memory_model: string
  compaction_model: string
  models: { id: string; display_name: string; context_window: number }[]
}

export type ConfigPatch = Partial<Omit<ForgeConfig, 'models'>>

/** Subagent dynamic benchmark. Hand-written to match the server contracts. */
export interface LeaderboardEntry {
  model: string
  /** Averages are 0 (not null) for models with no successful samples yet. */
  avg_overall: number
  avg_work_quality: number
  avg_information_delivery: number
  avg_efficiency: number
  sample_count: number
  error_count: number
  last_timestamp: number
}

export interface OrchestratorFacet {
  model: string | null
  record_count: number
  sample_count: number
  error_count: number
  last_timestamp: number
}

export interface EvaluationSummary {
  id: string
  timestamp: number
  status: 'success' | 'error'
  subagent_model: string
  grader_model: string
  orchestrator_model: string | null
  orchestrator_model_inferred: boolean
  session_id: string
  project_id: string | null
  call_id: string
  worker_index: number
  mode: 'read' | 'write'
  task: string
  overall: number | null
}

export interface Grade {
  work_quality: number
  information_delivery: number
  efficiency: number
  overall: number
  rationale: string
  strengths: string[]
  issues: string[]
}

export interface EvaluationDetail extends EvaluationSummary {
  turn_count: number
  tool_call_count: number
  usage_tokens: number
  duration_ms: number
  parent_context: string
  worker_messages: Record<string, unknown>[]
  final_report: string
  raw_grader_response: string
  grade: Grade | null
  error: string | null
}

/** Pydantic defaults make seq optional in the generated types; wire always has it. */
export function seqOf(e: WireEvent): number {
  return e.seq ?? 0
}
