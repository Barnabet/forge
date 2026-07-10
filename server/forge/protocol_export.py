"""Print a JSON Schema bundle of the wire protocol for web codegen."""
from __future__ import annotations

import json

from pydantic import TypeAdapter

from forge.engine.actor import SessionMeta
from forge.engine.events import (
    Event, OutputChunk, SessionDeleted, TextDelta, Todo, ToolCallPending,
)
from forge.store.changesets import Changeset
from forge.store.projects import Project

if __name__ == "__main__":
    bundle = {
        "event": TypeAdapter(Event).json_schema(),
        "text_delta": TextDelta.model_json_schema(),
        "output_chunk": OutputChunk.model_json_schema(),
        "session_deleted": SessionDeleted.model_json_schema(),
        "tool_call_pending": ToolCallPending.model_json_schema(),
        "session_meta": SessionMeta.model_json_schema(),
        "changeset": Changeset.model_json_schema(),
        "project": Project.model_json_schema(),
        "todo": Todo.model_json_schema(),
    }
    print(json.dumps(bundle, indent=2))
