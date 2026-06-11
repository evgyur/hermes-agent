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
4. Do not re-run final audit, deploy smoke, build, or live probes unless the user explicitly asks for fresh verification.
5. Reply in a short stop format:
   - `Goal complete. Останавливаюсь.`
   - `STATE.md: Status COMPLETE / Current phase COMPLETE`
   - `final audit timestamp` and `release SHA` if already present in `STATE.md`
   - `следующей numbered phase нет`

## Why

Repeated re-verification creates noisy duplicate answers and can accidentally turn a completed production goal into an unbounded monitoring loop. Completion state on disk is the source of truth; once complete, the right next action is no-op unless the user gives a new goal or a concrete bug.
