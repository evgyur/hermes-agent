# Ignored `.supergoal/` package hygiene

Use this when creating or executing a SuperGoal package inside repos where `.supergoal/` is gitignored.

## Lesson

`git status --short` can show a clean tree while a full `.supergoal/` package exists under ignored files. That is correct for implementation cleanliness, but it can hide stale package artifacts during planning or make phase evidence look contradictory.

## Planner rule

Before creating a new package:

1. Inspect for an existing `.supergoal/` directory even if git status is clean.
2. If the user asked for a new SuperGoal and the existing package is stale/unrelated, delete or archive it before writing the new package.
3. After writing, verify the package directly, not through git status:
   - required files exist and are non-empty;
   - phase specs pass `scripts/validate-phase.sh`;
   - exactly one actual launch line starts `SUPERGOAL_GOAL_BODY:`;
   - no non-launch artifact has a line starting `SUPERGOAL_GOAL_BODY:`.
4. When reporting git status, distinguish:
   - tracked implementation status (`git status --short`);
   - ignored package visibility (`git status --short --ignored .supergoal`).

## Executor rule

During phase verification, `repo-state.sh changed-files <baseline>` may report `0` when only ignored `.supergoal/STATE.md` changed. Do not treat that as evidence that STATE was not updated. Read `STATE.md` directly for SuperGoal state evidence, and use repo-state cleanliness only for implementation-file drift.

## Good evidence wording

```text
tracked git status: clean
.supergoal package: ignored intentionally, verified directly
STATE.md: updated to Current phase: 2
implementation changed_files_since_baseline: 0
```

This prevents a false conflict between “tracked tree clean” and “SuperGoal state updated”.
