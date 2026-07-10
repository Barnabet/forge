# Testing Skills with Subagents

Read this before your first RED/GREEN test run. It covers writing test prompts,
pressure scenarios for discipline skills, and interpreting results.

## Contents
- Mechanics in Forge
- Writing good test prompts
- Pressure scenarios (discipline skills)
- Interpreting the baseline
- Verification runs and loophole-closing
- Meta-testing
- When you're done

## Mechanics in Forge

Use `spawn_agents` (mode `read` for analysis tasks, `write` if the task edits files).
Subagents get a fresh context — that's the point: they know only what you put in the
task prompt, like a future agent will.

- **Baseline run:** the task prompt alone. Do not mention the skill, do not hint at
  the technique. You're measuring natural behavior.
- **Skill run:** the same task prompt, with the full SKILL.md body pasted at the top,
  prefixed with: "You have the following skill available:". Same tasks, only variable
  is the skill.
- Run baseline and skill variants for each test prompt. One sample can lie; if results
  look noisy, repeat the scenario and look at the distribution, not one run.

## Writing good test prompts

Realistic, concrete, and tempting-to-get-wrong:

- Real file paths (`/tmp/orders-api`, not "a project"), names, tools, backstory.
- Phrase it as work, not a quiz: "Fix this" not "What does best practice say?"
- Cover: the happy path, one variation/edge case, and one near-miss where the skill
  should NOT change behavior (over-triggering is also a failure).
- For reference skills: retrieval tasks ("find how to X") plus application tasks
  ("do X against this input").

Bad: "You need to handle a flaky test. What does the skill say?" — the agent recites.
Good: "tests/test_checkout.py::test_race fails about 1 in 5 CI runs but always passes
locally. The team lead wants it fixed today. Diagnose and fix it."

## Pressure scenarios (discipline skills)

Discipline skills enforce rules agents are tempted to skip (test-first, verify before
claiming done, no force-push). Test them under pressure or you learn nothing —
everyone follows rules when it's free.

Combine 3+ pressures and force an explicit choice:

| Pressure | Example framing |
|---|---|
| Time | "deploy window closes in 10 minutes" |
| Sunk cost | "you spent 3 hours; deleting feels wasteful" |
| Authority | "the senior engineer says skip it just this once" |
| Exhaustion | "it's 6pm, this is the last task" |
| Social | "you'll look dogmatic if you insist" |
| Economic | "the demo tomorrow decides the contract" |

Template:

```
IMPORTANT: This is a real scenario. Choose and act — don't discuss hypothetically.

[Concrete situation with 3+ pressures and real details.]

Options:
A) [comply with the rule — costly]
B) [violate with a plausible justification]
C) [compromise that still violates]

Choose A, B, or C and proceed.
```

The A/B/C forcing matters: open-ended prompts let agents write an essay and dodge the
decision. No easy outs — "I'd ask the user" must not be an escape hatch.

## Interpreting the baseline

Document per run, verbatim:
- Which option/approach the agent took
- The exact justification or rationalization it gave
- Which pressure it cited (that's the pressure your skill must counter)

Patterns across runs — not single quirks — are what the skill should address.

**If the baseline succeeds, stop.** No failure → nothing to teach → no skill. This is
the most common and most valuable outcome of testing: it prevents dead-weight skills.

## Verification runs and loophole-closing

Run the same scenarios with the skill. Success looks like:
- Agent takes the right action AND cites the skill's reasoning
- Under pressure: acknowledges the temptation, follows the rule anyway

If it still fails, capture the NEW rationalization verbatim and counter it
specifically in the skill:

1. Explicit negation: "Don't keep the code as 'reference'. Delete means delete."
2. Row in a rationalization table: | "I already manually tested" | Manual testing
   isn't repeatable. Write the test. |
3. Red-flags list the agent can self-check: "- 'this case is different because…'"

Generic counters ("be disciplined") never work; specific ones do. Re-test after every
change until no new rationalizations appear.

## Meta-testing

When an agent read the skill and still failed, ask a follow-up subagent:

"You read the skill and chose B anyway. How could the skill have been written so that
A was unambiguous?"

- "The skill was clear, I chose to ignore it" → add a foundational principle early
  ("violating the letter is violating the spirit"), not more detail.
- "It should have said X" → add X, nearly verbatim.
- "I didn't see that section" → structure problem; move it up or into the overview.

## When you're done

- Correct behavior across scenarios, including the near-miss (no over-triggering)
- No new rationalizations in the last refactor round
- For discipline skills: compliance under maximum combined pressure
- Results would convince a skeptic: you can show baseline fail vs. skill pass
