from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pydantic import BaseModel

from forge.engine.events import DiffStats

if TYPE_CHECKING:
    from forge.store.changesets import ChangesetStore


class ToolResult(BaseModel):
    output: str
    is_error: bool = False
    diff_stats: DiffStats | None = None


@dataclass
class ToolContext:
    cwd: Path
    emit_chunk: Callable[[str], None] = field(default=lambda _t: None)
    changesets: "ChangesetStore | None" = None

    def resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self.cwd / p)


class Tool(ABC):
    name: str
    description: str
    params: dict
    read_only: bool = False

    def display(self, args: dict) -> str:
        return args.get("path") or args.get("command") or self.name

    @abstractmethod
    async def run(self, args: dict, ctx: ToolContext) -> ToolResult: ...


def openai_spec(tool: Tool) -> dict:
    return {"type": "function", "function": {
        "name": tool.name, "description": tool.description, "parameters": tool.params}}


def truncate_middle(s: str, max_chars: int = 30_000) -> str:
    if len(s) <= max_chars:
        return s
    half = max_chars // 2
    return f"{s[:half]}\n… [{len(s) - max_chars} chars truncated] …\n{s[-half:]}"
