# Shared live-workspace concurrency

Forge lets multiple sessions operate on the same project at once. This document
describes how those sessions coordinate on a single, live working tree — what is
shared, how mutations are serialized, how out-of-band changes are detected and
attributed, and how the stale-write, revert, and rewind guards behave.

## Model in one sentence

Every session whose working directory resolves to the same real path edits **the
same live files on disk** — there are no per-session branches, copies, or
worktrees — and all Forge-controlled mutations across those sessions are
serialized by **one shared in-process lock** keyed by the resolved cwd.

## One live tree per resolved cwd

- A `SharedWorkspace` (`server/forge/engine/workspace.py`) coordinates every
  `SessionActor` whose cwd resolves to the same real directory.
- `WorkspaceRegistry` canonicalizes each cwd with `Path.resolve()`, so symlink
  aliases and equivalent paths map to **one** `SharedWorkspace` object — and
  therefore one lock, one activity log, and one tree tracker. There is one
  registry per Forge home.
- The workspace hash is a stable, filesystem-safe SHA-256 of the resolved cwd
  (`workspace_hash`); two aliases that resolve to the same directory produce the
  same hash.

There is exactly one live working tree. Nothing is sandboxed or copied; edits by
any session are immediately visible to all others because they are the same
files.

## The shared mutation lock

- `SharedWorkspace.lock` is an `asyncio.Lock`. Each actor aliases it as
  `self.workspace_lock`, so all sessions on the same tree serialize together.
- The lock is held around **Forge-controlled mutations** so a checkpoint capture
  or restore never races a partially-applied mutation. Coordinated under it:
  - Mutating tools (e.g. `write_file`, `edit_file`) — held by the actor around
    the tool call.
  - Finite (run-to-completion) `bash` — the actor brackets it with a whole-tree
    snapshot before/after, all under the lock.
  - FS API writes (`/api/sessions/{sid}/fs/*`: mkdir, touch, move, delete,
    upload).
  - Changeset revert.
  - Message checkpoint capture and rewind restore.
- **Write subagents:** `spawn_agents` sets `manages_workspace_lock = True`. The
  actor does **not** wrap the whole tool call in the lock. Write workers may run
  concurrently—their model turns, reads, and grading overlap—but each mutating
  tool call acquires the shared lock. Finite bash calls are snapshot/diffed inside
  that same critical section. This serializes only actual mutations, not workers.
- Read-only tools do not take the lock.

The lock is **in-process**: it coordinates sessions inside one Forge server
process only. It does not coordinate separate Forge server processes, editors,
or arbitrary shells (see Limitations).

## Durable activity log

Provenance is recorded in an append-only JSONL log
(`server/forge/store/workspace_activity.py`):

```
FORGE_HOME/workspaces/<hash of resolved cwd>/activity.jsonl
```

- Appends are durable (write + flush + `fsync`, plus a directory `fsync` when the
  file is first created) and guarded by a thread lock, so concurrent in-process
  appends never interleave or race the monotonic `seq` counter.
- Each record is self-describing: `seq`, `timestamp`, `cwd`, `session_id`,
  `origin`, `action`, `paths`, `call_id`, and before/after content-hash maps
  keyed by canonical path (a value of `None` means the path was missing at that
  point).
- Malformed or missing logs load cleanly: a corrupt line is skipped; an absent
  file reads as empty.
- Sibling state lives in the same per-workspace directory: the tree tracker
  (`tree.git/`) and the tree cursor (`tree-cursor.json`).

### Origins (attribution)

`origin` records who made a change:

| origin       | meaning                                                        |
|--------------|----------------------------------------------------------------|
| `tool`       | a Forge tool mutation (write/edit)                             |
| `fs_api`     | a mutation via the REST FS API                                 |
| `bash`       | attributed to a finite bash command (bracketed snapshot diff)  |
| `terminal`   | marker that a session launched a persistent PTY terminal       |
| `subagent`   | a write subagent's mutation                                    |
| `external`   | a change observed to have happened outside Forge's control     |
| `checkpoint` | provenance marker for a captured user-message rewind point     |
| `rewind`     | provenance marker for a successful rewind restore              |
| `revert`     | provenance marker for a changeset revert                       |

Attribution is by `session_id` when known; otherwise the human-readable author
is the origin (e.g. `external`).

## Read baselines and stale-write refusal

- Per-session, per-path **baselines** record the last content hash a session
  observed for a path (keyed by `(session_id, canonical-path)`, guarded by a
  thread lock). A reader can register the exact bytes it returned via
  `observe_hash`, avoiding a TOCTOU re-read.
