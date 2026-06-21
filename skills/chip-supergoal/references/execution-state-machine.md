# Execution state machine

This is the precedence table for the generated `/goal` executor.

## States

- `PLANNED` — package exists, launch not started.
- `READY_TO_DISPATCH` — planner finished and emitted launch body.
- `PHASE_N` — executor is running numbered phase N.
- `AUDIT` — all numbered phases have `SUPERGOAL_PHASE_DONE`; final audit is pending.
- `BLOCKED` — approval, missing credentials, destructive action, failed preflight, or unrecoverable verification gap.
- `DONE` — `AUDIT_COMPLETE` and `SUPERGOAL_RUN_COMPLETE` printed.

## Official GoalManager loop

1. Read `STATE.md`.
2. If `Current phase: N`, read `phases/phase-N.md`.
3. Print `SUPERGOAL_PHASE_START` and `SUPERGOAL_STATUS`.
4. Do phase work and mandatory verification.
5. Print `SUPERGOAL_PHASE_VERIFY` with criterion-to-evidence mapping.
6. Run `RPD_PHASE_REVIEW` when required.
7. Save durable non-obvious lessons or print `MEMORY_SAVED: none`.
8. Print `SUPERGOAL_PHASE_DONE`.
9. Update `STATE.md` to next phase or `AUDIT`.
10. Print `SUPERGOAL_TURN_YIELD` and stop this assistant turn.

Do not start phase N+1 in the same official GoalManager turn.

## State ledger and ignored package artifacts

- Use a real timestamp for every `STATE.md` event (`date -u +%Y-%m-%dT%H:%M:%SZ` or tool-provided equivalent). Do not write placeholder times and patch them later unless unavoidable.
- `.supergoal/` is often intentionally git-ignored. Plain `git status --short` can look clean while the package exists only as ignored files. When verifying package presence or cleanliness, also run one of:
  - `git status --short --ignored .supergoal`
  - `find .supergoal -maxdepth 3 -type f | sort`
- Before creating a new package, check whether an ignored `.supergoal/` already exists. If it is stale and the user asked for a fresh SuperGoal, remove/recreate it explicitly and record that in `STATE.md`.
- Phase evidence must distinguish tracked repo cleanliness from ignored planning artifacts. A clean tracked status does not prove the `.supergoal/` package is absent, delivered, or current.
- During final residue cleanup, exclude runtime/vendor dirs such as `venv/`, `.venv/`, `.git/`, `.supergoal/`, and `node_modules/`. Clean `__pycache__`, `.pytest_cache`, and `*.pyc` with a bounded script that skips those dirs; do not sweep the whole repo blindly or you can hit permission-owned package caches inside the active virtualenv.

## Manual Telegram/no-stall fallback

If auto-continuation is visibly not happening and the user sends a continuation command, continue from `STATE.md`. You may process more than one phase within a bounded practical budget only when that is the only way to avoid stalling. You still must emit every phase marker and update state after each phase.

## Blocked/approval precedence

- Money movement, DNS, secrets, grants, destructive production actions, and mass/public posts require explicit approval.
- A `/goal` continuation alone is not approval.
- If approval is missing, print `BLOCKED_BY_APPROVAL` or `READY_FOR_DELETE_APPROVAL` and stop.

## Audit

Final audit re-reads the original `ROADMAP.md`, not prior self-reports. It re-runs aggregate mandatory commands, checks deliverables with `repo-state.sh`, runs `RPD_FINAL_REVIEW`, and only then prints `AUDIT_COMPLETE`.

If gaps remain after bounded repair rounds, print `AUDIT_HANDOFF` and do not print `SUPERGOAL_RUN_COMPLETE`.


## Retrieval-before-ask gate

If the user says “у тебя уже есть”, references a prior approval/artifact/key/doc, or the plan depends on a value likely stored locally, search before asking:

- `.supergoal/` state and project docs;
- repo-local ignored overlays without printing secrets;
- session history and project skills;
- Telegram history through `telegram-chip` when the value was exchanged in chat.

If not found, ask for the smallest missing item and list the stores checked.

## Repo delivery completion gate

When a phase deliverable includes push/private repo/publication, `SUPERGOAL_PHASE_DONE` must cite remote HEAD match or explicit local-only boundary. Final audit treats missing remote proof as an `AUDIT_GAP`.

If the user adds a durable-doc/update instruction during the final phase or audit (for example: “пропиши это в server-doctor so future updates reapply the patch”), treat it as part of the current audit deliverable, not as a separate future promise:

- load the governing ops/update skill or reference;
- patch the reusable class-level runbook with the exact reapply shape and verification commands;
- if the runbook is repository-backed and the user did not forbid it, commit and push only that doc update, then verify remote HEAD / `HEAD...origin/main == 0 0`;
- do not let unrelated pre-existing dirty files in that repo block the targeted doc commit;
- include the durable-doc proof in `AUDIT_VERIFY` before printing `AUDIT_COMPLETE`.
