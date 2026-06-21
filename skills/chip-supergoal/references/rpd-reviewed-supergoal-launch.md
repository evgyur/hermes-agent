# RPD-reviewed SuperGoal launch refresh

Use when the user/operator asks for `/rpd` / xhigh review of a freshly created SuperGoal and then says “дай новый supergoal”.

## Problem pattern

A plan can be directionally right but still fail the SuperGoal quality bar because phase specs keep generic acceptance criteria. If `/rpd` mutates the plan, the launch artifact must be refreshed too; otherwise the operator may start an older body that does not mention the review evidence or mutated criteria.

## Required workflow

1. Run the `/rpd` review against live artifacts, not memory:
   - `.supergoal/LAUNCH_GOAL.md`
   - `.supergoal/ROADMAP.md`
   - `.supergoal/THINKING.md`
   - current `.supergoal/phases/phase-*.md`
2. If the review finds weak/generic acceptance criteria, patch the phase specs in place with phase-specific falsifiable criteria.
3. Re-run `scripts/validate-phase.sh` on every touched phase spec.
4. Write a compact review artifact under `.supergoal/reports/rpd-xhigh-plan-review-<ts>.md`.
5. Patch `THINKING.md` / `ROADMAP.md` / `STATE.md` with a short RPD addendum so the executor sees that the launch was reviewed.
6. Rewrite `LAUNCH_GOAL.md` so its `SUPERGOAL_GOAL_BODY:` explicitly references the RPD report and the current phase specs.
7. Deliver the refreshed `LAUNCH_GOAL.md` as a native file and tell the user/operator to reply `/goal` to that file.

## Launch body checklist

The refreshed goal body should mention:

- repository/project root;
- `.supergoal/PROTOCOL.md`, `STATE.md`, `ROADMAP.md`, `THINKING.md`;
- the exact RPD report path;
- “do not treat `.supergoal/archive/` as active” when a stale completed goal was archived;
- approval gates for production/service/billing/bot side effects;
- the real completion condition: phase-specific criteria, required commands, risky-phase RPD evidence, `AUDIT_COMPLETE`, `SUPERGOAL_RUN_COMPLETE`.

## Pitfall

Do not answer only with “RPD says go”. If the plan was mutated, the operator needs a new launch artifact. The old `LAUNCH_GOAL.md` is stale by definition until rewritten.