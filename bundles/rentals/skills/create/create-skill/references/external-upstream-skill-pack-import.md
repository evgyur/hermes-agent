# External upstream skill-pack import

Use this when Chip asks to install a third-party skill pack into the current Hermes profile, especially from an upstream repo that ships `skills/<name>/SKILL.md` directories.

## Pattern

1. Treat the request as `user-local` unless Chip explicitly asks to modify a bundled/public Hermes repo.
2. Inspect the upstream repo in a temp directory first; do not blindly install the whole tree.
3. Select class-level skills only. Prefer a small coherent pack over every adapter skill in the repo.
   - Good: `hyperframes`, `hyperframes-cli`, `hyperframes-media`, `hyperframes-registry`, `website-to-hyperframes`.
   - Avoid installing broad helper names like `tailwind`, `gsap`, `three`, `lottie`, `waapi` unless the user asked for them or the local library has no conflicting/broader class-level skill. These can over-trigger normal frontend/design work.
4. Copy into `~/.hermes/skills/<skill-name>/` only after checking the destination does not already exist.
5. Run the create-skill workflow guard. If upstream skills lack Hermes-required sections, patch minimally rather than rewriting the upstream content:
   - `## Output Contract`
   - `## Quick Test Checklist`
   - `## Done Criteria`
6. If a copied skill has no `references/` directory, add `references/source.md` with the upstream URL so future agents can trace provenance.
7. Verify discovery and loadability:
   - `skill_view(<primary-skill>)`
   - `skills_list(query=<pack-name>)`
8. Optionally smoke-test the external CLI/runtime if it is central to the skill and cheap to check. Report it as environment readiness, not as a skill-validation requirement.

## Safety notes

- Do not encode transient environment failures as skill rules. Capture the durable install/import pattern only.
- Do not install every upstream skill by default. Skill trigger hygiene matters more than maximal coverage.
- If the only matching target is protected/bundled, do not edit it; add a user-local umbrella or stop with `Nothing to save.` depending on the task.

## Output proof

A compact completion report should include:

- installed skill names;
- source repo/path;
- guard result;
- discovery/load verification;
- runtime smoke output if checked.
