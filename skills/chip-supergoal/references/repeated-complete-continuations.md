# Repeated complete standing-goal continuations

Use when a host-generated standing-goal continuation arrives after a SuperGoal is already complete.

## Problem

After `AUDIT_COMPLETE` / `SUPERGOAL_RUN_COMPLETE`, some clients may still send repeated messages like:

```text
[Continuing toward your standing goal]
Continue working toward this goal. Take the next concrete step.
```

If `STATE.md` already says `Status: COMPLETE` and `Current phase: COMPLETE`, these are stale evaluator continuations, not instructions to re-open the run.

## Correct behavior

1. Read `STATE.md` if not already loaded in the current turn.
2. If status/current phase are complete, stop the loop.
3. Do not run another numbered phase.
4. Do not re-run final audit, deploy smoke, build, or live probes unless Chip explicitly asks for fresh verification.
5. On the **first** post-complete continuation, it is acceptable to cite the final audit timestamp / counts from the saved report if already known.
6. On **repeated identical** post-complete continuations in the same chat, do **not** keep replying with status summaries. Treat them as stale evaluator noise.
7. If the runtime can truly suppress delivery, no-op silently. If the runtime still requires a visible final message, do **not** return an empty string — empty finals render as confusing `(empty)` messages. Use the shortest explicit marker instead: `COMPLETE — no-op.`
8. Only answer substantively again if Chip adds a new human question, asks for fresh verification, reports a bug, or provides a new goal. Then answer that new request, not the stale continuation wrapper.
9. Reply format if a visible answer is needed:
   - first repeat: `COMPLETE — SuperGoal уже завершён. STATE.md: Status COMPLETE / Current phase COMPLETE. Следующей numbered phase нет.`
   - later repeated wrapper with no new human content, if suppression is impossible: `COMPLETE — no-op.`

## Why

Repeated re-verification creates noisy duplicate answers and can accidentally turn a completed production goal into an unbounded monitoring loop. Completion state on disk is the source of truth; once complete, the right next action is no-op unless the user gives a new goal or a concrete bug.
