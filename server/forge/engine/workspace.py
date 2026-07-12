from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from pathlib import Path

from forge.store.workspace_activity import (
    ActivityOrigin, WorkspaceActivity, WorkspaceActivityStore,
)
from forge.store.workspace_checkpoints import (
    WorkspaceCheckpointError, WorkspaceCheckpointStore,
)


def _hash_path(path: Path) -> str | None:
    """SHA-256 of a path's bytes, or None if the path is missing/unreadable as a
    regular file. Distinguishes a missing path (None) from an empty file (a real
    hash of b"")."""
    try:
        data = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (IsADirectoryError, PermissionError, OSError):
        return None
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    """SHA-256 of ``text`` encoded as UTF-8. Matches ``_hash_path`` for a file
    whose bytes are that UTF-8 encoding, so a reader can register the exact
    content it returned without re-reading the file from disk."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fsync_dir(directory: Path) -> None:
    """fsync a directory so a newly created entry within it is durable across a
    crash. No-op on platforms that cannot open a directory for fsync."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class SharedWorkspace:
    """Coordinator shared by every SessionActor whose cwd resolves to the same
    real directory. Owns the single mutation lock, the durable activity log, and
    per-session per-path observed content baselines.

    This is a shared-tree design: there is exactly one live working tree. The
    lock serializes Forge-controlled mutations across all sessions on that tree;
    the baselines and activity log give later tasks the raw material for
    stale-write and rewind protection. Baseline/hash methods are synchronous and
    cheap; whole-tree scanning is intentionally out of scope here.
    """

    def __init__(self, resolved_cwd: Path, activity: WorkspaceActivityStore):
        self.cwd = resolved_cwd
        self.lock = asyncio.Lock()
        self.activity = activity
        # (session_id, resolved-path-str) -> last observed content hash (or None
        # for observed-missing). Keyed per session so each session tracks what it
        # last saw independently. Guarded by ``_baselines_lock`` since the
        # synchronous baseline API may be called from multiple threads.
        self._baselines: dict[tuple[str | None, str], str | None] = {}
        self._baselines_lock = threading.Lock()
        # Durable whole-tree tracker: a cwd-scoped shadow Git object store under
        # this workspace's data directory, separate from any session checkpoint
        # store. Lets us observe the entire tree cheaply and diff arbitrary
        # (including out-of-band) edits. Instantiated lazily; if git is
        # unavailable the tree APIs degrade to no-ops rather than failing tools.
        self._tracker_dir = activity.dir / "tree.git"
        self._cursor_file = activity.dir / "tree-cursor.json"
        self._tracker: WorkspaceCheckpointStore | None = None
        self._tracker_failed = False
        self._cursor: dict | None = None

    # -- content observation ------------------------------------------------
    def _key(self, session_id: str | None, path: Path) -> tuple[str | None, str]:
        return (session_id, str(self._resolve(path)))

    def canonical(self, path: Path) -> Path:
        """Public canonical form of ``path`` under this workspace: the stable key
        used for baselines and activity records. Callers (tools, API) should use
        this rather than reaching into the private resolver, so provenance keys
        always match what the workspace stores."""
        return self._resolve(path)

    def _resolve(self, path: Path) -> Path:
        p = path if path.is_absolute() else (self.cwd / path)
        # Canonicalize stably whether or not the leaf currently exists: resolve
        # the parent (which normalizes symlinked ancestors and "..") and re-join
        # the final component by name. Using resolve() on the leaf directly would
        # follow a symlinked leaf when present but not when missing, so the same
        # logical path would get different keys across create/delete. Resolving
        # only the parent keeps one stable baseline key across a path's lifecycle.
        parent = Path(p.parent).resolve()
        return parent / p.name

    def current_hash(self, path: Path) -> str | None:
        """SHA-256 of the path's current bytes, or None if it is missing."""
        return _hash_path(self._resolve(path))

    def observe(self, session_id: str | None, path: Path) -> str | None:
        """Record the path's current content as this session's baseline and
        return that hash (None if missing)."""
        h = self.current_hash(path)
        with self._baselines_lock:
            self._baselines[self._key(session_id, path)] = h
        return h

    def observe_hash(self, session_id: str | None, path: Path,
                     content_hash: str | None) -> str | None:
        """Record a caller-supplied hash as this session's baseline. Lets a
        reader register the exact bytes it just returned (avoiding a re-read
        TOCTOU) rather than having the workspace re-hash the file from disk."""
        with self._baselines_lock:
            self._baselines[self._key(session_id, path)] = content_hash
        return content_hash

    def baseline(self, session_id: str | None, path: Path) -> str | None:
        """This session's last observed hash for the path, or None if it has
        never been observed (indistinguishable from observed-missing; callers
        that need to tell them apart should use ``has_baseline``)."""
        with self._baselines_lock:
            return self._baselines.get(self._key(session_id, path))

    def has_baseline(self, session_id: str | None, path: Path) -> bool:
        with self._baselines_lock:
            return self._key(session_id, path) in self._baselines

    def current_state(self, path: Path) -> str | None:
        return self.current_hash(path)

    # -- change recording ---------------------------------------------------
    def record_controlled_change(self, *, session_id: str | None, action: str,
                                 paths: list[Path], origin: ActivityOrigin = "tool",
                                 call_id: str | None = None,
                                 before: dict[str, str | None] | None = None,
                                 note: str | None = None,
                                 baseline_owner: str | None = None,
                                 ) -> WorkspaceActivity:
        """Record a Forge-controlled mutation. Captures after-hashes from disk
        and refreshes this session's baselines to the new content. ``before`` may
        be supplied by the caller (captured pre-mutation); when omitted it falls
        back to this session's known baselines."""
        resolved = [self._resolve(p) for p in paths]
        observation_owner = (baseline_owner if baseline_owner is not None
                             else session_id)
        before_map: dict[str, str | None] = dict(before or {})
        after_map: dict[str, str | None] = {}
        for orig, rp in zip(paths, resolved):
            key = str(rp)
            h = _hash_path(rp)
            after_map[key] = h
            with self._baselines_lock:
                if key not in before_map:
                    before_map[key] = self._baselines.get((observation_owner, key))
                self._baselines[(observation_owner, key)] = h
        rec = self.activity.append(
            origin=origin, action=action, paths=[str(p) for p in resolved],
            session_id=session_id, call_id=call_id, before=before_map,
            after=after_map, note=note)
        # Move the tree cursor past this direct edit so a subsequent reconcile
        # does not re-observe and relabel it as an external out-of-band change.
        self.advance_cursor()
        return rec

    def record_external_change(self, *, action: str, paths: list[Path],
                               note: str | None = None,
                               before: dict[str, str | None] | None = None
                               ) -> WorkspaceActivity:
        """Record a change observed to have happened outside Forge's control."""
        resolved = [self._resolve(p) for p in paths]
        after_map = {str(rp): _hash_path(rp) for rp in resolved}
        return self.activity.append(
            origin="external", action=action, paths=[str(p) for p in resolved],
            before=dict(before or {}), after=after_map, note=note)

    def record_checkpoint(self, *, session_id: str | None, user_seq: int,
                          checkpoint: str) -> WorkspaceActivity:
        """Record a provenance marker for a captured user-message checkpoint.

        Claims no changed paths (the capture only snapshots the tree). The note
        carries the ``user_seq`` and checkpoint id so a later audit can tie the
        rewind point to the message that produced it. Call under the workspace
        lock, after ``reconcile`` and the capture, so the cursor already reflects
        the snapshotted tree and this marker does not disturb it."""
        return self.activity.append(
            origin="checkpoint", action="checkpoint", session_id=session_id,
            note=f"user_seq={user_seq} checkpoint={checkpoint}")

    def record_rewind(self, *, session_id: str | None, paths: list[str],
                      before: dict[str, str | None],
                      after: dict[str, str | None], note: str | None = None
                      ) -> WorkspaceActivity:
        """Record a provenance marker for a successful workspace rewind and
        advance the cursor to the restored tree so a later reconcile does not
        relabel the restore as external. ``paths`` are canonical path strings the
        restore changed, with before/after content hashes."""
        rec = self.activity.append(
            origin="rewind", action="rewind", session_id=session_id,
            paths=list(paths), before=dict(before), after=dict(after), note=note)
        # The restore already mutated the live tree; move the cursor past it so
        # reconcile treats the restored content as attributed, not external.
        self.advance_cursor()
        return rec

    def record_terminal_launch(self, *, session_id: str | None,
                               call_id: str | None,
                               note: str | None = None) -> WorkspaceActivity:
        """Record a marker for a persistent PTY terminal the agent launched.

        Deliberately claims no changed paths: the terminal writes asynchronously
        and outlives this run, so any files it mutates surface as ``external`` on
        a future ``reconcile`` rather than being attributed here. The marker only
        notes that this session/call started a terminal."""
        return self.activity.append(
            origin="terminal", action="launch", session_id=session_id,
            call_id=call_id, note=note)

    def recent_activity(self, limit: int = 50) -> list[WorkspaceActivity]:
        return self.activity.recent(limit)

    # -- whole-tree reconciliation ------------------------------------------
    # All of these run synchronously and MUST be called while ``self.lock`` is
    # held (the actor/API already hold it around mutations), so the snapshot,
    # diff, activity append, and cursor update are consistent with each other
    # and with any impending mutation.

    def _tracker_store(self) -> WorkspaceCheckpointStore | None:
        """Lazily build the cwd-scoped shadow tree tracker. Returns None (once,
        stickily) if git is unavailable so tree features degrade to no-ops
        instead of breaking tools."""
        if self._tracker is not None:
            return self._tracker
        if self._tracker_failed:
            return None
        try:
            self._tracker = WorkspaceCheckpointStore(self._tracker_dir, self.cwd)
        except WorkspaceCheckpointError:
            self._tracker_failed = True
            return None
        return self._tracker

    def _load_cursor(self) -> dict | None:
        if self._cursor is not None:
            return self._cursor
        if self._cursor_file.exists():
            try:
                self._cursor = json.loads(self._cursor_file.read_text())
            except (json.JSONDecodeError, OSError):
                self._cursor = None
        return self._cursor

    def _write_cursor(self, tree: str, seq: int | None) -> None:
        """Atomically and durably persist the cursor (tree sha + optional
        activity seq): write a temp file, fsync its bytes, atomically rename it
        over the cursor, and fsync the parent directory the first time the cursor
        file appears so the new directory entry survives a crash."""
        self._cursor = {"tree": tree, "seq": seq}
        self._cursor_file.parent.mkdir(parents=True, exist_ok=True)
        is_new_file = not self._cursor_file.exists()
        tmp = self._cursor_file.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            f.write(json.dumps(self._cursor))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._cursor_file)
        if is_new_file:
            _fsync_dir(self._cursor_file.parent)

    def _ensure_cursor(self, store: WorkspaceCheckpointStore) -> str | None:
        """On first use, snapshot the current tree and seed the cursor without
        recording any external activity (the whole project is not "external").
        Returns the cursor's tree sha, or None if snapshotting fails."""
        cursor = self._load_cursor()
        if cursor is not None and cursor.get("tree"):
            return cursor["tree"]
        try:
            tree = store.snapshot_tree()
        except WorkspaceCheckpointError:
            return None
        self._write_cursor(tree, self.activity.last_seq())
        return tree

    def _rel_to_canonical(self, rel: str) -> str:
        return str(self._resolve(self.cwd / rel))

    def current_tree(self) -> str | None:
        """Snapshot and return the current whole-tree sha (no recording). None
        when the tracker is unavailable."""
        store = self._tracker_store()
        if store is None:
            return None
        self._ensure_cursor(store)
        try:
            return store.snapshot_tree()
        except WorkspaceCheckpointError:
            return None

    def begin_tree(self) -> str | None:
        """Baseline the current tree for a bracketed mutation and return its sha.
        Seeds the cursor on first use. Pass the returned sha to
        ``record_tree_change`` after the mutation."""
        return self.current_tree()

    def _content_maps(self, store: WorkspaceCheckpointStore, old_tree: str,
                      new_tree: str, rels: list[str]
                      ) -> tuple[dict[str, str | None], dict[str, str | None]]:
        """Before/after content-hash maps (canonical-keyed) for ``rels`` across
        two trees. Best-effort: unreadable blobs are simply omitted."""
        before: dict[str, str | None] = {}
        after: dict[str, str | None] = {}
        try:
            b = store.blob_content_hashes(old_tree, rels)
            a = store.blob_content_hashes(new_tree, rels)
        except WorkspaceCheckpointError:
            return before, after
        for rel in rels:
            key = self._rel_to_canonical(rel)
            before[key] = b.get(rel)
            after[key] = a.get(rel)
        return before, after

    def reconcile(self) -> WorkspaceActivity | None:
        """Durably fold any out-of-band tree changes into the activity log.

        Snapshot the current tree; if it differs from the cursor, diff the two,
        append exactly one ``external`` activity carrying the canonical changed
        paths and before/after content hashes, and advance the cursor to the new
        tree atomically. Returns the appended record, or None when nothing
        changed (no duplicate record) or the tracker is unavailable."""
        store = self._tracker_store()
        if store is None:
            return None
        old_tree = self._ensure_cursor(store)
        try:
            current = store.snapshot_tree()
        except WorkspaceCheckpointError:
            return None
        if old_tree is None or current == old_tree:
            return None
        changes = self._diff(store, old_tree, current)
        if not changes:
            self._write_cursor(current, self.activity.last_seq())
            return None
        rels = self._unique_rels(changes)
        before, after = self._content_maps(store, old_tree, current, rels)
        rec = self.activity.append(
            origin="external", action="external",
            paths=[self._rel_to_canonical(r) for r in rels],
            before=before, after=after, note="out-of-band tree change")
        # Refresh disk-backed after-hashes into no session's baseline: external
        # edits belong to no session, so baselines are intentionally untouched.
        self._write_cursor(current, rec.seq)
        return rec

    def advance_cursor(self) -> None:
        """Move the cursor to the current tree without recording anything. Call
        after a direct controlled change (record_controlled_change) so a later
        reconcile does not relabel that already-attributed edit as external."""
        store = self._tracker_store()
        if store is None:
            return
        self._ensure_cursor(store)
        try:
            current = store.snapshot_tree()
        except WorkspaceCheckpointError:
            return
        self._write_cursor(current, self.activity.last_seq())

    def record_tree_change(self, before_tree: str | None, *, origin: ActivityOrigin,
                           action: str, session_id: str | None = None,
                           call_id: str | None = None, note: str | None = None
                           ) -> WorkspaceActivity | None:
        """Attribute a bracketed whole-tree mutation to a session/call.

        Snapshot the after-tree, diff it against ``before_tree``, and — when any
        file changed (even if the command failed) — append one attributed
        activity with canonical changed paths and before/after content hashes,
        refresh the session's baselines for those paths, and advance the cursor
        so a later reconcile does not re-record the same change as external.
        Returns None (recording nothing) when nothing changed."""
        store = self._tracker_store()
        if store is None or before_tree is None:
            return None
        try:
            current = store.snapshot_tree()
        except WorkspaceCheckpointError:
            return None
        if current == before_tree:
            # Nothing changed on disk: keep the cursor consistent and record
            # nothing (callers that want a marker do so separately).
            self._write_cursor(current, self.activity.last_seq())
            return None
        changes = self._diff(store, before_tree, current)
        if not changes:
            self._write_cursor(current, self.activity.last_seq())
            return None
        rels = self._unique_rels(changes)
        before, after = self._content_maps(store, before_tree, current, rels)
        # Refresh this session's baselines to the after-content for each path so
        # its own bracketed change is not later seen as stale drift.
        with self._baselines_lock:
            for key, h in after.items():
                self._baselines[(session_id, key)] = h
        rec = self.activity.append(
            origin=origin, action=action,
            paths=[self._rel_to_canonical(r) for r in rels],
            session_id=session_id, call_id=call_id, before=before, after=after,
            note=note)
        self._write_cursor(current, rec.seq)
        return rec

    @staticmethod
    def _diff(store: WorkspaceCheckpointStore, old_tree: str,
              new_tree: str) -> list[tuple[str, str]]:
        try:
            return store.diff_trees(old_tree, new_tree)
        except WorkspaceCheckpointError:
            return []

    @staticmethod
    def _unique_rels(changes: list[tuple[str, str]]) -> list[str]:
        rels: list[str] = []
        for _status, rel in changes:
            if rel and rel not in rels:
                rels.append(rel)
        return rels

    # -- provenance queries -------------------------------------------------
    def latest_activity_for(self, path: Path) -> WorkspaceActivity | None:
        """The most recent recorded activity that touched ``path`` (compared by
        canonical path), or None if nothing on record touched it."""
        target = str(self._resolve(path))
        for rec in reversed(self.activity.read()):
            if target in rec.paths:
                return rec
        return None

    def describe_author(self, path: Path) -> str | None:
        """Human-readable author of the latest recorded change to ``path``:
        ``session <id>`` when attributable, otherwise the origin (e.g.
        ``external``). None when nothing is on record."""
        rec = self.latest_activity_for(path)
        if rec is None:
            return None
        if rec.session_id:
            return f"session {rec.session_id}"
        return rec.origin

    # -- stale-write guard --------------------------------------------------
    def detect_stale(self, session_id: str | None, path: Path) -> str | None:
        """Guard against overwriting content this session never re-read.

        If ``session_id`` has observed ``path`` and the current on-disk content
        differs from that baseline, return a human-readable conflict message.
        Whole-tree ``reconcile`` is run first so any out-of-band drift is folded
        into the activity log as one ``external`` record; the message can then
        name the true author. Returns None when the write may proceed: either the
        path was never observed (no baseline, allowed for compatibility) or it is
        unchanged since observed.

        Call under the workspace lock so the reconcile, current-hash check, and
        any external record are consistent with the impending write.
        """
        if not self.has_baseline(session_id, path):
            return None
        # Fold any external tree activity into the log before comparing, so the
        # provenance for this path is up to date. Reconciliation owns external
        # attribution and dedupes via the cursor; it is a no-op when the tracker
        # is unavailable, so we keep a direct-record fallback below.
        self.reconcile()
        baseline = self.baseline(session_id, path)
        current = self.current_hash(path)
        if current == baseline:
            return None
        key = str(self._resolve(path))
        rec = self.latest_activity_for(path)
        explained = rec is not None and rec.after.get(key) == current
        if not explained:
            # The tracker did not (or could not) attribute this drift. Record it
            # directly as external so the provenance is never lost. Skip when the
            # latest record is already an identical external observation.
            already_external = (
                rec is not None and rec.origin == "external"
                and rec.after.get(key) == current)
            if not already_external:
                self.record_external_change(
                    action="observed_modified", paths=[path])
        author = self.describe_author(path)
        who = f" (last modified by {author})" if author else ""
        return (f"{key} changed on disk since this session last read it"
                f"{who}. Re-read the file and reapply your change; the file was "
                "left unmodified.")


class WorkspaceRegistry:
    """Maps each resolved cwd to its single SharedWorkspace. Canonicalizes with
    ``Path.resolve()`` so symlink aliases and equivalent paths share one object
    (and therefore one lock). One registry per Forge home."""

    def __init__(self, home: Path):
        self.home = home
        self._workspaces: dict[str, SharedWorkspace] = {}

    def _canonical(self, cwd: str | Path) -> Path:
        return Path(cwd).resolve()

    def get(self, cwd: str | Path) -> SharedWorkspace:
        resolved = self._canonical(cwd)
        key = str(resolved)
        ws = self._workspaces.get(key)
        if ws is None:
            ws = SharedWorkspace(resolved, WorkspaceActivityStore(self.home, resolved))
            self._workspaces[key] = ws
        return ws
