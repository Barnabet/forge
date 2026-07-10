from __future__ import annotations

import platform
from datetime import date
from pathlib import Path

from forge.engine.skills import discover_skills

GUIDELINES = """\
## Guidelines
- Prefer the dedicated tools (read_file, edit_file, glob, grep) over bash equivalents.
- Verify your work: run tests or re-read files after changing them.
- Be concise in prose; the user sees your text between tool calls.
- When a task teaches you a durable fact about the user or a project, save it to memory."""

MEMORY_HOWTO = """\
## Memory
Your persistent memory lives at {mem_dir}. The index below is loaded every session.
Save one durable fact per markdown file in that directory and keep {mem_dir}/MEMORY.md
updated with one line per fact. Update or delete stale facts rather than duplicating.

### Memory index
{index}"""


def _read(path: Path) -> str:
    return path.read_text().strip() if path.is_file() else ""


def build_system_prompt(meta, home: Path) -> str:
    cwd = Path(meta.cwd)
    parts = [
        "You are Forge, a capable local agent operating on the user's machine "
        "with shell and file access.",
        f"## Environment\nOS: {platform.system()} · cwd: {cwd} · "
        f"date: {date.today().isoformat()} · model: {meta.model}",
    ]
    if g := _read(home / "FORGE.md"):
        parts.append("## Global instructions\n" + g)
    if p := (_read(cwd / "FORGE.md") or _read(cwd / "AGENTS.md")):
        parts.append("## Project instructions\n" + p)
    mem_dir = home / "memory"
    parts.append(MEMORY_HOWTO.format(
        mem_dir=mem_dir, index=_read(mem_dir / "MEMORY.md") or "(empty)"))
    skills = discover_skills([home / "skills", cwd / ".forge" / "skills"])
    if skills:
        lines = "\n".join(f"- {s.name} — {s.description}" for s in skills)
        parts.append("## Skills\nCall load_skill(name) before tasks a skill covers.\n"
                     + lines)
    parts.append(GUIDELINES)
    return "\n\n".join(parts)
