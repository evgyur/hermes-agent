# Research report → SuperGoal planning pattern

Use when Chip replies with “Make supergoal” / “Сделай supergoal” after a research/top-N improvement report.

## Durable pattern

1. Treat the research/report as the source brief, not as execution proof.
2. If a previous `.supergoal/` already exists and is complete or from a different goal, archive it first under `.supergoal_archive/<slug>-<timestamp>/` rather than overwriting it.
3. Create a fresh `.supergoal/` with the normal human-facing files:
   - `THINKING.md`
   - `ROADMAP.md`
   - `LAUNCH_GOAL.md`
   - `RESEARCH.md` when research drove the plan
   - `phases/phase-N.md`
   - `PROTOCOL.md`
   - any validation/preflight scripts needed by the future `/goal` run
4. Copy or reference the research artifact under `.supergoal/research-sources/` so the future executor does not depend on chat memory.
5. Encode a safe lane explicitly when the plan touches money, credentials, model routing, gateways, production infra, or public channels.
6. Validate every phase spec with `validate-phase.sh` and run a small preflight that proves target paths/source artifacts exist before presenting the plan.
7. Attach exactly the three review files by default in the engineering/DM chat: `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`.

## Pitfalls

- Do not continue an old completed SuperGoal just because `.supergoal/` exists. Archive it and start a new root if the user’s request is a new goal.
- Do not execute phase work while planning. The deliverable is a launchable plan, not completed implementation.
- Do not let Perplex/Sonar research become canonical by itself. Phase 0 should source-lock official docs/live target files and classify search synthesis as discovery evidence.
- Do not create duplicate skills from stale paths; future `/goal` phases must resolve live `skill_dir` before editing.

## Good phase shape for skill-library improvement SuperGoals

- Phase 0: inventory/source lock/live skill-dir resolution.
- Phase 1..N: one class-level skill or tightly coupled skill group per phase.
- Penultimate phase: regression probes / permission manifests / negative prompts.
- Final phase: reload target skills, validate discoverability, run scans/probes, then final audit.
