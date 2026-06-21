# Upstream `/goal` compatibility

Use this reference whenever `chip-supergoal` generates or updates a SuperGoal package. The target runtime is the standard Hermes Agent `/goal` / `GoalManager` loop, not a custom phase runner.

## Boundary

Hermes upstream `/goal` owns:

- persistent goal state in `SessionDB.state_meta` under `goal:<session_id>`;
- turn continuation by appending a normal user-role continuation prompt;
- judge calls after each assistant response;
- max-turn budget, pause/resume/clear/status, and restart recovery for active safe goals.

`chip-supergoal` owns:

- compiling the task into a disk-backed `.supergoal/` package;
- `PROTOCOL.md`, `ROADMAP.md`, `STATE.md`, phase specs, receipts, and audit rules;
- an upstream-compatible `LAUNCH_GOAL.md` containing exactly one launch body;
- transcript markers that make the standard judge continue or stop correctly.

Forbidden:

- no custom runner and no custom GoalManager replacement;
- no chain of nested `/goal` commands;
- no hidden alternate launch body in `ROADMAP.md`, `THINKING.md`, or `PROTOCOL.md`;
- no assumption that upstream `/goal` understands SuperGoal phases, receipts, audit, or approval state by itself.

## Compiler contract

`LAUNCH_GOAL.md` must compile the package into a self-contained standing goal. The launch body must tell the standard `/goal` executor to:

1. load `PROTOCOL.md`, `STATE.md`, `ROADMAP.md`, and `phases/phase-*.md` from the package root;
2. continue from `STATE.md` rather than chat memory;
3. run at most one numbered phase per assistant turn;
4. end non-final turns with a judge-proof `SUPERGOAL_TURN_YIELD` block;
5. treat the whole goal as incomplete until both `AUDIT_COMPLETE` and `SUPERGOAL_RUN_COMPLETE` are printed in the same final response;
6. stop with explicit `BLOCKED_BY_APPROVAL`, `FAILURE_HANDOFF`, or `AUDIT_HANDOFF` only for real blockers.

## Judge-proof turn endings

Because the standard judge sees only the goal plus the last response snippet, each non-final turn must explicitly say the whole goal is not complete.

Required non-final footer:

```text
SUPERGOAL_TURN_YIELD
Goal complete: no
Next: <phase N+1|AUDIT|blocked marker>
Completion requires: AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE in the same final response.
```

A normal phase `SUPERGOAL_PHASE_DONE` is not whole-goal completion. Do not write wording like “done”, “complete”, or “finished” without the footer above.

Required final footer:

```text
AUDIT_COMPLETE
SUPERGOAL_RUN_COMPLETE
Goal complete: yes
```

If audit passes but `SUPERGOAL_RUN_COMPLETE` is missing, the judge must continue.

## Blocker semantics

The upstream judge treats “blocked / needs user input” as done for the loop. Use that deliberately:

- `BLOCKED_BY_APPROVAL` for missing bounded approval/manifest;
- `FAILURE_HANDOFF` after the 3-strike failure path;
- `AUDIT_HANDOFF` after audit cannot close.

Every blocker response must include exact blocker, required user input, package path, and current `STATE.md` state. Do not use blocker markers for convenience pauses.

## Turn budget

Default upstream budget is 20 turns. Package generation should prefer <=20 executor turns total, including audit and expected retry rounds. If a task needs more, encode a visible warning in `THINKING.md` and `LAUNCH_GOAL.md` recommending `goals.max_turns` increase or task splitting.

## Compatibility test prompts

Use these as static/judge probes:

1. Phase done + `SUPERGOAL_TURN_YIELD` + `Goal complete: no` → continue.
2. `AUDIT_COMPLETE` without `SUPERGOAL_RUN_COMPLETE` → continue.
3. `SUPERGOAL_RUN_COMPLETE` without `AUDIT_COMPLETE` → continue.
4. Both final markers + `Goal complete: yes` → done.
5. `BLOCKED_BY_APPROVAL` with required manifest → done as blocked.
