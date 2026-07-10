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


class RenameSession(BaseModel):
    name: str


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
