# RPD → SuperGoal handoff

Use this when Chip invokes `/rpd` or asks for xhigh/senior review, then immediately says “Create supergoal” / “сделай SG” in reply to the review.

## Lesson

Treat the quoted RPD verdict as the source task for the SuperGoal package. Do not ask Chip to restate the task when the reply context already names the decision, risks, and next action.

## Required flow

1. Identify the active object from the quoted/replied RPD text:
   - the verdict;
   - the minimal next action;
   - direct artifact paths or repo root;
   - blocked actions / approval boundaries.
2. Generate a fresh `.supergoal/` package for that object:
   - `THINKING.md` preserves the RPD evidence and assumptions;
   - `ROADMAP.md` turns the RPD next action into phases;
   - `LAUNCH_GOAL.md` contains exactly one actual `SUPERGOAL_GOAL_BODY:` line;
   - phase specs include RPD requirements for risky phases.
3. If an existing `.supergoal/` directory is present in the target root, do not mix old artifacts into the new package.
   - If this is an active continuation/repair, resume from `STATE.md` instead of creating a new package.
   - If this is a fresh package, remove or archive stale `.supergoal/` contents before writing the new package, then verify the final file list.
4. Validate before reporting ready:
   - run `scripts/validate-phase.sh` for every phase;
   - probe that required files exist;
   - ensure only `LAUNCH_GOAL.md` has a line starting exactly `SUPERGOAL_GOAL_BODY:`;
   - list the final `.supergoal/` files so stale residue is visible.

## Output shape

Keep the final report short:

- package root;
- phase count;
- validation result;
- exact launch instruction;
- attach the three review files when delivery is expected: `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`.

Do not claim execution success. The generated `/goal` executor earns `AUDIT_COMPLETE` and `SUPERGOAL_RUN_COMPLETE` later.
