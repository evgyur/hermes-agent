# Research and architecture gates

Use these gates before writing phase specs when external facts or architectural choices materially shape the plan.

## Research trigger

Run research for greenfield systems, unfamiliar domains, current SDK/API behavior, auth/payments/security/compliance, major migrations/refactors, build-vs-buy uncertainty, or explicit requests for best practice/current practice.

Use skill-first research when available. Prefer official docs/current source for SDK facts. Generic web search is fallback, not equivalent to dedicated research skills.

## Existing-solutions gate

Search libraries, SaaS, OSS repos, package registries, or reference implementations when build-vs-buy matters. Decide one of:

- `buy`
- `wrap`
- `fork`
- `copy_pattern`
- `build_fresh`
- `defer`

Record rationale in `RESEARCH.md` and `ROADMAP.md`.

## Architect+ lite

For substantial or risky work, name:

- source of truth boundaries
- permissions matrix
- failure modes and rollback/recovery
- verification strategy
- overengineering budget for new layers/fallbacks/shims/agents

A plan is not approval-ready if missing facts materially affect security, payments, auth, compliance, SDK choice, or build-vs-buy, unless the user explicitly accepts the assumption.
