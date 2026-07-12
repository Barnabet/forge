from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from forge.llm.embeddings import Embedder
from forge.store.workspace_checkpoints import HARD_EXCLUDE_DIRS

_SAFE_PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]+$")
INDEX_SCHEMA_VERSION = 1
DEFAULT_THRESHOLD = 0.45
_EMBED_BATCH = 32
_MAX_PDF_SAMPLE_PAGES = 5


@dataclass
class Chunk:
    text: str
    start_line: int  # 1-indexed, inclusive
    end_line: int


@dataclass
class FileSnippet:
    path: str  # relative to the project root, posix
    start_line: int
    end_line: int
    text: str
    score: float


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"cosine dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def project_file_index_path(home: Path, project_id: str) -> Path | None:
    """One JSON index file per project, alongside its memory/ dir."""
    if not _SAFE_PROJECT_ID.fullmatch(project_id):
        return None
    return home / "projects" / project_id / "file-index.json"


# -- Part A: file walk + text extraction --------------------------------------

def _pruned(root: Path, p: Path) -> bool:
    return any(part in HARD_EXCLUDE_DIRS for part in p.relative_to(root).parts)


def list_project_files(root: Path, max_bytes: int, max_files: int) -> list[Path]:
    """Enumerate candidate files under root, preferring git's view of tracked +
    untracked-but-not-ignored files, falling back to a plain recursive walk."""
    candidates: list[Path] = []
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard"],
            cwd=root, capture_output=True, text=True)
    except FileNotFoundError:
        proc = None
    if proc is not None and proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if line:
                candidates.append((root / line))
    else:
        candidates = list(root.rglob("*"))

    out: list[Path] = []
    for p in candidates:
        try:
            if _pruned(root, p):
                continue
            if not p.is_file():
                continue
            if p.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        out.append(p.resolve())
    out = sorted(set(out))
    return out[:max_files]


def _extract_pdf(path: Path) -> str | None:
    import pdfplumber

    try:
        with pdfplumber.open(str(path)) as pdf:
            n = len(pdf.pages)
            if n == 0:
                return None
            texts = [pdf.pages[i].extract_text(layout=False) or "" for i in range(n)]
            sample = min(_MAX_PDF_SAMPLE_PAGES, n)
            total = sum(len(texts[i].strip()) for i in range(sample))
            if total < 50 * sample:  # scanned / no text layer
                return None
            body = "\n\n".join(t for t in texts if t.strip())
            return body or None
    except Exception:  # noqa: BLE001 — any pdfplumber failure → not indexable
        return None


