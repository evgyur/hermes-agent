# SuperGoal Markdown launch handoff

Use this when a skill-authoring or research-to-skill-library task produces a `.supergoal/LAUNCH_GOAL.md` for Chip.

## Rule

Chip expects the assistant's Markdown answer to be launchable as the goal source. Prefer a clean `Goal:` manifest over “copy this command” prose.

Good final handoff body:

```text
Goal: Implement `.supergoal/ROADMAP.md` in `<workdir>` from phase 0 through final audit. Use `.supergoal/PROTOCOL.md` and `.supergoal/phases/phase-*.md`.
...
Completion requires AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE.
```

Avoid:

```text
Send this command:
/goal Goal: ...
```

Also avoid asking “if you want, I can send it”. If the user already asked for SuperGoal, the next useful artifact is the launchable manifest.

## Recovery signal

If Chip replies `?`, `Жду supergoal`, or points at `No active goal`, assume the launch handoff failed. Re-emit the clean `Goal:` manifest or continue the phase work if the goal files are already present and the requested lane is safe. Do not start an approval-loop.
