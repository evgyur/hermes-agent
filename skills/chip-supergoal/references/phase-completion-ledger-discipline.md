# Phase completion ledger discipline

Use when executing an existing `.supergoal/phases/phase-*.md` file.

## Pitfall

A phase can look complete because code/tests/report were written, while `.supergoal/STATE.md` is stale. This creates confusing progress heartbeats: report says phase done, state still points at the previous phase or omits the phase from `Completed phases`.

## Required closeout sequence

Before printing `SUPERGOAL_PHASE_DONE` / `SUPERGOAL_TURN_YIELD`:

1. Save evidence under `.supergoal/evidence/phase-N/`:
   - RED output when using TDD;
   - focused test output;
   - full suite output;
   - probe output if relevant;
   - secret/raw-surface scan if the task touches money, credentials, execution, or public channels;
   - side-effect audit.
2. Write `.supergoal/reports/phase-N.md` with:
   - command exits;
   - evidence paths;
   - acceptance-criteria map;
   - RPD phase review;
   - exact completion markers.
3. Update `.supergoal/STATE.md`:
   - `Current phase: N+1`;
   - phase checklist marks N as complete;
   - `Completed phases` includes N;
   - completion section records report path, test outputs, changed files, and mutation status.
4. Verify:
   - run the phase validator script if present;
   - grep for `SUPERGOAL_PHASE_DONE`, `SUPERGOAL_TURN_YIELD`, `Status`, and `Current phase`;
   - check target repo `git status --short`.

## Output pattern

The final phase-yield reply should include only the verified compact status: what changed, tests/probes, state, and next phase. Do not claim full goal completion unless the final audit phase wrote `AUDIT_COMPLETE` and `SUPERGOAL_RUN_COMPLETE`.