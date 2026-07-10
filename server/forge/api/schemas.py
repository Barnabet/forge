from __future__ import annotations

from pydantic import BaseModel


class CreateSession(BaseModel):
    cwd: str | None = None
    model: str | None = None
    autonomy: str | None = None


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


class RenameSession(BaseModel):
    name: str
