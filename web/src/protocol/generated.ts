/* AUTO-GENERATED from the engine Pydantic models — do not edit.
 * Regenerate with: pnpm gen:protocol */

export type Event =
  | SessionCreated
  | SessionRenamed
  | StatusChanged
  | AutonomyChanged
  | ModelChanged
  | UserMessage
  | MessageCheckpointed
  | AssistantMessage
  | ToolCallStarted
  | ToolCallFinished
  | ApprovalRequested
  | ApprovalResolved
  | PolicyAdded
  | ContextCompacted
  | RunFinished
  | RunAcknowledged
  | ErrorEvent
  | SessionArchived
  | SessionUnarchived
  | EffortChanged
  | ModeChanged
  | PlanProposed
  | PlanResolved
  | TodosUpdated
  | MemoryRecalled
  | HistoryRewound
  | SubagentState
  | TerminalState;

export interface SessionCreated {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "session_created";
  name: string;
  cwd: string;
  model: string;
  autonomy: "yolo" | "guarded";
  project_id?: string | null;
  effort?: "default" | "low" | "medium" | "high";
}
export interface SessionRenamed {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "session_renamed";
  name: string;
}
export interface StatusChanged {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "status_changed";
  status: "idle" | "running" | "attention" | "queued";
}
export interface AutonomyChanged {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "autonomy_changed";
  autonomy: "yolo" | "guarded";
}
export interface ModelChanged {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "model_changed";
  model: string;
}
export interface UserMessage {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "user_message";
  text: string;
  images?: string[];
  steering?: boolean;
  workspace_checkpoint?: string | null;
}
/**
 * Attaches a workspace checkpoint to an already-emitted user message. The
 * capture runs off the event loop after the bubble is published, so the
 * message renders instantly; this durable follow-up records the rewind target
 * once the snapshot completes.
 */
export interface MessageCheckpointed {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "message_checkpointed";
  user_seq: number;
  checkpoint: string;
}
export interface AssistantMessage {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "assistant_message";
  text: string;
  tool_calls?: ToolCallSpec[];
  usage_tokens?: number;
  context_seq?: number;
}
export interface ToolCallSpec {
  id: string;
  name: string;
  arguments: string;
}
export interface ToolCallStarted {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "tool_call_started";
  call_id: string;
  tool: string;
  display: string;
  auto_approved?: boolean;
}
export interface ToolCallFinished {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "tool_call_finished";
  call_id: string;
  tool: string;
  output: string;
  is_error?: boolean;
  duration_ms?: number;
  diff_stats?: DiffStats | null;
  images?: string[];
}
export interface DiffStats {
  path: string;
  added: number;
  removed: number;
  changeset_index: number;
  diff?: string;
}
export interface ApprovalRequested {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "approval_requested";
  call_id: string;
  tool: string;
  display: string;
}
export interface ApprovalResolved {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "approval_resolved";
  call_id: string;
  decision: "allow" | "deny";
}
export interface PolicyAdded {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "policy_added";
  tool: string;
  pattern: string;
  scope: "session" | "global";
}
export interface ContextCompacted {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "context_compacted";
  summary: string;
  upto_seq: number;
}
export interface RunFinished {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "run_finished";
  reason: "completed" | "cancelled" | "interrupted" | "error";
  unread?: boolean;
}
/**
 * Durable, idempotent read acknowledgment: records that the latest
 * successful ``completed`` run (identified by its ``run_finished`` seq) has
 * been seen by the user, clearing the session's unread marker. Emitted only
 * when a completed run is currently unread, so replaying the active branch
 * yields a monotonic read watermark that rewinds ignore on abandoned
 * branches.
 */
export interface RunAcknowledged {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "run_acknowledged";
  run_seq: number;
}
export interface ErrorEvent {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "error";
  message: string;
}
export interface SessionArchived {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "session_archived";
}
export interface SessionUnarchived {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "session_unarchived";
}
export interface EffortChanged {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "effort_changed";
  effort: "default" | "low" | "medium" | "high";
}
export interface ModeChanged {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "mode_changed";
  mode: "act" | "plan";
}
export interface PlanProposed {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "plan_proposed";
  call_id: string;
  plan: string;
}
export interface PlanResolved {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "plan_resolved";
  call_id: string;
  decision: "approve" | "revise";
  feedback?: string;
}
export interface TodosUpdated {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "todos_updated";
  todos: Todo[];
}
export interface Todo {
  text: string;
  status?: "pending" | "in_progress" | "completed";
}
/**
 * Memory snippets retrieved for one user message; rendered below it in
 * the projection. Durable so retrieval results never shift between turns.
 */
export interface MemoryRecalled {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "memory_recalled";
  user_seq: number;
  snippets: RecalledSnippet[];
}
export interface RecalledSnippet {
  tier: "global" | "project";
  region: string;
  start_line: number;
  end_line: number;
  text: string;
  score: number;
}
/**
 * Append-only marker that rewinds the active conversation branch to just
 * before ``target_user_seq``. Conversational/run events on the then-active
 * branch with seq >= target_user_seq become inactive; session settings and
 * lifecycle events remain. Never truncates the log.
 */
export interface HistoryRewound {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "history_rewound";
  target_user_seq: number;
  target_checkpoint: string;
  safety_checkpoint: string;
  replacement: boolean;
}
/**
 * Durable snapshot of one spawn_agents worker's lifecycle state, so the
 * crew viewer can reconstruct after refresh/reconnect without persisting
 * high-frequency activity lines. Only lifecycle transitions land here; the
 * live tool-line stream stays ephemeral (SubagentUpdate). Replaying the log
 * yields the latest state per worker plus the final report on done/error.
 */
