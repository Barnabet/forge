from __future__ import annotations

from forge.engine.events import Event

SUMMARY_PREFIX = "[Summary of the conversation so far]\n"


def to_messages(events: list[Event], system_prompt: str) -> list[dict]:
    summary, cut = None, 0
    for e in events:
        if e.type == "context_compacted":
            summary, cut = e.summary, e.upto_seq

    msgs: list[dict] = [{"role": "system", "content": system_prompt}]
    if summary is not None:
        msgs.append({"role": "user", "content": SUMMARY_PREFIX + summary})

    pending_users: list[str] = []
    open_calls = 0
    for e in events:
        if e.seq <= cut:
            continue
        if e.type == "user_message":
            if open_calls:
                pending_users.append(e.text)
            else:
                msgs.append({"role": "user", "content": e.text})
        elif e.type == "assistant_message":
            m: dict = {"role": "assistant", "content": e.text or None}
            if e.tool_calls:
                m["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": c.arguments}}
                    for c in e.tool_calls
                ]
                open_calls = len(e.tool_calls)
            msgs.append(m)
        elif e.type == "tool_call_finished":
            msgs.append({"role": "tool", "tool_call_id": e.call_id, "content": e.output})
            open_calls = max(0, open_calls - 1)
            if open_calls == 0 and pending_users:
                msgs += [{"role": "user", "content": t} for t in pending_users]
                pending_users.clear()
    msgs += [{"role": "user", "content": t} for t in pending_users]
    return msgs


def dangling_call_ids(events: list[Event]) -> list[tuple[str, str]]:
    """(call_id, tool) for assistant tool calls that never got a result."""
    finished = {e.call_id for e in events if e.type == "tool_call_finished"}
    out = []
    for e in events:
        if e.type == "assistant_message":
            out += [(c.id, c.name) for c in e.tool_calls if c.id not in finished]
    return [(cid, tool) for cid, tool in out if cid not in finished]
