# Repeated completed-wrapper guard

Use when a `/goal` / SuperGoal continuation keeps reappearing after a root is already complete.

## Pitfall

A stale GoalManager/wrapper can repeatedly inject:

```text
[Continuing toward your standing goal]
...
If you believe the goal is complete, state so explicitly and stop.
```

If the SuperGoal root's `.supergoal/STATE.md` already says `Status: COMPLETE`, `Current phase: complete`, and terminal markers (`AUDIT_COMPLETE`, `SUPERGOAL_RUN_COMPLETE`) are present, repeating `COMPLETE — stop` is not useful. After the first visible stop it becomes chat spam and a control-plane bug.

## Required behavior

1. Inspect the referenced root and normalize malformed paths such as `.supergoal/AUDIT_HANDOFF.md/PROTOCOL.md` back to the project root.
2. If the referenced root is already complete, do not run phases and do not keep repeating the same completion line.
3. Diagnose as `STALE_GOAL_WRAPPER`; name the completed root and the active successor root if one is known.
4. Fix/clear the control-plane state when tools are available. The durable fix belongs in GoalManager/judge/gateway logic, not only in the final answer style.
5. If a newer SuperGoal root is `READY`/phase 0, do not close it just because an older previous rail is complete.

## Regression shape

A robust GoalManager guard should:

- read `.supergoal/STATE.md` before invoking the LLM/judge;
- close already-complete roots as done at the control-plane layer;
- normalize malformed audit-handoff paths;
- preserve incomplete current roots even if a previous root mentioned in the prompt is complete;
- include tests for both cases.
