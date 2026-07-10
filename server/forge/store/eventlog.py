from __future__ import annotations

import json
from pathlib import Path

from forge.engine.events import Event, parse_event


class EventLog:
    """Append-only JSONL log of durable events for one session."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[Event] = []
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    self._events.append(parse_event(json.loads(line)))

    @property
    def last_seq(self) -> int:
        return self._events[-1].seq if self._events else 0

    def append(self, event) -> Event:
        stamped = event.model_copy(update={"seq": self.last_seq + 1})
        with self.path.open("a") as f:
            f.write(json.dumps(stamped.model_dump(mode="json")) + "\n")
        self._events.append(stamped)
        return stamped

    def read(self, after_seq: int = 0) -> list[Event]:
        return [e for e in self._events if e.seq > after_seq]
