# Architecture-decision SuperGoal pattern

Use this when the user/operator asks to “make SuperGoal” for an architecture decision/evaluation rather than a normal coding feature.

## Shape

Do not turn the first phase into implementation or runtime mutation. The SuperGoal should produce evidence and decision artifacts first.

Recommended phase shape:

0. **Baseline & Ownership Ledger** — snapshot dirty tree, baseline SHA, existing failures, and edit ownership.
1. **SSOT Decision Update** — encode the selected architecture and boundaries in canonical docs.
2. **Evaluation Harness** — create privacy-safe fixtures and measurable metrics.
3. **Primary Candidate Probe** — test the simplest/built-in candidate in sandbox/temp profile.
4. **Fallback/Upgrade Candidate Probe** — test the next-simple sidecar only if the gap is measured.
5. **Deferred/Heavy Candidate Gate** — produce eligibility/no-go for heavier systems; no raw ingest or runtime enable.
6. **Polish & Harden** — verifier, tests, secret scan, consistency, final audit.

## Rules

- Treat existing dirty repo state as a first-class risk; Phase 0 must protect unrelated work.
- Do not change production config/provider/cron/routing in the planning chat.
- Heavy tools (Cognee, graph DBs, external providers) get an eligibility phase before any install/ingest.
- Every derived index/corpus must be marked non-canonical and rebuildable.
- If the task touches memory, privacy, gateway, model routing, or production services, include Architect+ lite and RPD gates.
- The final `/goal` body should start from repo root, read `.supergoal/PROTOCOL.md`, and run one phase per turn.

## Example application

For Hermes-local memory architecture:

- Holographic = primary built-in structured fact candidate.
- SQLite FTS5 + local embeddings/sqlite-vec = measured semantic-gap upgrade path.
- Cognee = deferred read-only graph eligibility gate only.
- mem0g remains canonical shared memory boundary.
