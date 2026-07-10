from __future__ import annotations

from typing import get_args

from forge.engine.events import Todo, TodoStatus
from forge.tools.base import Tool, ToolContext, ToolResult

_STATUSES = get_args(TodoStatus)


class UpdateTodosTool(Tool):
    name = "update_todos"
    description = (
        "Replace your task checklist with the given snapshot (the FULL list every "
        "call, not a delta). Use it to track multi-step work: keep exactly one "
        "item in_progress, and update immediately as steps complete."
    )
    params = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "status": {"type": "string", "enum": list(_STATUSES)},
                    },
                    "required": ["text", "status"],
                },
            }
        },
        "required": ["todos"],
    }
    read_only = True  # touches nothing outside the session log

    def display(self, args: dict) -> str:
        todos = args.get("todos") or []
        done = sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "completed")
        return f"{done}/{len(todos)} done"

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        raw = args.get("todos")
        if not isinstance(raw, list) or not raw:
            return ToolResult(output="todos must be a non-empty array", is_error=True)
        todos: list[Todo] = []
        for i, item in enumerate(raw, 1):
            if not isinstance(item, dict) or not isinstance(item.get("text"), str) \
                    or not item["text"].strip():
                return ToolResult(output=f"todo {i} must have non-empty text", is_error=True)
            if item.get("status") not in _STATUSES:
                return ToolResult(
                    output=f"todo {i} has invalid status: {item.get('status')!r}", is_error=True)
            todos.append(Todo(text=item["text"].strip(), status=item["status"]))
        return ToolResult(output="Todos updated.", todos=todos)
