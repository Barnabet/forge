# Conversation rewind and workspace restoration

Forge can rewind a session to a checkpointed user message or edit that message and continue from the restored point.

## User-visible behavior

- **Edit from here** restores the workspace to the state captured immediately before the selected message, removes that message and all later conversational events from the active branch, posts the edited replacement, and starts a new run.
- **Rewind here** performs the same history and workspace restoration without posting a replacement.
- The event log remains append-only. A durable `history_rewound` marker defines which earlier events are no longer part of the active branch, so reconnecting clients and WebSocket subscribers converge without sequence numbers moving backward.
- Messages from sessions created before checkpoint support remain readable, but cannot restore code state and therefore have rewind actions disabled.

## Workspace checkpoints

Each accepted user message records a checkpoint ID for the project tree as it stands before that message's run begins. The message bubble is published first and the snapshot runs off the event loop; a follow-up `message_checkpointed` event attaches the checkpoint to the message once capture completes, so the UI never waits on git. Checkpoints use a private shadow Git object store at:

```text
$FORGE_HOME/sessions/<session-id>/workspace.git
```

This store does not use or modify the project's `.git`, index, branch, or commits. Git objects naturally deduplicate identical file content and trees.

Checkpoints include regular files, binary files, creates, deletes, renames, and symlinks. They therefore cover mutations made through file tools, shell commands, filesystem UI actions, and write-mode subagents.

The following are intentionally outside checkpoint scope and remain untouched by restoration:

- the project's `.git` directory;
- Forge's own session data when it resides below the workspace;
- ignored files and directories from the project's Git ignore rules;
- dependency, cache, and build directories such as `node_modules`, `.venv`, `dist`, `build`, `target`, and `.cache`.

Forge-controlled mutations and checkpoint operations share a per-session lock. External processes writing directly to the workspace are not transactionally controlled by Forge.

## Restore safety and crash recovery

Before restoring an old checkpoint, Forge captures the current tree as a safety checkpoint. A durable `rewind-intent.json` journal is written before the destructive restore.

On normal success, Forge appends the rewind marker, appends the replacement message when editing, and then removes the intent journal. On startup after an interruption:

- if the marker was not persisted, Forge restores the safety checkpoint and keeps the old history branch active;
- if the marker persisted, Forge restores the target checkpoint and completes a missing replacement message before normal interrupted-run recovery.

If checkpoint capture or restoration fails, the API rejects the rewind and retains a coherent history/workspace state. Recovery failures keep the intent file for diagnosis rather than silently deleting evidence.

## Storage and retention

Checkpoint Git objects and checkpoint metadata are retained for the lifetime of the session. Deleting a session removes its event log, changesets, checkpoint object store, and recovery journal. There is currently no automatic checkpoint garbage collection or named-branch browser.

## Operational recovery

If a session cannot recover automatically:

1. Stop Forge so no process mutates the workspace.
2. Preserve `$FORGE_HOME/sessions/<session-id>/rewind-intent.json`, `events.jsonl`, and `workspace.git` for diagnosis.
3. Inspect the intent's `safety_checkpoint` and `target_checkpoint` IDs in `workspace.git/checkpoints.jsonl`.
4. Restart Forge after correcting missing/corrupt storage. The intent remains present until recovery succeeds.

Do not point ordinary Git commands at the project's repository to manipulate Forge checkpoints; they live in the separate shadow object store.
