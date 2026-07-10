---
name: creating-skills
description: Use when creating a new skill, editing an existing skill, or when a hard-won technique should be captured for future sessions — including when a workflow took several attempts to get right, when the user says "remember how to do this" or "make a skill", or when you notice you would benefit from a skill that doesn't exist yet.
---

# Creating Skills

## Overview

A skill is a reference guide that teaches a future agent a proven technique. The core
principle: **a skill is only as good as its behavior change** — if an agent behaves the
same with and without it, it's dead weight. So skills are written like code is written:
test first (baseline the failure), write minimally (address that failure), verify
(watch the agent comply), refactor (close loopholes).

Skills are reusable techniques, patterns, and references. Skills are NOT narratives
about how you solved a problem once, and NOT project conventions (those go in the
project's FORGE.md / AGENTS.md).

## How skills work in Forge

1. At session start, every skill's `name` + `description` is indexed into the system
   prompt. Nothing else is loaded.
2. An agent calls `load_skill(name)` when the description matches its task. That loads
   the SKILL.md body and lists bundled files.
3. Bundled files are read with `read_file` (or executed with `bash`) only when needed.

This three-level loading means: the description is the only thing every agent always
sees; the body costs context only when triggered; bundled files are free until read.
Budget accordingly — description is precious, body should be lean (<500 lines), bundled
files can be exhaustive.

**Locations:** `~/.forge/skills/<name>/SKILL.md` (global) or
`<cwd>/.forge/skills/<name>/SKILL.md` (project-specific; wins on name collision).
Broadly-applicable skills go global; skills tied to one codebase go in the project.

## When to create a skill

Create when ALL of these hold:
- The technique wasn't intuitively obvious (you or a baseline agent got it wrong first)
- It will recur across tasks or projects
- It's a judgment call or multi-step process — not something a linter/script could enforce

Don't create for: one-off solutions, things any competent agent already knows,
project-specific conventions (FORGE.md), or mechanical rules (automate those instead).

## Skill types — and how to test each

| Type | Example | Test by |
|---|---|---|
| Technique | step-by-step method | Can a fresh agent apply it to a new case? |
| Pattern | a way to think about problems | Does the agent recognize when it applies — and when NOT? |
| Reference | API/tool documentation | Can the agent find and correctly use the info? |
| Discipline | a rule agents want to skip under pressure | Does the agent comply under combined pressure? |

## Writing the frontmatter

`name`: lowercase letters, numbers, hyphens. Prefer gerund verb-first form describing
the action: `creating-skills`, `debugging-migrations`, `condition-based-waiting`.
Never vague (`helper`, `utils`).

`description`: the single highest-leverage line in the skill. Rules:
- Third person, starts with what it's for, then "Use when …" with concrete triggers:
  symptoms, error messages, situations, user phrases. Keep under ~500 chars.
- Include the words a future agent would think of: error strings, tool names, synonyms
  ("flaky/hanging/timeout").
- Be a little pushy — agents undertrigger skills. List adjacent situations where the
  skill should still fire.
- **Never summarize the skill's workflow in the description.** Agents will follow the
  summary instead of loading the body, and the summary is always lossier. Triggers
  only.

```yaml
# BAD — vague:            description: Helps with async tests
# BAD — workflow summary: description: Use for TDD — write test first, watch it fail, then code
# GOOD — triggers only:   description: Use when tests have race conditions, timing
#   dependencies, or pass/fail inconsistently — including setTimeout/sleep in tests,
#   "flaky" CI failures, or tests that pass alone but fail in a suite.
```

## Writing the body

Structure that works (adapt, don't cargo-cult):

```markdown
# Skill Name
## Overview          — what + core principle in 1–2 sentences
## When to use       — symptoms, use cases, when NOT to use
## Core pattern      — before/after comparison or the key recipe
## Quick reference   — table/bullets for scanning
## Implementation    — inline code (<50 lines) or pointer to bundled file
## Common mistakes   — what goes wrong + fixes
```

Principles:
- **Assume the agent is smart.** Only add what it doesn't already know. Challenge every
  paragraph: "does this justify its token cost?" Cut explanations of common knowledge.
- **Explain why, not just what.** Reasoned instructions generalize; bare commands get
  rationalized away. If you find yourself writing ALWAYS/NEVER in caps, first try
  explaining the reason instead.
- **Match freedom to fragility.** Many valid approaches → give heuristics (high
  freedom). One safe sequence, errors are costly → give the exact commands and say not
  to deviate (low freedom).
- **One excellent example beats five mediocre ones.** Real, complete, commented for
  why. Don't port it to five languages.
- **Prefer imperative voice.** "Run X, then Y" not "you might consider running X".
- **Match the form to the failure** (from baseline testing):
  - Agent skips a rule under pressure → prohibition + rationalization table + red flags
  - Output has the wrong shape → positive recipe stating what the output IS (prohibition
    lists backfire here)
  - Agent omits an element → REQUIRED slot in a template it fills in
  - Behavior depends on a condition → conditional keyed to an observable predicate
  - No nuance clauses ("don't X unless it matters" reopens the negotiation)

## Bundled files

Keep SKILL.md self-contained unless there's a reason to split:
- **Heavy reference** (100+ lines of API docs, schemas) → own `.md` file, with a table
  of contents at the top, linked from SKILL.md with a note on when to read it
- **Reusable scripts** → `scripts/` — executed via bash, they never cost context.
  Scripts must handle their own errors (create missing files, print actionable
  messages) rather than punting failures back to the agent. No magic constants.
- References one level deep only: SKILL.md → file. Never SKILL.md → file → file.
- Forward-slash paths, descriptive filenames (`form-validation-rules.md`, not `doc2.md`).

## Testing: baseline first, always

**The iron law: no skill without a failed baseline.** If you never watched an agent
fail without the skill, you don't know the skill teaches the right thing — you only
know what YOU think needs preventing. This applies to edits too.

The cycle, using `spawn_agents`:

1. **RED — baseline.** Write 2–3 realistic task prompts that should exercise the skill.
   Spawn subagents WITHOUT the skill (don't mention it). Document exactly how they
   fail — wrong choices, missing steps, and for discipline skills the rationalizations
   verbatim.
   - If the baseline doesn't fail, STOP. There is nothing to teach; don't write the skill.
2. **GREEN — write minimally.** Address the specific observed failures, nothing
   hypothetical. Spawn fresh subagents WITH the skill body included in the task prompt.
   Verify they now succeed.
3. **REFACTOR — close loopholes.** New failure or rationalization? Add a specific
   counter (not "don't cheat" — "don't keep it as reference; delete means delete").
   Re-test until stable.
4. If subagents in different test runs each rebuilt the same helper script, that
   script belongs in the skill's `scripts/`.

Realistic test prompts are concrete — file paths, backstory, casual phrasing — not
"apply the skill". For discipline skills, combine 3+ pressures (deadline + sunk cost +
authority) and force an explicit A/B/C choice. Full methodology, pressure catalog, and
worked example: read `testing-skills.md` in this directory before your first test run.

## Common mistakes

| Mistake | Fix |
|---|---|
| Written from memory of one incident, narrative style | Extract the general technique; drop the story |
| Description summarizes the process | Triggers only; process lives in the body |
| Skill restates what agents already do fine | Delete it — baseline passed means no skill |
| Verbose body "for completeness" | Every token competes with the actual task context |
| Multiple examples of the same pattern | Keep the best one |
| Batch-creating skills without testing each | One skill, tested, then the next |
| Editing a live skill without re-testing | Edits are code changes; baseline the failure the edit addresses |

## Checklist

Before considering a skill done:

- [ ] Baseline run WITHOUT the skill failed, failures documented
- [ ] name: lowercase-hyphens, verb-first, specific
- [ ] description: third person, concrete triggers + symptoms, no workflow summary
- [ ] Body lean, explains why, one great example, form matches the failure type
- [ ] Bundled files only for heavy reference or reusable scripts, one level deep
- [ ] Verification run WITH the skill passed
- [ ] Loopholes from testing closed with specific counters
- [ ] Placed in the right scope (global `~/.forge/skills` vs project `.forge/skills`)
