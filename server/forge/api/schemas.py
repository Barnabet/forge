from __future__ import annotations

from pydantic import BaseModel


class CreateSession(BaseModel):
    cwd: str | None = None
    model: str | None = None
    autonomy: str | None = None
    project_id: str | None = None
    effort: str | None = None


class PostMessage(BaseModel):
    text: str
    images: list[str] = []  # data URLs (base64)


class AlwaysPolicy(BaseModel):
    pattern: str
    scope: str = "session"


class ResolveApproval(BaseModel):
    decision: str  # "allow" | "deny"
    always: AlwaysPolicy | None = None


class SetAutonomy(BaseModel):
    autonomy: str


class SetModel(BaseModel):
    model: str


class SetEffort(BaseModel):
    effort: str


class SetMode(BaseModel):
    mode: str


class ResolvePlan(BaseModel):
    decision: str  # "approve" | "revise"
    feedback: str = ""


class RenameSession(BaseModel):
    name: str


class Rewind(BaseModel):
    target_user_seq: int
    # Omitted (None) → rewind-only. Present → edit-and-resend; an empty
    # replacement (blank text and no images) is rejected with 400.
    text: str | None = None
    images: list[str] | None = None


class CreateProject(BaseModel):
    name: str
    cwd: str
    default_model: str = ""
    default_autonomy: str = ""
    default_effort: str = ""


class UpdateProject(BaseModel):
    name: str | None = None
    cwd: str | None = None
    default_model: str | None = None
    default_autonomy: str | None = None
    default_effort: str | None = None


class UpdateConfig(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    default_autonomy: str | None = None
    max_concurrent: int | None = None
    max_resident_sessions: int | None = None
    serper_api_key: str | None = None
    firecrawl_api_key: str | None = None
    openrouter_api_key: str | None = None
    embedding_model: str | None = None
    image_model: str | None = None
    memory_similarity_threshold: float | None = None
    max_subagents: int | None = None
    subagent_max_turns: int | None = None
    subagent_model: str | None = None
    memory_model: str | None = None
    compaction_model: str | None = None


class TerminalWrite(BaseModel):
    data: str


class TerminalResize(BaseModel):
    cols: int
    rows: int


class TerminalSignal(BaseModel):
    signal: str  # "INT" | "TERM" | "KILL" | "WINCH"


class FsPath(BaseModel):
    path: str


class FsMove(BaseModel):
    src: str
    dst: str
