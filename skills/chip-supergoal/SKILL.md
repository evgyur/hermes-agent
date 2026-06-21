---
name: chip-supergoal
description: Principal+ plan-only SuperGoal planner for non-trivial software work. Builds a verified .supergoal package with THINKING, ROADMAP, STATE, phase specs, PROTOCOL, RPD/Senior gates, Telegram delivery receipts when required, and one explicit /goal handoff. Use for /chip-supergoal, plan and ship X, autonomous build planning, risky refactors, production-adjacent tasks, and standing SuperGoal continuation repair.
argument-hint: <describe what must be built, fixed, shipped, or planned>
---

# chip-supergoal

`chip-supergoal` is a **planner/compiler**, not the executor. It turns a non-trivial task into a disk-backed `.supergoal/` package and one launchable standard Hermes `/goal` handoff. The later upstream GoalManager session executes from the generated files, verifies every phase, runs final audit, and prints `SUPERGOAL_RUN_COMPLETE` only after `AUDIT_COMPLETE`.

## Principal+ contract

Use this root as the controller. Heavy detail lives in references and templates.

1. **Simple core, modular depth** — root owns triggers, invariants, stage order, artifact list, and reference dispatch. Incident lessons live in references.
2. **Plan-only boundary** — this skill may inspect, research, and write planning artifacts. It must not execute numbered implementation phases.
3. **One launch surface** — create exactly one human-facing launch body in `LAUNCH_GOAL.md`. Do not hide alternate launch bodies in `ROADMAP.md` or `THINKING.md`.
4. **One standard `/goal`, not a chain** — the executor reads `STATE.md` and continues until all phases plus audit complete.
5. **No false done** — every phase needs real evidence; final completion requires re-reading the original `ROADMAP.md`, re-running aggregate checks, checking deliverables, `RPD_FINAL_REVIEW`, `AUDIT_COMPLETE`, then `SUPERGOAL_RUN_COMPLETE`.
6. **Risky work gets Senior Gate** — auth, payments, secrets, production, migrations, gateways, cron/model routing, private data, destructive actions, public launches, and recurring bugs require evidence-tiered RPD/Senior review.
7. **Telegram delivery is blocking when requested** — if the user/operator asks for files or final artifacts in Telegram, scripted send + receipt is part of done, not a promise.

## Use when

Use for:

- `/chip-supergoal <task>` or “make SuperGoal / SG / ТЗ package”
- “plan and ship X”, autonomous feature/refactor/redesign planning
- brownfield work where codebase reality, tests, deployment, or recovery matter
- greenfield products/systems where stack, research, architecture, and phase boundaries matter
- standing SuperGoal continuation/repair where `STATE.md` exists
- skill/library hardening work that needs phases, review, and final audit

Do **not** use for tiny edits, one factual answer, pure copywriting, or a task whose safest path is direct execution in the current session. For those, say it is too small for SuperGoal and use the direct workflow.

## Human gates

Only two gates are allowed by default:

1. **Stage 1 clarifying questions** — only for true material gaps that tools cannot answer.
2. **Stage 6 plan review** — show the reviewed package summary and wait for explicit go/no-go before launch.

Everything else should be autonomous and evidence-backed.

## Generated artifacts

Write under `$SUPERGOAL_ROOT` (normally `<repo>/.supergoal/`):

- `THINKING.md` — goals, constraints, risks, dependencies, assumptions, memory hits, tools/skills used.
- `RESEARCH.md` — only when research gates run.
- `ROADMAP.md` — decision package, phase map, measurable acceptance criteria, mandatory commands, evidence requirements.
- `STATE.md` — current phase, baseline ref, status snapshot, events, delivery state.
- `PROTOCOL.md` — self-contained executor loop copied from `templates/PROTOCOL.md`.
- `LAUNCH_GOAL.md` — the only artifact containing a launch line beginning exactly `SUPERGOAL_GOAL_BODY:`.
- `phases/phase-N.md` — one strict phase spec per phase, validated by `scripts/validate-phase.sh`.
- `scripts/repo-state.sh` — deliverable/diff/cleanliness helper copied from this skill.
- delivery scripts/receipts when Telegram/file delivery is requested.

See `references/artifact-schemas.md` for exact schemas and `templates/LAUNCH_GOAL.md` for the launch contract.

## Procedure

