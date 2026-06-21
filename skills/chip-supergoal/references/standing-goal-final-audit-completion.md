# Standing-goal final audit completion

Use when Chip sends a SuperGoal continuation that says to finish through final audit, or the current/last phase spec itself requires `AUDIT_COMPLETE` / `SUPERGOAL_RUN_COMPLETE`.

## Lesson

Do not stop at a normal phase-yield if the declared finish line includes final audit and the remaining audit steps are safe. Chip expects the run to continue through the declared completion markers without internal micro-approval prompts.

## Required behavior

1. Read `.supergoal/STATE.md` and the current phase spec.
2. Execute the current phase normally.
3. If the phase is the final numbered phase and the spec/goal says final audit is part of the phase or finish line:
   - run the final audit in the same bounded continuation when safe;
   - re-run aggregated mandatory commands;
   - re-check live/read-only status if the manifest permits it;
   - write `.supergoal/reports/final-audit.md`;
   - include `AUDIT_COMPLETE` and `SUPERGOAL_RUN_COMPLETE` in the artifact and visible transcript;
   - update `.supergoal/STATE.md` to `Status: COMPLETE` / `Current phase: COMPLETE`.
4. Only stop earlier for a real blocker: unsafe side effect, missing unretrievable context, failed command/criterion after recovery, or explicit human/provider approval boundary.

## Safety boundary

This does not authorize side effects. For money/control-plane SuperGoals, final audit may use local checks, static scans, and read-only probes only. Funding, withdrawals, orders, cancels, closes, leverage/margin changes, new builder approvals, and new agent approvals remain forbidden unless separately approved.