from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ValidationError


class RewindIntent(BaseModel):
    """Durable description of an in-flight rewind. Written atomically before the
    first destructive workspace restore so a crash between the restore and the
    persistence of the ``history_rewound`` marker (and any replacement message)
    can be recovered deterministically on rehydrate."""
    target_user_seq: int
    target_checkpoint: str
    safety_checkpoint: str
    replacement: bool
    replacement_text: str = ""
    replacement_images: list[str] = []


class RewindIntentStore:
    """Single-file per-session store for the current rewind intent. At most one
    intent exists at a time; its presence on rehydrate signals an interrupted
    rewind whose recovery is decided by whether the marker durably landed."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> RewindIntent | None:
        try:
            data = self.path.read_text()
        except FileNotFoundError:
            return None
        try:
            return RewindIntent.model_validate_json(data)
        except ValidationError:
            # The atomic writer never leaves a partial main file, so this only
            # happens on a hand-corrupted file; treat as no intent.
            return None

    def write(self, intent: RewindIntent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w") as f:
            f.write(intent.model_dump_json())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        # fsync the directory so the rename itself is durable across a crash.
        dfd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