| Stage | Action | Evidence |
|---|---|---|
| 0 | Resolve live skill dir, preload memory, detect tools/skills, detect resume state. | skill path + context notes |
| 1 | Intake. Brownfield asks 0–2 questions; greenfield batches up to 4 until material gaps close. | assumptions/gaps list |
| 2 | Recon. Run stack/env/repo scripts and read outputs. | 5-line stack/commands/risk summary |
| 3 | Research + architecture gates. Use skill-first research when current facts matter. | `THINKING.md`; optional `RESEARCH.md` |
| 4 | Decompose into as many phases as the task requires. | phase map with dependencies |
| 5 | Write roadmap, state, protocol, launch goal, and phase specs. | files on disk + phase validation |
| 6 | Run embedded `RPD_PLAN_REVIEW`; mutate weak artifacts or mark `checked-holds`. Show review summary and wait. | revision ledger + go/no-go |
| 6.5 | Preflight smoke: baseline commands, repo state, required files, blockers. | `PREFLIGHT_GREEN` or `PREFLIGHT_RED` |
| 7 | Emit one ready launch card/file. User starts `/goal`; planner stops. | `READY_TO_DISPATCH` or blocked state |

Detailed planning rules: `references/core-planning-contract.md`, `references/research-and-architecture-gates.md`, `references/phase-design.md`, `references/planning-depth.md`.

## Executor invariants for generated `/goal`

The generated `PROTOCOL.md` must preserve these exact marker families:

- phase loop: `SUPERGOAL_PHASE_START`, `SUPERGOAL_STATUS`, `SUPERGOAL_PHASE_VERIFY`, `MEMORY_SAVED`, `SUPERGOAL_PHASE_DONE`, `SUPERGOAL_TURN_YIELD`
- preflight: `PREFLIGHT_GREEN`, `PREFLIGHT_RED`, `READY_TO_DISPATCH`
- RPD: `RPD_PLAN_REVIEW`, `RPD_PHASE_REVIEW`, `RPD_FINAL_REVIEW`
- failure recovery: `FAILURE_PROBE`, `FAILURE_ESCALATE`, `FAILURE_HANDOFF`
- audit: `AUDIT_START`, `AUDIT_VERIFY`, `AUDIT_GAPS`, `AUDIT_COMPLETE`, `AUDIT_HANDOFF`
- delivery/approval: `SUPERGOAL_REVIEW_FILES_BLOCKED`, `SUPERGOAL_FILES_SENT`, `BLOCKED_BY_APPROVAL`, `READY_FOR_DELETE_APPROVAL`
- completion: `SUPERGOAL_RUN_COMPLETE`

Official GoalManager execution runs **at most one numbered phase per assistant turn**, then prints `SUPERGOAL_TURN_YIELD`. Manual Telegram/no-stall recovery may continue within a bounded budget only when auto-continuation is visibly unavailable, while preserving every marker and state update. See `references/execution-state-machine.md`.

## Phase spec contract

Every phase file must contain:

```text
SUPERGOAL_PHASE_START
Phase: N of TOTAL — <name>
Task: <one-line task>
Mandatory commands: <csv>
Acceptance criteria: <count>
Evidence required: <csv>
Depends on phases: <ids|none>
RPD required: yes|no
RPD focus: security|integration|ux|migration|data-loss|gateway|payments|none
```

And exact headings:

- `## Work`
- `## Acceptance criteria`
- `## Mandatory commands`
- `## Evidence required`

Run `bash "$SUPERGOAL_DIR/scripts/validate-phase.sh" <phase-file>` for every phase.

## RPD / Senior Gate

`chip-supergoal` embeds RPD. Do not invoke external `/rpd` to run this workflow.

- `RPD_PLAN_REVIEW` always runs before Stage 6 user review.
- `RPD_PHASE_REVIEW` runs in generated `/goal` for risky phases or `RPD required: yes`.
- `RPD_FINAL_REVIEW` always runs after `AUDIT_VERIFY` and before `AUDIT_COMPLETE`.
- Findings must mutate `ROADMAP.md`, `THINKING.md`, phase specs, protocol, code/work, or audit-fix specs. Otherwise mark `checked-holds` with evidence tier.

Load `references/rpd-review-gates.md` for the full evidence-tier, severity, overengineering-budget, and principal-review contract.

## Reference dispatch

Load only the matching reference:

