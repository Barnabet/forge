from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel

from forge.tools.search import SKIP_DIRS

# Directories that must never be captured. Combines the source-tree exclusions
# already treated as non-source (dependency/build/cache dirs, .git) with Forge's
# own per-session data directory. Project .gitignore rules are honored on top of
# these hard excludes via git's normal ignore handling.
HARD_EXCLUDE_DIRS = SKIP_DIRS | {
    ".forge", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache",
    ".idea", ".gradle", ".next", ".turbo", ".tox", "target", "vendor",
}


class WorkspaceCheckpointError(RuntimeError):
    """Raised when a checkpoint capture or restore cannot complete."""


def _parse_name_status_z(out: str) -> list[tuple[str, str]]:
    """Parse ``git diff-tree --name-status -z`` output into (status, path)
    pairs. A rename (Rxxx) carries two path fields (old, new); it is reduced to
    a delete of the old path and an add of the new so downstream consumers can
    treat every changed path uniformly without rename bookkeeping."""
    parts = [p for p in out.split("\0") if p != ""]
    changes: list[tuple[str, str]] = []
    i = 0
    while i < len(parts):
        code = parts[i]
        letter = code[:1]
        if letter in ("R", "C") and i + 2 < len(parts):
            old_path, new_path = parts[i + 1], parts[i + 2]
            if letter == "R":
                changes.append(("D", old_path))
            changes.append(("A", new_path))
            i += 3
        else:
            path = parts[i + 1] if i + 1 < len(parts) else ""
            changes.append((letter, path))
            i += 2
    return changes


class Checkpoint(BaseModel):
    id: str
    tree: str  # git tree object sha for the captured worktree
    ts: float
    label: str = ""