export interface SubagentState {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "subagent_state";
  call_id: string;
  worker: number;
  task: string;
  mode?: "read" | "write";
  state: "queued" | "running" | "blocked" | "done" | "error";
  report?: string;
}
/**
 * Durable snapshot of one PTY terminal's lifecycle, so the terminal viewer
 * can reconstruct its records after refresh/reconnect without persisting the
 * raw output stream. Emitted on open, resize, and every final transition
 * (exit/close), plus an ``orphaned`` marker written at rehydrate when a replay
 * shows a terminal still ``running`` but the runtime process is gone. Replaying
 * the log yields the latest state per terminal; the live byte stream stays
 * ephemeral (TerminalOutput).
 */
export interface TerminalState {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "terminal_state";
  terminal_id: string;
  command: string[];
  cwd: string;
  cols: number;
  rows: number;
  state: "starting" | "running" | "exited" | "closed" | "orphaned";
  output_offset?: number;
  exit_code?: number | null;
  exit_reason?: string | null;
}

export interface TextDelta {
  seq?: number;
  session_id: string;
  type?: "text_delta";
  text: string;
}

export interface OutputChunk {
  seq?: number;
  session_id: string;
  type?: "output_chunk";
  call_id: string;
  text: string;
}

export interface SessionDeleted {
  seq?: number;
  session_id: string;
  type?: "session_deleted";
}

/**
 * Announced the moment the model starts streaming a tool call, before
 * its arguments have finished arriving. The durable ToolCallStarted (or an
 * approval gate) supersedes it.
 */
export interface ToolCallPending {
  seq?: number;
  session_id: string;
  type?: "tool_call_pending";
  call_id: string;
  tool: string;
}

/**
 * Announced when a completion STARTS: every pending steering message with
 * seq <= context_seq is now part of the context the model is generating
 * against, so its bubble can un-ghost (and relocate) immediately instead of
 * waiting for the completion to finish. The durable AssistantMessage carries
 * the same context_seq and un-ghosts identically on replay.
 */
export interface SteeringConsumed {
  seq?: number;
  session_id: string;
  type?: "steering_consumed";
  context_seq: number;
}

/**
 * Progress of the post-run project-memory pass. Never persisted: after a
 * reload the indicator simply disappears.
 */
export interface MemoryUpdate {
  seq?: number;
  session_id: string;
  type?: "memory_update";
  state: "running" | "written" | "unchanged" | "error";
}

/**
 * Progress of a project's workspace vectorization pass. Project-scoped (no
 * session_id) and never persisted: after a reload the current status is
 * re-fetched over REST (GET /api/index).
 */
export interface FileIndexProgress {
  seq?: number;
  type?: "file_index_progress";
  project_id: string;
  state: "indexing" | "ready" | "error";
  done?: number;
  total?: number;
}

/**
 * Progress of a context-compaction pass (manual or automatic). Never
 * persisted: after a reload the indicator simply disappears. `phase` is the
 * number of summary sections completed (0..9) and `label` names the current
 * section, driving a determinate progress display.
 */
export interface CompactionState {
  seq?: number;
  session_id: string;
  type?: "compaction";
  state: "running" | "done";
  phase?: number;
  label?: string;
}

/**
 * Live progress of one spawn_agents worker. Never persisted: the durable
 * story is the parent tool_call_finished report.
 */
export interface SubagentUpdate {
  seq?: number;
  session_id: string;
  type?: "subagent_update";
  call_id: string;
  worker: number;
  task: string;
  mode?: "read" | "write";
  state: "queued" | "running" | "blocked" | "done" | "error";
  activity?: string;
  report?: string;
}

/**
 * Live decoded output for one PTY terminal. Never persisted: the durable
 * story is the TerminalState snapshots plus the terminal's own ring buffer.
 * ``start_offset``/``end_offset`` are the monotonic byte cursors bounding this
 * chunk, letting the frontend dedupe overlapping replays and detect a gap
 * (a ``start_offset`` beyond the last ``end_offset`` it saw).
 */
export interface TerminalOutput {
  seq?: number;
  session_id: string;
  type?: "terminal_output";
  ts: number;
  terminal_id: string;
  start_offset: number;
  end_offset: number;
  text: string;
}

export interface SessionMeta {
  id: string;
  name?: string;
  cwd: string;
  model: string;
  autonomy?: "yolo" | "guarded";
  status?: "idle" | "running" | "attention" | "queued";
  project_id?: string | null;
  archived?: boolean;
  effort?: "default" | "low" | "medium" | "high";
  mode?: "act" | "plan";
  last_message_at?: number | null;
  last_run_reason?: ("completed" | "cancelled" | "interrupted" | "error") | null;
  last_run_seq?: number | null;
  unread?: boolean;
}

export interface Changeset {
  index: number;
  path: string;
  added: number;
  removed: number;
  diff: string;
  status?: "pending" | "kept" | "reverted";
  session_id?: string | null;
  call_id?: string | null;
  before_hash?: string | null;
  after_hash?: string | null;
}

export interface Project {
  id: string;
  name: string;
  cwd: string;
  default_model?: string;
  default_autonomy?: string;
  default_effort?: string;
}

