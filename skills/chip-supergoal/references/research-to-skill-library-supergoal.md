# Research-to-skill-library SuperGoal pattern

Use this when the user/operator asks to turn a research/top-N improvement list into a SuperGoal that updates Hermes skills.

## Trigger

- the user/operator says “Make supergoal” after a research pass about skill improvements.
- The active target is a set of existing skills, not app/runtime code.
- The work should patch class-level skills and references, not create narrow one-session skills.

## Pattern

1. **Archive the prior `.supergoal` first** if the current workspace already contains a completed or unrelated SuperGoal.
   - Move it under `.supergoal_archive/<slug>-<timestamp>/`.
   - Copy the relevant research artifacts into the new `.supergoal/research-sources/` before planning.

2. **Resolve live skill dirs before writing phases.**
   - Use `skill_view` output (`skill_dir`) as canonical.
   - Do not trust stale hard-coded paths.
   - Do not create duplicate skills if a target is category-backed.

3. **Make Phase 0 a source-lock phase.**
   - Record target skill dirs.
   - Separate official docs/current sources from synthesis (Perplex/Deep/etc.).
   - Classify weak or irrelevant citations explicitly.
   - Map each research recommendation into target buckets: skill A, skill B, governance/regression, deferred.

4. **Use a safe-lane manifest for skill work.**
   - Allowed: skill markdown/reference/probe edits, docs/source verification, `skill_view` reloads, validation/static scans, reports/evidence.
   - Forbidden: wallet/signing/API-key/builder/agent/trade/fund/withdraw/public publish unless the user separately approves that exact side effect.

5. **Prefer class-level skill updates.**
   - Patch currently loaded class-level skills first.
   - Add `references/` support files for session-specific detail and patch `SKILL.md` with one-line discoverability pointers.
   - Add deterministic regression probes when the learning is safety/terminology/gate-related.

6. **Plan phases around durable library changes, not execution theater.**
   - Example sequence: source-lock → primary skill patch → secondary skill patch → governance/risk reference → regression probes → final skill reload/audit.

## Phase-count clarity

For zero-indexed phase files, avoid ambiguous user-facing wording.

- Good: `Total phases: 6`, files `phase-0.md` through `phase-5.md`, metadata `Phase: 0 of 5`.
- In summaries, say “6 phases, indexed 0–5” so the operator does not read `0 of 5` as five total phases.

## Done criteria

- New `.supergoal/THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`, `RESEARCH.md`, `PROTOCOL.md`, and `phase-*.md` exist.
- Every phase validates structurally.
- A preflight scan confirms target skill dirs and no secret-shaped values.
- The launch body states the safe lane and forbidden side effects explicitly.