class WorkspaceCheckpointStore:
    """Per-session shadow Git object store that snapshots and restores the
    managed workspace without ever touching the project's own repository.

    A bare Git repo lives under ``sessions/<sid>/workspace.git``. All operations
    target that object store and the session's private index explicitly, with
    system/global config disabled and repository auto-discovery suppressed, so
    the project's ``.git`` (index, branch, config) is never read or written.
    """

    def __init__(self, git_dir: Path, work_tree: Path):
        self.git_dir = Path(git_dir)
        self.work_tree = Path(work_tree)
        self.index_file = self.git_dir / "capture.index"
        self._meta_file = self.git_dir / "checkpoints.jsonl"
        self._checkpoints: list[Checkpoint] = []
        self._init_repo()
        self._load()

    # -- setup / persistence -------------------------------------------------
    def _init_repo(self) -> None:
        if not (self.git_dir / "HEAD").exists():
            self.git_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(
                    ["git", "init", "--bare", "-q", str(self.git_dir)],
                    check=True, capture_output=True, text=True)
            except FileNotFoundError as e:  # git not installed
                raise WorkspaceCheckpointError(
                    "git executable not found; workspace checkpoints unavailable"
                ) from e
            except subprocess.CalledProcessError as e:
                raise WorkspaceCheckpointError(
                    f"failed to initialize checkpoint store: {e.stderr.strip()}"
                ) from e
        self._write_excludes()

    def _write_excludes(self) -> None:
        info = self.git_dir / "info"
        info.mkdir(parents=True, exist_ok=True)
        patterns = [f"{d}/" for d in sorted(HARD_EXCLUDE_DIRS)]
        # FORGE_HOME is usually outside the project, but tests and custom setups
        # may place a session directory beneath the managed worktree. Never stage
        # our own event log/checkpoint object store: doing so makes snapshots
        # self-referential and can invalidate objects during restore.
        session_dir = self.git_dir.parent.resolve()
        work_tree = self.work_tree.resolve()
        if session_dir.is_relative_to(work_tree):
            rel = session_dir.relative_to(work_tree).as_posix()
            patterns.append(f"/{rel}/")
        (info / "exclude").write_text("\n".join(patterns) + "\n")

    def _load(self) -> None:
        if self._meta_file.exists():
            self._checkpoints = [
                Checkpoint.model_validate(json.loads(line))
                for line in self._meta_file.read_text().splitlines() if line.strip()]

    def _save(self) -> None:
        self._meta_file.write_text(
            "".join(json.dumps(c.model_dump()) + "\n" for c in self._checkpoints))

    # -- git plumbing --------------------------------------------------------
    def _env(self) -> dict[str, str]:
        return {
            **os.environ,
            "GIT_DIR": str(self.git_dir),
            "GIT_WORK_TREE": str(self.work_tree),
            "GIT_INDEX_FILE": str(self.index_file),
            # Never discover or honor any repository/config but our own.
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Forge",
            "GIT_AUTHOR_EMAIL": "forge@localhost",
            "GIT_COMMITTER_NAME": "Forge",
            "GIT_COMMITTER_EMAIL": "forge@localhost",
        }

    def _git(self, *args: str) -> str:
        # core.bare=false lets us use the shadow bare repo against an explicit
        # work tree; the repo is otherwise bare so nothing leaks into a branch.
        try:
            proc = subprocess.run(
                ["git", "-c", "core.bare=false", *args],
                cwd=str(self.work_tree), env=self._env(),
                check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise WorkspaceCheckpointError(
                f"git {args[0]} failed: {(e.stderr or e.stdout).strip()}") from e
        return proc.stdout.strip()

    def _write_tree(self) -> str:
        """Stage the current worktree into the private index and return its tree
        sha. Honors project ignore rules plus the hard excludes; leaves the index
        matching the worktree so a subsequent two-way merge is well-defined."""
        self._git("read-tree", "--empty")
        self._git("add", "--all", "--", ".")
        return self._git("write-tree")

    def _is_tree(self, sha: str) -> bool:
        try:
            return self._git("cat-file", "-t", sha) == "tree"
        except WorkspaceCheckpointError:
            return False

    # -- non-checkpoint inspection ------------------------------------------
    def snapshot_tree(self) -> str:
        """Return the git tree sha of the current worktree WITHOUT recording a
        checkpoint. Uses the same staging/exclude rules as ``capture`` so the
        sha is directly comparable to checkpoint trees, but appends no metadata.
        Callers (SharedWorkspace reconcile/bracketing) use this to observe tree
        state cheaply and durably via the object store."""
        if not self.work_tree.is_dir():
            raise WorkspaceCheckpointError(f"work tree missing: {self.work_tree}")
        return self._write_tree()

    def diff_trees(self, old_tree: str, new_tree: str) -> list[tuple[str, str]]:
        """Canonical list of (status, relative_path) changed between two tree
        shas. Status is one of A/M/D/R (renames reduced to their new path plus a
        delete of the old path for simple, order-independent consumption). Paths
        are worktree-relative POSIX strings honoring the store's excludes."""
        if not (self._is_tree(old_tree) and self._is_tree(new_tree)):
            raise WorkspaceCheckpointError("diff_trees requires two tree objects")
        out = self._git("diff-tree", "-r", "--no-commit-id", "--name-status",
                        "-z", old_tree, new_tree)
        return _parse_name_status_z(out)

    def blob_content_hashes(self, tree: str, paths: list[str]
                            ) -> dict[str, str | None]:
        """SHA-256 (matching ``_hash_path``/``hash_text``) of each path's blob
        content in ``tree``. A path absent from the tree maps to None. Reads the
        raw blob bytes from the object store, so callers get the same content
        hash they would compute from the on-disk file without touching disk."""
        if not paths:
            return {}
        if not self._is_tree(tree):
            raise WorkspaceCheckpointError("blob_content_hashes requires a tree")
        present = self.tree_paths(tree)
        out: dict[str, str | None] = {}
        for path in paths:
            blob = present.get(path)
            if blob is None:
                out[path] = None
                continue
            try:
                data = subprocess.run(
                    ["git", "-c", "core.bare=false", "cat-file", "blob", blob],
                    cwd=str(self.work_tree), env=self._env(),
                    check=True, capture_output=True).stdout
            except subprocess.CalledProcessError as e:
                raise WorkspaceCheckpointError(
                    f"git cat-file failed: {(e.stderr or b'').decode().strip()}"
                ) from e
            out[path] = hashlib.sha256(data).hexdigest()
        return out

    def tree_paths(self, tree: str) -> dict[str, str]:
        """Map of relative POSIX path -> blob sha for every file in ``tree``.
        Lets SharedWorkspace attribute per-path content without re-reading the
        worktree."""
        if not self._is_tree(tree):
            raise WorkspaceCheckpointError("tree_paths requires a tree object")
        out = self._git("ls-tree", "-r", "-z", tree)
        paths: dict[str, str] = {}
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            fields = meta.split(" ")
            if len(fields) >= 3:
                paths[path] = fields[2]
        return paths

    # -- public API ----------------------------------------------------------
    def capture(self, label: str = "") -> Checkpoint:
        """Snapshot the current workspace. Identical consecutive trees are
        deduplicated (the git objects dedupe inherently; the metadata reuses the
        prior checkpoint) so repeated captures are cheap and stable."""
        if not self.work_tree.is_dir():
            raise WorkspaceCheckpointError(f"work tree missing: {self.work_tree}")
        tree = self._write_tree()
        if self._checkpoints and self._checkpoints[-1].tree == tree:
            return self._checkpoints[-1]
        cp = Checkpoint(id=f"cp{len(self._checkpoints) + 1}", tree=tree,
                        ts=time.time(), label=label)
        self._checkpoints.append(cp)
        self._save()
        return cp

    def restore(self, checkpoint_id: str) -> Checkpoint:
        """Restore the workspace to a captured checkpoint. Takes a safety
        snapshot of the live state first; if the restore fails partway, the
        worktree is rolled back to that safety snapshot before re-raising."""
        cp = self.get(checkpoint_id)
        if not self.work_tree.is_dir():
            raise WorkspaceCheckpointError(f"work tree missing: {self.work_tree}")
        if not self._is_tree(cp.tree):
            raise WorkspaceCheckpointError(
                f"checkpoint {checkpoint_id} tree missing from object store")
        safety = self._write_tree()  # live state; index now matches worktree
        try:
            self._git("read-tree", "-m", "-u", safety, cp.tree)
        except WorkspaceCheckpointError:
            self._rollback(safety)
            raise
        return cp

    def _rollback(self, safety_tree: str) -> None:
        try:
            current = self._write_tree()
            self._git("read-tree", "-m", "-u", current, safety_tree)
        except WorkspaceCheckpointError:
            # Best-effort: the primary restore error is what we re-raise.
            pass

    def get(self, checkpoint_id: str) -> Checkpoint:
        for cp in self._checkpoints:
            if cp.id == checkpoint_id:
                return cp
        raise KeyError(checkpoint_id)

    def list(self) -> list[Checkpoint]:
        return list(self._checkpoints)
