# No-internal-approval standing-goal execution

Use this when Chip continues an existing SuperGoal and explicitly says to run “без апрувалов внутри” / without internal approval prompts.

## Pattern

- Treat the visible standing-goal wrapper as the single bounded launch authorization.
- Continue from `.supergoal/STATE.md` `Current phase`; do not restart from phase 0 unless state says so.
- Execute only manifest-covered work: local edits, tests, read-only probes, reports, setup verification.
- Never introduce `clarify`/approval cards between phases.
- If a needed step is outside the manifest, fail closed in the phase report and stop.

## Per-phase completion gate

Before updating `STATE.md` to the next phase:

1. Run the phase work.
2. Write evidence/report files under `.supergoal/reports/` and `.supergoal/evidence/<phase>/` when applicable.
3. Run mandatory commands from the phase spec.
4. Run/write `RPD_PHASE_REVIEW` when the phase requires it.
5. Verify artifacts exist.
6. Patch `STATE.md`: current phase + completed phases + timestamp + notable event.
7. Reply with the visible contract: `SUPERGOAL_PHASE_START`, `SUPERGOAL_PHASE_VERIFY`, `RPD_PHASE_REVIEW`, `MEMORY_SAVED`, `SUPERGOAL_PHASE_DONE`, `SUPERGOAL_TURN_YIELD`.

## Pitfalls

- Do not ask for micro-approvals after the user already gave a bounded no-internal-approval launch instruction.
- Do not report a phase done before mandatory commands and artifact checks actually passed.
- Do not move `STATE.md` ahead before verification; otherwise the next continuation skips work.
- Do not leak secrets in reports; use present-redacted / status/classification evidence.
