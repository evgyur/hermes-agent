# Launch Goal: {{TASK_TITLE}}

Reply `/goal` to this file/message to start the generated SuperGoal executor through the standard upstream Hermes GoalManager.

- **SuperGoal root:** `{{SUPERGOAL_ROOT}}`
- **Protocol:** `{{SUPERGOAL_ROOT}}/PROTOCOL.md`
- **Roadmap:** `{{SUPERGOAL_ROOT}}/ROADMAP.md`
- **State:** `{{SUPERGOAL_ROOT}}/STATE.md`
- **Phase specs:** `{{SUPERGOAL_ROOT}}/phases/phase-*.md`

SUPERGOAL_GOAL_BODY: Execute the disk-backed SuperGoal package in `{{SUPERGOAL_ROOT}}` using standard Hermes `/goal` continuation only. First read `{{SUPERGOAL_ROOT}}/PROTOCOL.md`, `{{SUPERGOAL_ROOT}}/STATE.md`, `{{SUPERGOAL_ROOT}}/ROADMAP.md`, and `{{SUPERGOAL_ROOT}}/phases/phase-*.md`. Trust `STATE.md` over chat memory and continue from its current phase. Run at most one numbered phase per assistant turn, then stop that turn with `SUPERGOAL_TURN_YIELD`, `Goal complete: no`, `Next: <phase|AUDIT>`, and `Completion requires: AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE in the same final response.` Do not claim the whole goal is done after a phase. Run final audit after all phases. The whole `/goal` is complete only when the same final response contains both `AUDIT_COMPLETE` and `SUPERGOAL_RUN_COMPLETE` plus `Goal complete: yes`. If truly blocked, print `BLOCKED_BY_APPROVAL`, `FAILURE_HANDOFF`, or `AUDIT_HANDOFF` with the exact missing input and stop.

## Human-readable goal

{{ONE_LINE_TASK}}

## Completion condition

The standard Hermes `/goal` loop should continue until all planned phases are complete, final audit has passed, required delivery receipts exist, and the executor prints `AUDIT_COMPLETE`, `SUPERGOAL_RUN_COMPLETE`, and `Goal complete: yes` in the final response.

## Upstream `/goal` compatibility

This file is a compiler output for standard Hermes GoalManager. It does not require a custom runner. Phase state, receipts, approvals, and audit are handled by the generated package files; `/goal` only provides continuation turns and judge decisions.