- Core planning: `references/core-planning-contract.md`
- Upstream `/goal` compatibility: `references/upstream-goal-compatibility.md`
- Upstream/private `/goal` reconciliation: `references/upstream-goal-reconciliation.md` when deciding whether private goal patches can be reverted, reduced, or upstreamed
- SuperGoal `/goal` code-review hardening: `references/supergoal-goal-code-review-hardening.md` when reviewing/fixing Hermes GoalManager, gateway launch, Telegram clarify button, or structured-completion regressions
- Artifact schemas: `references/artifact-schemas.md`
- Execution loop and recovery: `references/execution-state-machine.md`
- Research/architecture: `references/research-and-architecture-gates.md`, `references/architect-plus-lite.md`, `references/research-before-design.md`
- Phase design and validation: `references/phase-design.md`, `references/planning-depth.md`
- RPD/Senior Gate: `references/rpd-review-gates.md`
- RPD → SuperGoal handoff: `references/rpd-to-supergoal-handoff.md` when the user/operator says “Create supergoal” / “сделай SG” in reply to an RPD/xhigh review; treat the quoted verdict as the task source and avoid mixing stale `.supergoal/` residue into the new package
- Telegram launch/delivery: `references/telegram-launch-and-delivery.md`, then specific incident refs if needed
- GoalManager recovery: `references/goalmanager-recovery.md`, then specific continuation/restart/completion refs if needed
- Dev-history hardening: `references/dev-history-hardening.md` when recent Dev chat shows repeated SuperGoal/approval/delivery/restart/retrieval problems; convert incidents into gates and probes, not root bloat
- Production safety: `references/production-safety.md`, `references/production-deploy-gates.md`, `references/process-integrity-production-runs.md`
- Skill maintenance: `references/skill-maintenance.md`, `references/category-backed-skill-path-validation.md`, `references/legacy-skill-phase-validation.md`
- Ignored package hygiene: `references/ignored-supergoal-package-hygiene.md` when `.supergoal/` may be gitignored, stale packages exist, or git status appears clean while SuperGoal state changed
- Historical archaeology only: `references/legacy-monolith-2026-06-19.md`
- Full catalog: `references/INDEX.md`

If a new incident only adds another example of an existing invariant, update the relevant reference. Add to root only when it introduces a new invariant or public marker.

## Launch and delivery rules

- `LAUNCH_GOAL.md` is the replyable launch file. It contains the exact upstream-compatible `SUPERGOAL_GOAL_BODY:` line.
- `ROADMAP.md`, `THINKING.md`, and `PROTOCOL.md` must not contain their own actual launch body line.
- Planning-stage review pack is exactly three native `.md` files by default: `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`.
- If Telegram/native file delivery is required, the run must create/send receipts and verify `ok=true` and `sent=true` before declaring the corresponding gate closed.
- Final artifacts require `SUPERGOAL_FILES_SENT` before `SUPERGOAL_RUN_COMPLETE` when final-file delivery was requested.

## Verification checklist

After editing this skill or generating a package:

```bash
cd <skill-dir>
bash scripts/test.sh
python3 <hermes-home>/skills/create-skill/scripts/skill_workflow_guard.py <skill-dir> || true
```

Then verify live loadability with `skill_view("chip-supergoal")` and, for critical refs, `skill_view("chip-supergoal", file_path="references/rpd-review-gates.md")`.

## Output Contract

For the user/operator, final planning output is compact and evidence-first:

1. what package was written
2. key risks/assumptions
3. which files were sent or where they are
4. exact launch instruction/card
5. what remains blocked, if anything

Do not claim execution success from this planner. Only the `/goal` executor can earn `AUDIT_COMPLETE` and `SUPERGOAL_RUN_COMPLETE`.

## Quick Test Checklist

- [ ] `skill_view("chip-supergoal")` loads this concise Principal+ root.
- [ ] `bash scripts/test.sh` exits 0 from the skill directory.
- [ ] `templates/LAUNCH_GOAL.md` is the only file with an actual line starting `SUPERGOAL_GOAL_BODY:`.
- [ ] `templates/ROADMAP.md` has no launch-body line.
- [ ] A filled `templates/phase-goal.txt` passes `scripts/validate-phase.sh`.
- [ ] `templates/PROTOCOL.md` preserves one-phase-per-turn and final-audit markers.
- [ ] New incident lessons update canonical references/tests instead of bloating root.
- [ ] If `.supergoal/` is ignored by git, stale package cleanup and direct package verification are reported separately from tracked implementation git status.

## Done Criteria

- [ ] Frontmatter has `name: chip-supergoal`, trigger-rich description, and `argument-hint`.
- [ ] Root `SKILL.md` stays under the local architecture budget enforced by `scripts/test.sh`.
- [ ] Planner writes `THINKING.md`, optional `RESEARCH.md`, `ROADMAP.md`, `STATE.md`, `PROTOCOL.md`, `LAUNCH_GOAL.md`, and strict phase specs.
- [ ] Embedded RPD/Senior Gate remains self-contained; no external `/rpd` dependency.
- [ ] Risky phases require RPD metadata and measurable evidence.
- [ ] Final executor completion requires `AUDIT_COMPLETE` before `SUPERGOAL_RUN_COMPLETE`.
- [ ] File/Telegram delivery, when requested, is backed by receipts before completion.
