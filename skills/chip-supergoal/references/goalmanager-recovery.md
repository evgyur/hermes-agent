# GoalManager recovery

Use when a SuperGoal continuation, restart, stale wrapper, repeated complete message, or missing auto-continue behavior appears.

## Recovery order

1. Locate `.supergoal/STATE.md` for the active goal.
2. Trust `STATE.md` over chat memory.
3. If `Current phase` is numeric, resume that phase.
4. If `AUDIT`, run final audit.
5. If `BLOCKED`, surface the blocker and stop.
6. If `DONE`, do not re-run work; answer with the completion evidence or package location.

## Wrong-goal guard

If the visible chat wrapper points to a different goal than `STATE.md`, pause and ask for the correct package path or goal identity. Do not execute a stale/bogus goal.

## Completion-loop guard

Never print `SUPERGOAL_RUN_COMPLETE` again just to satisfy a repeated wrapper. Completion requires current package evidence; otherwise summarize that the goal is already complete or blocked.