- Before a write, `detect_stale` runs (under the lock):
  1. If the session has no baseline for the path, the write proceeds (allowed for
     compatibility — a path the session never read).
  2. Otherwise it first `reconcile`s (folding any out-of-band drift into the log
     as one `external` record), then compares the on-disk hash to the baseline.
  3. If they differ, the write is **refused** with a message naming the true
     author, and the file is left unmodified. The agent must **re-read the file
     and reapply** its change. If the tracker could not attribute the drift, it
     is recorded directly as `external` so provenance is never lost.

## Whole-tree shadow tracker and cursor

The workspace maintains a **whole-tree shadow tracker**: a cwd-scoped shadow Git
object store under `FORGE_HOME/workspaces/<hash>/tree.git`, separate from any
session checkpoint store. It lets Forge snapshot the entire tree cheaply and diff
arbitrary edits (including ones made out-of-band).

- The **cursor** (`tree-cursor.json`) records the last tree sha (and activity
  seq) Forge has accounted for. It is written atomically and durably.
- On first use the tracker snapshots the current tree and seeds the cursor
  **without** recording anything external — the pre-existing project is not
  "external".
- The tracker respects the same hard excludes as checkpoints
  (`HARD_EXCLUDE_DIRS`: dependency/build/cache dirs, `.git`, Forge's own
  `.forge` dir, etc.) plus project `.gitignore` rules via git's normal ignore
  handling. **The project's own `.git` repository is never touched**; the shadow
  store is entirely separate.

### Detecting external / bash / subagent changes

- **`reconcile`** snapshots the current tree; if it differs from the cursor it
  diffs the two, appends exactly **one** `external` activity carrying the
  canonical changed paths and before/after content hashes, and advances the
  cursor — atomically. It returns `None` (recording nothing) when nothing
  changed or the tracker is unavailable. It runs at reconcile points: before a
  stale-write check, before a checkpoint capture, before a revert's conflict
  check, before the rewind gate, and in the workspace-status endpoint.
- **`record_controlled_change`** (tools, FS API, revert) records the direct edit
  and advances the cursor so a later reconcile does not relabel that already
  attributed edit as `external`.
- **`begin_tree` / `record_tree_change`** bracket opaque mutations (finite bash,
  including subagent bash calls, and rewind restore): snapshot before, run,
  snapshot after, diff, and attribute any
  changed paths to the session/call — recorded on every exit (success, nonzero,
  timeout, exception, cancellation) so a mutation is never lost.
- **Persistent terminals** launched by the agent record a no-path `terminal`
  marker only; the terminal writes asynchronously and outlives the run, so any
  files it mutates surface as `external` on a future reconcile.
- If git is unavailable the tree APIs degrade to no-ops rather than failing
  tools; the stale-write guard keeps a direct `external`-record fallback.

## Changeset revert conflict behavior

`ChangesetStore.revert` restores the recorded *before*-content, but **refuses**
to clobber content that changed since the changeset was applied: it compares the
file's current hash against the changeset's recorded *after*-hash and raises
`RevertConflict` (surfaced as HTTP 409) when they differ, leaving disk and
status untouched. The endpoint reconciles first so an external mutation is
recorded before a conflict is raised rather than lost; a successful revert is
recorded as a `revert` activity.

## Rewind boundary and conflict behavior

Rewind returns the tree to a prior user-message checkpoint. The gate runs under
the lock, after `reconcile`, and **before any destructive step**, so a refused
rewind leaves the session, tree, and terminals untouched.

- **Restore-touched paths** are every path that differs between the target tree
  and the live tree. Because the restore reinstates the *exact* target tree, it
  also **deletes files added since the target** — including a foreign session's
  newly-added file — so such additions are restore-touched and can block.
- The gate scans activity recorded after the target message's activity boundary
  (a conservative whole-log scan when the boundary is unknown/legacy). A
  restore-touched path **conflicts** when a later record that touched it is
  `external` or attributed to **another session**. No-path markers
  (checkpoint/terminal launches) and the rewinding session's **own** later
  changes are ignored — its own changes are simply rewound.
- On conflict, `RewindConflict` is raised (the paths and authors are reported)
  and nothing is changed.
- If the gate passes: live terminals in the session are closed first (a rewind
  rewrites the tree out from under running processes; V1 closes **all** live
  terminals in the session, since terminals are session-scoped), a safety
  checkpoint and a durable rewind-intent are captured, then the restore runs and
  is attributed as a `rewind` activity. Note that an exact restore **may delete
  the session's own later additions** that are not part of the target tree.

Crash recovery: the rewind-intent is written before the first destructive
restore. On restart `recover_rewind` replays it — if the `history_rewound`
marker never landed it restores the safety checkpoint (log still describes the
old branch); if the marker landed it ensures the target checkpoint is restored
and finishes any replacement message. If checkpoint restore fails during
recovery the intent is retained as evidence for a later attempt or an operator.

## Agent awareness and UI surfacing

