# Artifact schemas

## `THINKING.md`

Required sections:

- Goal
- Non-goals
- Constraints and permissions
- Risks top 3
- Dependencies/order
- Assumptions
- Memory hits applied
- Tools/skills used
- Best practices applied

## `RESEARCH.md`

Use only when research gates run. Required sections:

- Research status
- Sources with URLs/tool names
- Existing-solution candidates
- Build-vs-buy verdict
- Planning implications
- Unverified assumptions and falsifiers

## `ROADMAP.md`

Required sections:

- Decision package
- Context summary
- Assumptions
- Risk top 3
- Phase map
- One section per phase with deliverables, acceptance criteria, mandatory commands, evidence, dependencies
- Final polish/hardening phase when the task is product-facing or risky

`ROADMAP.md` must not contain a line beginning `SUPERGOAL_GOAL_BODY:`. Launch belongs in `LAUNCH_GOAL.md`.

## `STATE.md`

Must include:

- Goal identity / title
- Current phase (`1..N`, `AUDIT`, `BLOCKED`, or `DONE`)
- Total phases
- Baseline ref or explicit non-git baseline reason
- Status snapshot
- Delivery receipt state when file delivery is required
- Event ledger

## `LAUNCH_GOAL.md`

The only replyable/human launch surface. The generated file must include exactly one actual line beginning with the launch marker. Documentation should quote it with a leading `>` so it is not mistaken for a launchable artifact:

```text
> SUPERGOAL_GOAL_BODY: From the project root, execute the SuperGoal in `<SUPERGOAL_ROOT>` using `<SUPERGOAL_ROOT>/PROTOCOL.md`, `<SUPERGOAL_ROOT>/ROADMAP.md`, `<SUPERGOAL_ROOT>/STATE.md`, and `<SUPERGOAL_ROOT>/phases/phase-*.md`. Start from STATE.md current phase, run one phase per turn, run the final audit, and finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE.
```

## `phases/phase-N.md`

Must pass `scripts/validate-phase.sh`. See root Phase spec contract.

## Delivery receipts

Planning review receipt:

```json
{"ok": true, "sent": true, "target": "...", "files": ["THINKING.md", "ROADMAP.md", "LAUNCH_GOAL.md"], "hashes": {}}
```

Final artifacts receipt:

```json
{"ok": true, "sent": true, "target": "...", "archive": "...", "hash": "..."}
```

Receipts are evidence, not decoration. Missing or false receipts keep the run blocked.