def extract_text(path: Path) -> str | None:
    """Return the file's text content, or None when it is not indexable."""
    if path.suffix.lower() == ".pdf":
        return _extract_pdf(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    return text if text.strip() else None


# -- Part B: generic chunker ---------------------------------------------------

def chunk_file(
    text: str, max_lines: int = 80, max_chars: int = 3000, overlap: int = 15
) -> list[Chunk]:
    """Split text into contiguous line-window chunks bounded by max_lines /
    max_chars, with `overlap` lines shared between consecutive chunks so spans
    straddling a boundary stay wholly contained in some chunk. Line numbers are
    1-indexed inclusive; empty chunks are never emitted and every non-empty
    input yields at least one chunk."""
    lines = text.split("\n")
    chunks: list[Chunk] = []
    start = 0  # 0-based index of the window's first line
    while start < len(lines):
        end = start  # inclusive 0-based
        size = len(lines[start])
        while end + 1 < len(lines):
            nxt = len(lines[end + 1]) + 1  # +1 for the joining newline
            if (end - start + 1) >= max_lines or size + nxt > max_chars:
                break
            end += 1
            size += nxt
        window = lines[start:end + 1]
        chunk_text = "\n".join(window)
        if chunk_text.strip():
            chunks.append(Chunk(chunk_text, start + 1, end + 1))
        if end == len(lines) - 1:
            break  # window reached EOF; overlap would only re-emit tail lines
        # Advance, retaining `overlap` lines; always progress by at least 1.
        start = max(end + 1 - overlap, start + 1)
    return chunks


# -- Part C: the index ---------------------------------------------------------

class FileIndex:
    """Incremental embedding index over a project's source files. One JSON file
    per project; chunks re-embedded only when their content hash changes."""

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
            "files": {},
        }

    # -- persistence ----------------------------------------------------------
    def _load(self, path: Path) -> dict:
        if not path.is_file():
            return self._fresh()
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return self._fresh()
        # Rebuild when metadata is absent or incompatible with the current
        # embedder, so stale vectors never produce bad scores.
        if (data.get("schema_version") != INDEX_SCHEMA_VERSION
                or data.get("model_id") != self.embedder.model_id
                or data.get("dimensions") != self.embedder.dimensions
                or not isinstance(data.get("files"), dict)):
            return self._fresh()
        return data

    @staticmethod
    def _save(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)

    # -- sync -----------------------------------------------------------------
    async def _sync(self, root: Path, path: Path, data: dict,
                    max_bytes: int, max_files: int,
                    progress: Callable[[int, int], None] | None = None) -> bool:
        """Reconcile the loaded index against files on disk; True if changed.

        When `progress` is given it is called as `progress(done, total)` where
        `total` is the number of new chunks to embed and `done` counts embedded
        chunks — once before embedding (0, total) and after each sub-batch."""
        async with self._lock:
            # list_project_files returns resolved paths; resolve root too so
            # relative_to holds even when cwd is an unresolved symlink (e.g.
            # macOS /tmp -> /private/tmp).
            root = root.resolve()
            files = list_project_files(root, max_bytes, max_files)
            present: set[str] = set()
            changed = False
            # (rel, chunk) pairs whose vector is new and must be embedded.
            pending: dict[str, list[Chunk]] = {}
            rebuilt: dict[str, dict] = {}

            for p in files:
                text = extract_text(p)
                if text is None:
                    continue
                rel = p.relative_to(root).as_posix()
                present.add(rel)
                file_hash = _sha(text)
                entry = data["files"].get(rel)
                if entry and entry.get("hash") == file_hash:
                    continue  # unchanged — keep existing vectors
                changed = True
                chunks = chunk_file(text)
                known = {c["hash"]: c["vector"]
                         for c in (entry or {}).get("chunks", [])}
                new = [c for c in chunks if _sha(c.text) not in known]
                rebuilt[rel] = {"hash": file_hash, "chunks": chunks,
                                "known": known}
                if new:
                    pending[rel] = new

            # Embed all new chunks of all changed files, sub-batched so a
            # progress callback can report incrementally.
            flat: list[tuple[str, Chunk]] = [
                (rel, c) for rel, cs in pending.items() for c in cs]
            total = len(flat)
            if progress is not None:
                progress(0, total)
            done = 0
            for i in range(0, total, _EMBED_BATCH):
                batch = flat[i:i + _EMBED_BATCH]
                vectors = await self.embedder.embed([c.text for _, c in batch])
                for (rel, c), v in zip(batch, vectors):
                    rebuilt[rel]["known"][_sha(c.text)] = v
                done += len(batch)
                if progress is not None:
                    progress(done, total)

            for rel, built in rebuilt.items():
                known = built["known"]
                data["files"][rel] = {
                    "hash": built["hash"],
                    "chunks": [{"hash": _sha(c.text), "start_line": c.start_line,
                                "end_line": c.end_line, "text": c.text,
                                "vector": known[_sha(c.text)]}
                               for c in built["chunks"]],
                }

            for rel in list(data["files"]):
                if rel not in present:
                    del data["files"][rel]
                    changed = True
            return changed

    # -- reindex --------------------------------------------------------------
    async def reindex(self, root: Path, project_id: str,
                      progress: Callable[[int, int], None] | None = None,
                      max_bytes: int = 262144, max_files: int = 5000) -> None:
        """Proactively bring a project's index up to date, reporting progress.
        Unchanged files reuse their cached vectors, so a warm project embeds
        nothing."""
        path = project_file_index_path(self.home, project_id)
        if path is None:
            return
        root = root.resolve()
        data = self._load(path)
        if await self._sync(root, path, data, max_bytes, max_files, progress):
            self._save(path, data)

    # -- search ---------------------------------------------------------------
    async def search(self, query: str, root: Path, project_id: str | None,
                     top_k: int = 8, threshold: float | None = None,
                     max_bytes: int = 262144,
                     max_files: int = 5000) -> list[FileSnippet]:
        if project_id is None:
            return []
        path = project_file_index_path(self.home, project_id)
        if path is None:
            return []
        if threshold is None:
            threshold = self.threshold

        data = self._load(path)
        if await self._sync(root, path, data, max_bytes, max_files):
            self._save(path, data)

        qvec = await self.embedder.embed_query(query)
        scored: list[FileSnippet] = []
        for rel, entry in data["files"].items():
            for c in entry["chunks"]:
                if len(c["vector"]) != len(qvec):
                    continue  # dimension mismatch guard
                score = _cosine(qvec, c["vector"])
                if score >= threshold:
                    scored.append(FileSnippet(
                        path=rel, start_line=c["start_line"],
                        end_line=c["end_line"], text=c["text"], score=score))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]
