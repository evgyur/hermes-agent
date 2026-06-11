# Supergoal → /goal pipeline: turn-yield discipline

## Durable lesson

A Supergoal run must not execute multiple numbered phases inside one assistant turn. Long production runs can otherwise hit the tool-call/context limit after several phases are green but before deploy, final audit, or `SUPERGOAL_RUN_COMPLETE`.

The `/goal` evaluator is the continuation mechanism. Treat it as the scheduler:

1. Run exactly one numbered phase.
2. Print `SUPERGOAL_PHASE_VERIFY` with evidence.
3. Print `SUPERGOAL_PHASE_DONE`.
4. Update `STATE.md` immediately.
5. Print `SUPERGOAL_TURN_YIELD` with the next state (`phase N+1` or `AUDIT`).
6. Stop the assistant turn.
7. Let the next `/goal` continuation read `STATE.md` and continue.

For the final numbered phase, set `Current phase: AUDIT` rather than chaining into audit in the same turn. The audit may be heavy and should start from a clean continuation turn.

## Failure mode this prevents

A prior the product Supergoal run completed several phases in one turn, then hit a tool-calling limit before deploy/final audit. The product code was not the issue; the execution pipeline was. The fix was protocol-level: yield between phases instead of chaining them.

## Operator pitfall

If the user says “I asked you to fix the supergoal-goal pipeline,” do not go fix the product repo’s CI/test pipeline. Scope to the Supergoal execution machinery and the loaded `chip-supergoal` skill/artifacts.

## Resuming / finishing an existing plan

When the user asks to “make a Supergoal to finish that plan” or otherwise finish a partially completed Supergoal, do not re-plan from zero and do not continue manual execution. Instead:

1. Read the existing `.supergoal/<goal>/STATE.md`.
2. Preserve completed phases exactly as listed in `Completed phases:`.
3. Build a short `SUPERGOAL_GOAL_BODY` that starts strictly from `STATE.md` current phase.
4. State that completed phases must not be restarted unless final audit finds a concrete gap.
5. Include the one-phase-per-turn rule explicitly: phase → `SUPERGOAL_TURN_YIELD` → continuation.
6. If old phase specs still say only `SUPERGOAL_PHASE_VERIFY` / `SUPERGOAL_PHASE_DONE`, patch those specs to include `SUPERGOAL_TURN_YIELD` before dispatch.
7. Save the body as a small artifact such as `.supergoal/<goal>/finish-goal-body.txt` for reproducibility.

This keeps the plan formally inside `/goal` while avoiding stale completed-work drift.

## Marker contract

A formal Supergoal phase now needs these visible markers when successful:

```text
SUPERGOAL_PHASE_START
SUPERGOAL_PHASE_VERIFY
SUPERGOAL_PHASE_DONE
SUPERGOAL_TURN_YIELD
```

Final completion still requires:

```text
AUDIT_COMPLETE
SUPERGOAL_RUN_COMPLETE
```
