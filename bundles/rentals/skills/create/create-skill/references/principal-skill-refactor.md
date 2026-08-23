# Principal+ skill refactor pattern

Use when a skill is powerful but has become a large incident-patch monolith. The goal is not to delete knowledge; it is to turn the root into a controller and move accumulated lessons into indexed references.

## Trigger

Apply this pattern when you see any of these:

- `SKILL.md` is near/over loader or skill-management size limits.
- Root starts with many recent incident fixes before the stable workflow contract.
- A skill mixes generic public workflow, private operator overlays, runtime artifacts, and historical postmortems.
- Launch/dispatch contracts are duplicated across several docs or templates.
- Tests verify shell syntax but not the architecture invariants that prevent future bloat.

## Refactor sequence

1. **Resolve the live target first**
   - Trust the `skill_dir` returned by the active skill loader over hard-coded paths.
   - If the live dir is not a git repo and a separate repo checkout exists, treat them as divergent until proven otherwise. Do not sync from the stale repo into the live dir.

2. **Preserve before shrinking**
   - Copy the old oversized root into `references/legacy-monolith-YYYY-MM-DD.md` or another explicit archive reference.
   - Label it archaeology/backward-compat only, not the canonical contract.

3. **Codify the disputed contract before shrinking prose**
   If active references disagree about artifact sets, delivery gates, launch surfaces, phase boundaries, or completion markers, add or update one canonical reference first. Make the boundary explicit: owner, stage, public/internal visibility, delivery default, receipt/proof, and final-audit responsibility. Then patch stale active references to point at that contract. Do not merely shorten the root while leaving conflicting incident rules alive.

4. **Rewrite root as controller**
   Keep only:
   - trigger/anti-trigger rules
   - core invariants
   - stage/procedure table
   - generated artifact contract
   - exact public markers/interfaces
   - reference dispatch map
   - output contract, quick tests, done criteria

5. **Create canonical references**
   Split by class of concern, not by session:
   - core workflow / planning contract
   - artifact schemas
   - execution state machine
   - research/architecture gates
   - delivery/launch behavior
   - recovery/failure modes
   - production safety
   - skill maintenance
   - reference index

6. **Fix single-source launch contracts**
   If a workflow has a launch body, command, or evaluator marker, make exactly one generated artifact the canonical launch surface. Documentation examples must quote or prefix the marker so validators do not treat docs as launchable artifacts.

7. **Upgrade tests to guard architecture**
   Add checks for:
   - root size budget
   - required controller sections
   - required canonical references/templates
   - exact marker/interface presence
   - single-source launch body placement
   - phase/spec/template validation
   - private-boundary or public-safety scan appropriate to the package

8. **Reclassify privacy honestly**
   If the installed skill is a private operator overlay, do not keep a fake public-safe README/test. Mark it private and scan for real secrets/credential shapes. If public distribution is required, make a separate sanitization/export pass.

9. **Verify through both skill and workflow gates**
   - Run the skill's own tests.
   - Run `skill_workflow_guard.py` when applicable.
   - Verify live loadability with `skill_view` after filesystem edits.

## Pitfalls

- Do not remove incident lessons just because the root is too large; archive and index them.
- Do not move executor-critical protocol out of copied templates unless the executor will have the reference locally.
- Do not let examples contain unquoted exact launch/evaluator lines if tests assert single-source placement.
- Do not treat a baseline-red public/privacy scan as a reason to weaken validation silently; decide whether the package is private overlay or public-safe distribution and update tests/docs accordingly.