- **System prompt:** when peers or recent foreign/external changes exist, the
  prompt gains a "Shared workspace" section telling the agent other sessions edit
  the exact same live files, to re-read a file immediately before overwriting it,
  and that a stale write or a rewind over another author's change may be refused.
  It lists peer sessions (id, status, mode) and recent foreign/external changes.
  This summary is computed **without** taking the lock or reconciling — it only
  reads resident meta and the durable log — and deliberately excludes the current
  session's own routine activity.
- **TopBar workspace pill** (`web/src/components/WorkspaceStatus.tsx`): shows
  "N sessions" and a foreign-change count, flags external changes, and opens a
  panel listing peer sessions and recent foreign activity. It hides entirely when
  the session is solo with no foreign activity. It polls the status endpoint.

### Status endpoint

`GET /api/sessions/{sid}/workspace/status?limit=<1..100>` returns (under the
lock, after a `reconcile`):

- `cwd` — the resolved workspace directory
- `sessions` — status rows for every session sharing the cwd (id, name, status,
  mode, archived, last_message_at, busy)
- `recent_activity` — recent activity records (seq, timestamp, session_id,
  author, origin, action, relativized paths, note)
- `current_tree` — current whole-tree sha (or null when the tracker is
  unavailable)
- `reconciled` — whether this call folded in an out-of-band change
- `last_external_paths` — paths of the most recent external change

## Limitations

- **External changes are only noticed at reconcile points** (before a
  stale-write check, checkpoint capture, revert, the rewind gate, and on the
  status endpoint) — not continuously. Between reconcile points, out-of-band
  edits are invisible to Forge.
- **Persistent-terminal mutations are labeled `external`.** A PTY terminal
  writes asynchronously and outlives the run, so its file changes are not
  attributed to the launching session; they surface as `external` on a later
  reconcile. Only a no-path `terminal` launch marker is attributed.
- **In-process locks do not coordinate separate Forge server processes.** Two
  Forge servers (or an external editor / arbitrary shell) pointed at the same
  tree are not serialized by the lock; their edits are only caught as `external`
  drift on the next reconcile.
- **Ignored / hard-excluded files are not tracked.** Paths under
  `HARD_EXCLUDE_DIRS` or matched by project `.gitignore` do not appear in the
  tree tracker, so changes to them are neither attributed nor detected.
- **Activity baselines reset in memory on restart.** Per-session, per-path read
  baselines are in-memory only; after a restart a session has no baselines until
  it re-reads files, so the stale-write guard allows the first write to a path it
  has not re-read this process. The durable activity log and the tree cursor
  persist across restarts, so external-change detection and provenance survive.
- **Tracker unavailability:** if git is unavailable the whole-tree features
  degrade to no-ops; the stale-write guard still functions via its direct
  `external`-record fallback, but bracketed bash/rewind attribution and
  reconcile-based external detection are disabled.

## Operator troubleshooting

- **"changed on disk since this session last read it" write refusals** — the file
  was modified since the session read it (by another session, an external
  process, or a terminal). The agent must re-read and reapply. Check
  `activity.jsonl` for the attributing record; the author names the culprit.
- **Revert returns 409 (`RevertConflict`)** — the file no longer matches the
  content the changeset wrote. Inspect current content vs. the changeset before
  reverting manually.
- **Rewind refused (`RewindConflict`)** — a restore-touched path was later
  changed by another session or an external process. The reported paths/authors
  show what would be clobbered. Resolve or coordinate before retrying.
- **Missing/duplicate external attribution** — confirm git is available (the
  tree tracker needs it) and that the changed paths are not hard-excluded or
  gitignored.
- **Two Forge servers on one tree** — the in-process lock cannot serialize them;
  prefer a single server per working tree.

### Relevant paths and endpoints

| what | where |
|------|-------|
| Coordinator / lock / baselines / tracker | `server/forge/engine/workspace.py` |
| Durable activity log | `server/forge/store/workspace_activity.py` |
| Activity log file | `FORGE_HOME/workspaces/<hash>/activity.jsonl` |
| Tree tracker (shadow git) | `FORGE_HOME/workspaces/<hash>/tree.git/` |
| Tree cursor | `FORGE_HOME/workspaces/<hash>/tree-cursor.json` |
| Changesets / revert | `server/forge/store/changesets.py` |
| Rewind gate / restore | `server/forge/engine/actor.py` |
| Prompt summary | `server/forge/engine/sysprompt.py` |
| Status endpoint | `GET /api/sessions/{sid}/workspace/status` |
| Revert endpoint | `POST /api/sessions/{sid}/changesets/{index}/revert` |
| FS write endpoints | `POST /api/sessions/{sid}/fs/{mkdir,touch,move,delete,upload}` |
| TopBar pill | `web/src/components/WorkspaceStatus.tsx` |
