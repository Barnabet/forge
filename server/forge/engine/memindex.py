from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from forge.engine.memory import (
    GLOBAL_REGIONS, PROJECT_REGIONS, global_memory_dir, project_memory_dir,
)
from forge.llm.embeddings import Embedder

_MAX_CHUNK_CHARS = 800
INDEX_FILE = "index.json"
INDEX_SCHEMA_VERSION = 3
DEFAULT_THRESHOLD = 0.45


@dataclass
class Chunk:
    text: str
    start_line: int  # 1-indexed, inclusive
    end_line: int


@dataclass
class Snippet:
    tier: str
    region: str
    start_line: int
    end_line: int
    text: str
    score: float


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _is_heading_only(text: str) -> bool:
    """True when every non-blank line is a Markdown heading, i.e. the chunk
    carries no substantive body to retrieve on."""
    non_blank = [ln for ln in text.split("\n") if ln.strip()]
    return bool(non_blank) and all(
        ln.lstrip().startswith("#") for ln in non_blank)


def chunk_region(text: str) -> list[Chunk]:
    """Split region markdown into retrieval chunks: one chunk per block of
    consecutive non-blank lines (headings start a new block); blocks over
    ~800 chars split at line boundaries. Chunks that contain only heading
    line(s) with no substantive body are dropped."""
    lines = text.split("\n")
    blocks: list[tuple[int, int]] = []  # (start_idx, end_idx) inclusive, 0-based
    start: int | None = None
    for i, line in enumerate(lines):
        if not line.strip():
            if start is not None:
                blocks.append((start, i - 1))
                start = None
        elif line.lstrip().startswith("#") and start is not None:
            blocks.append((start, i - 1))
            start = i
        elif start is None:
            start = i
    if start is not None:
        blocks.append((start, len(lines) - 1))

    chunks: list[Chunk] = []
    for bs, be in blocks:
        s = bs
        while s <= be:
            e, size = s, len(lines[s]) + 1
            while e < be and size + len(lines[e + 1]) + 1 <= _MAX_CHUNK_CHARS:
                e += 1
                size += len(lines[e]) + 1
            chunk_text = "\n".join(lines[s:e + 1])
            if not _is_heading_only(chunk_text):
                chunks.append(Chunk(chunk_text, s + 1, e + 1))
            s = e + 1
    return chunks


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(
            f"cosine dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class MemoryIndex:
    """Incremental embedding index over the memory brain. One index.json per
    tier directory; chunks re-embedded only when their content hash changes."""

    def __init__(self, home: Path, embedder: Embedder,
                 threshold: float = DEFAULT_THRESHOLD):
        self.home = home
        self.embedder = embedder
        self.threshold = threshold
        self._lock = asyncio.Lock()

    def _fresh(self) -> dict:
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "model_id": self.embedder.model_id,
            "dimensions": self.embedder.dimensions,
            "regions": {},
        }

    # -- persistence ----------------------------------------------------------
    def _load(self, dir_: Path) -> dict:
        path = dir_ / INDEX_FILE
        if not path.is_file():
            return self._fresh()
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return self._fresh()
        # Rebuild when metadata is absent (legacy index) or incompatible with
        # the current embedder, so stale vectors never produce bad scores.
        if (data.get("schema_version") != INDEX_SCHEMA_VERSION
                or data.get("model_id") != self.embedder.model_id
                or data.get("dimensions") != self.embedder.dimensions
                or not isinstance(data.get("regions"), dict)):
            return self._fresh()
        return data

    @staticmethod
    def _save(dir_: Path, data: dict) -> None:
        dir_.mkdir(parents=True, exist_ok=True)
        tmp = dir_ / f".{INDEX_FILE}.tmp"
        tmp.write_text(json.dumps(data))
        os.replace(tmp, dir_ / INDEX_FILE)

    # -- sync -----------------------------------------------------------------
    async def sync_region(self, tier: str, region: str,
                          project_id: str | None = None) -> None:
        dir_ = self._tier_dir(tier, project_id)
        if dir_ is None:
            return
        async with self._lock:
            data = self._load(dir_)
            if await self._sync_one(dir_, region, data):
                self._save(dir_, data)

    async def _sync_one(self, dir_: Path, region: str, data: dict) -> bool:
        """Reconcile one region in the loaded index; returns True if changed."""
        path = dir_ / f"{region}.md"
        text = path.read_text() if path.is_file() else ""
        file_hash = _sha(text)
        entry = data["regions"].get(region)
        if entry and entry.get("hash") == file_hash:
            return False
        if not text.strip():
            if entry is None:
                return False
            del data["regions"][region]
            return True
        chunks = chunk_region(text)
        known = {c["hash"]: c["vector"] for c in (entry or {}).get("chunks", [])}
        new = [c for c in chunks if _sha(c.text) not in known]
        vectors = await self.embedder.embed([c.text for c in new])
        for c, v in zip(new, vectors):
            known[_sha(c.text)] = v
        data["regions"][region] = {
            "hash": file_hash,
            "chunks": [{"hash": _sha(c.text), "start_line": c.start_line,
                        "end_line": c.end_line, "text": c.text,
                        "vector": known[_sha(c.text)]} for c in chunks],
        }
        return True

    async def _sync_tier(self, dir_: Path, regions: tuple[str, ...]) -> dict:
        async with self._lock:
            data = self._load(dir_)
            changed = False
            for region in regions:
                changed |= await self._sync_one(dir_, region, data)
            if changed:
                self._save(dir_, data)
            return data

    def _tier_dir(self, tier: str, project_id: str | None) -> Path | None:
        if tier == "global":
            return global_memory_dir(self.home)
        if tier == "project" and project_id:
            return project_memory_dir(self.home, project_id)
        return None

    # -- search ---------------------------------------------------------------
    async def search(self, query: str, project_id: str | None,
                     top_k: int = 5,
                     threshold: float | None = None) -> list[Snippet]:
        if threshold is None:
            threshold = self.threshold
        tiers: list[tuple[str, Path, tuple[str, ...]]] = [
            ("global", global_memory_dir(self.home), GLOBAL_REGIONS)]
        if project_id and (pdir := project_memory_dir(self.home, project_id)):
            tiers.append(("project", pdir, PROJECT_REGIONS))

        qvec = await self.embedder.embed_query(query)
        scored: list[Snippet] = []
        for tier, dir_, regions in tiers:
            data = await self._sync_tier(dir_, regions)
            for region, entry in data["regions"].items():
                for c in entry["chunks"]:
                    score = _cosine(qvec, c["vector"])
                    if score >= threshold:
                        scored.append(Snippet(
                            tier=tier, region=region, start_line=c["start_line"],
                            end_line=c["end_line"], text=c["text"], score=score))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]
