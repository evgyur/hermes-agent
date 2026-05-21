# Harness Engineering notes for skill authoring

Source studied: https://walkinglabs.github.io/learn-harness-engineering/ru/

Use these ideas when creating or refactoring Hermes skills.

## Direct lessons

- Root instruction files should be tables of contents and contracts, not encyclopedias.
- Move detail into `references/`, `templates/`, and `scripts/` with explicit links from `SKILL.md`.
- Skill state or workflow state should be machine-readable when agents need to resume it.
- A reusable workflow needs a definition of done, verification commands, and handoff expectations.
- Avoid “mostly done” language; require evidence and unverified-item labels.
- Add benchmark prompts for skill changes; compare old/new when feasible.
- Periodically simplify skills: if a guardrail no longer changes benchmark outcomes, remove it.

## Skill authoring checklist

When refactoring a large skill:

1. keep `SKILL.md` short and trigger-focused;
2. extract long explanations into `references/*.md`;
3. keep exact commands near the surface if they are safety-critical;
4. add a quick test checklist with realistic prompts;
5. define what evidence proves the skill works;
6. record compat shims for old entrypoints;
7. run validation and loading checks.
