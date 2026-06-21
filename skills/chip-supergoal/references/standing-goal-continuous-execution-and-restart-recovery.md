# Standing-goal continuous execution and restart recovery

Session lesson: Chip strongly expects a launched SuperGoal to run from its current `STATE.md` phase through final audit without stopping at phase summaries. `SUPERGOAL_TURN_YIELD` means persist state and continue, not stop.

## Execution rule

When continuing a SuperGoal:

1. Read `.supergoal/STATE.md` first.
2. Execute the current phase and immediately continue to the next safe phase.
3. Do not answer with only a phase status if more phases remain.
4. Stop only when one of these is true:
   - final audit has run;
   - mandatory artifact delivery receipts are green;
   - both `AUDIT_COMPLETE` and `SUPERGOAL_RUN_COMPLETE` are present;
   - a real approval/safety blocker prevents the next phase.

A user reply like `?` during a standing goal is usually a signal that the agent paused incorrectly. Inspect state and continue; do not switch to explanation-only mode.

## Clean successor SuperGoal pattern

If the user/operator asks for a new SuperGoal after a messy/partial/manual run:

- create a new root instead of continuing the polluted root;
- import completed reports/evidence from the previous root;
- record the target repo's actual baseline at Phase 0;
- make Phase 1 repair baseline drift before adding more capability;
- explicitly state in `LAUNCH_GOAL.md` that `SUPERGOAL_TURN_YIELD` is not permission to stop.

Do not replay completed phases; preserve them as evidence.

## Gateway restart auto-resume pattern

Active `/goal` sessions must auto-resume after gateway restart even if the last persisted conversation tail contains uncheckpointed assistant tool calls. Withholding creates the exact failure mode Chip objected to: goal stalls and asks for manual poke.

Safer behavior:

- auto-resume the active goal;
- inject an in-band startup recovery note into the continuation prompt;
- tell the resumed agent to inspect persisted state/artifacts first;
- forbid blindly repeating irreversible side effects;
- continue from canonical state files, not from stale chat text.

Regression locations used in this session:

- `gateway/run.py` startup goal recovery classification;
- `tests/gateway/test_goal_startup_recovery.py` open-tool-tail auto-resume case.
