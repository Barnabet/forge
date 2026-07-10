/* AUTO-GENERATED from the engine Pydantic models — do not edit.
 * Regenerate with: pnpm gen:protocol */

export type Event =
  | SessionCreated
  | SessionRenamed
  | StatusChanged
  | AutonomyChanged
  | ModelChanged
  | UserMessage
  | AssistantMessage
  | ToolCallStarted
  | ToolCallFinished
  | ApprovalRequested
  | ApprovalResolved
  | PolicyAdded
  | ContextCompacted
  | RunFinished
  | ErrorEvent;

export interface SessionCreated {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "session_created";
  name: string;
  cwd: string;
  model: string;
  autonomy: "yolo" | "guarded";
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
}
export interface AssistantMessage {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "assistant_message";
  text: string;
  tool_calls?: ToolCallSpec[];
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
}
export interface DiffStats {
  path: string;
  added: number;
  removed: number;
  changeset_index: number;
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
}
export interface ErrorEvent {
  seq?: number;
  session_id: string;
  ts: number;
  type?: "error";
  message: string;
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

export interface SessionMeta {
  id: string;
  name?: string;
  cwd: string;
  model: string;
  autonomy?: "yolo" | "guarded";
  status?: "idle" | "running" | "attention" | "queued";
}

export interface Changeset {
  index: number;
  path: string;
  added: number;
  removed: number;
  diff: string;
  status?: "pending" | "kept" | "reverted";
}

