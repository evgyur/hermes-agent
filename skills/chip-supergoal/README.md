# chip-supergoal

Private Chip/Hermes SuperGoal planner skill. It creates a disk-backed `.supergoal/` package and one explicit `/goal` handoff for non-trivial software work.

`chip-supergoal` writes:

- `.supergoal/THINKING.md`
- `.supergoal/RESEARCH.md` when current research is required
- `.supergoal/ROADMAP.md`
- `.supergoal/STATE.md`
- `.supergoal/PROTOCOL.md`
- `.supergoal/LAUNCH_GOAL.md`
- `.supergoal/phases/phase-N.md`
- helper scripts and delivery receipts when required

The skill is independent from external `/rpd`: the RPD/Senior review pattern is embedded in `references/rpd-review-gates.md` and in the generated execution protocol.

## Use

```text
/chip-supergoal Build or refactor X end-to-end
```

The skill does not execute implementation phases. It plans, writes artifacts, reviews the plan, and emits a launch body. The later `/goal` session executes from the generated files.

## Verify

```bash
bash scripts/test.sh
```

## Privacy boundary

This installed package is a private Chip operator overlay, not a public-safe distribution. Tests scan for raw credential/private-key/JWT style leaks and contract regressions; public publication would require a separate sanitization pass.
