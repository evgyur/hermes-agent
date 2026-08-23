# Decision Framework

Use this when choosing architecture for a production website. The goal is not to pick the fanciest stack; it is to choose the smallest architecture that preserves data authority, account safety, media safety, deploy provenance, and rollback.

## First Principles

- Prefer a modular monolith until load, team boundaries, compliance, or integration pressure proves otherwise.
- Keep one write authority per live domain object.
- Add a backend when it protects secrets, coordinates writes, hides privileged data, or stabilizes integrations.
- Add local Supabase when auth, RLS, migrations, storage, or edge functions are part of normal development.
- Add immutable releases when a rollback or incident would be expensive.
- Add HLS/protected media only when content access matters; public marketing media can stay simple.

## Complexity Score

| Criterion | 1 point | 2 points | 3 points |
| --- | --- | --- | --- |
| Team | one owner | small team | multiple owners/operators |
| Auth | public/anonymous | login only | paid/invited/linked identities |
| Data | read-mostly | writes + admin | payments, migrations, regulated data |
| Media | public images/video | signed downloads | paid video/HLS/private storage |
| Deploy | one platform build | custom services | multiple services + rollback |
| Failure cost | inconvenience | support load | revenue/access/data loss |

Guideline:

- `6 or less`: keep it simple; Next.js plus managed services is usually enough.
- `7-11`: staff workflow required; define contracts and risk-based verification.
- `12+`: full Architect+ planning; add formal state machines, threat model, permission matrix, and rollout policy before runtime work.

## Architecture Choices

### Static Site vs App

Use a static/marketing site when:

- there is no login;
- content is public;
- forms can post to a managed provider;
- deploy rollback is platform-native and low risk.

Use an app when:

- users have accounts;
- content access depends on session or entitlement;
- admin/operator screens exist;
- the site performs writes or personalized rendering.

### Direct Supabase vs Backend API

Use direct Supabase from the web app when:

- RLS fully expresses permissions;
- writes are simple and idempotent;
- there are no privileged provider secrets in the path;
- client payloads can be safely validated through DB constraints and policies.

Use a backend API when:

- service-role access is needed;
- payment, email, media, or provider secrets are involved;
- writes span multiple tables or systems;
- idempotency, audit logging, or workflow state matters;
- admin/operator actions need strict permission boundaries.

### Local Supabase Required vs Optional

Require local Supabase when:

- auth redirects, RLS, migrations, storage, realtime, or edge functions are in scope;
- onboarding and account journeys are core product behavior;
- database migrations are part of routine development.

Allow mock/API-only local mode when:

- the change is presentational;
- the repo has stable repository contracts;
- no auth, RLS, storage, migration, or write behavior changes.

### Public Video vs Protected HLS

Use public static video when:

- the video is public marketing material;
- leaking the URL has no business impact;
- analytics and access control are not required.

Use protected HLS/private media when:

- users pay for access;
- recordings are member-only;
- entitlement, watermarking, segment-level access, or revocation matters;
- URLs should expire.

### Platform Deploy vs Immutable Release

Use platform deploy when:

- one managed platform owns build, release, env, rollback, and logs;
- the app has one service and low operational coupling.

Use explicit immutable release flow when:

- frontend and backend must be promoted together;
- self-hosted services exist;
- env and release provenance must be auditable;
- rollback needs a known release id and current pointer.

## Decision Record Template

```text
decision:
context:
chosen option:
rejected options:
why now:
source of truth:
data authority:
auth/account impact:
media impact:
deploy/rollback impact:
verification required:
residual risk:
```

If the decision touches auth, data, payment, media, or deploy, add the relevant contract from [contracts.md](contracts.md).
