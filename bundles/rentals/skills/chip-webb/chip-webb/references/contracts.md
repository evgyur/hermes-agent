# Contracts

Use these when a site touches auth, Supabase, protected media, deploy, rollback, env, payments, or admin/operator actions. Contracts are staff-level guardrails: if the contract is missing, implementation is still planning, not production-ready.

## Auth State Machine

Minimum states:

| State | Entry condition | Allowed next states | Must prove |
| --- | --- | --- | --- |
| `guest` | no valid session | login, public page | protected data is not fetched |
| `authenticated_unlinked` | user exists but required linked identity is absent | linking, logout | protected API calls fail closed |
| `authenticated_needs_activation` | identity exists but entitlement is absent/expired | payment, redeem, support, logout | paid content remains blocked |
| `authenticated_active` | required identity and entitlement are valid | dashboard, protected content, logout | account and content render from server state |
| `stale_or_invalid_session` | cookie/session exists but cannot refresh | guest, cookie clear/recover | server render does not crash |

Required fields:

- user id;
- email or stable identifier;
- display name source;
- linked identities;
- entitlement scopes and expiry;
- onboarding or activation state;
- safe `next` destination.

## Entitlement And Access Contract

Define:

- product/access scopes;
- who grants each scope;
- start/end timestamps;
- revocation model;
- manual grant model;
- migration/import source;
- audit fields;
- idempotency key for automated grants.

Rules:

- Do not collapse unrelated scopes into one generic `hasAccess`.
- Check access at server/API boundaries, not only UI.
- Expired, revoked, and missing access must be distinct in operator views.
- Payment or invite completion must grant access idempotently.

## Supabase Data Authority Contract

Define:

- live write database;
- legacy/read-only databases;
- backup targets;
- migration source;
- local dev database;
- cutover states;
- rollback lock condition.

Invariants:

- a live app process has exactly one write database;
- backups do not become primary without restore evidence;
- legacy projects are read-only unless explicitly approved;
- RLS/API permission model is tested for every protected table or endpoint;
- schema cache reload or equivalent is required after DDL/RPC changes when the runtime needs it.

## Media Access Contract

Define:

- public vs protected media classes;
- storage root or bucket;
- token payload fields;
- token TTL;
- segment/key route behavior;
- cache policy;
- error behavior;
- logging policy.

Invariants:

- protected media is not in public static paths;
- token is bound to content id;
- expired/wrong-content/missing token fails;
- segment filenames cannot contain path traversal or query injection;
- server-only signing material is never exposed to client bundles or logs.

## Deploy And Release Contract

Define:

- canonical publish checkout;
- deployable ref;
- build command;
- release id format or platform release identifier;
- release metadata fields;
- env source;
- live switch mechanism;
- health checks;
- smoke command;
- handoff format.

Invariants:

- dirty canonical tree blocks deploy;
- build/preflight completes before live switch;
- release fingerprint must match expected SHA/release id after deploy;
- service restart or platform promotion is verified;
- smoke covers changed routes and critical journeys.

## Rollback Contract

Define:

- current release id;
- last known good release;
- rollback command or platform mechanism;
- data-safety condition;
- write-after-bad-release handling;
- verification after rollback;
- incident handoff.

Invariants:

- rollback is by release id or platform release, not by memory;
- blind rollback is unsafe after incompatible writes;
- rollback verification includes release fingerprint and changed critical paths;
- incident report records user/data actions after the bad release.

## Env And Secrets Contract

Define:

- committed examples with placeholders only;
- local generated env paths;
- production secret store;
- public env prefix;
- server-only keys;
- rotation owner;
- logging redaction policy.

Invariants:

- `.env.local` and generated env files are ignored;
- service-role or signing keys never enter client bundles;
- scripts do not print raw secrets;
- public examples do not include real domains, tokens, account ids, or provider payloads unless they are intentionally public.

## Admin Action Contract

Define:

- actor;
- target;
- permission check;
- action payload;
- idempotency key if repeatable;
- audit record;
- safe preview/dry-run;
- rollback or compensation path.

Invariants:

- non-admins fail before data access;
- dangerous actions have audit records;
- repeated webhook/click/retry does not duplicate side effects;
- raw provider payloads are redacted in UI and handoff.
