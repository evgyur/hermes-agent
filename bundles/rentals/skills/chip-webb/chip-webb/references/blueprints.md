# Blueprints

Use these as production shapes. Adapt names, routes, providers, and commands to the target repo.

## Membership Site With Supabase

Core components:

- web app with server-rendered protected pages;
- Supabase auth, profiles, entitlements, and audit tables;
- repository/data layer with `mock`, `api`, and `supabase` or equivalent modes;
- account dashboard;
- admin/operator screen;
- release fingerprint route.

Required contracts:

- auth state machine;
- entitlement/access contract;
- Supabase data authority contract;
- env/secrets contract;
- deploy/release contract.

Minimum verification:

- local Supabase reset or managed dry-run;
- guest/login/callback/reset/inactive/active journeys;
- RLS or API authorization tests;
- release fingerprint and smoke route.

## Paid Video Course

Core components:

- content catalog;
- lesson/detail pages;
- protected video route layer;
- private media storage or internal redirect root;
- signed media tokens bound to content id and expiry;
- playback state that is non-authoritative unless backed by server state.

Required contracts:

- media access contract;
- entitlement/access contract;
- auth state machine;
- source-of-truth map for media storage.

Minimum verification:

- unauthorized manifest, segment, and key requests fail;
- authorized manifest rewrites segment/key URLs;
- unsafe filenames are rejected;
- expired or wrong-content token fails;
- paid and unpaid user journeys differ at the server boundary, not only in UI.

## Account Dashboard

Core components:

- normalized server session;
- profile and linked identity display;
- entitlement cards;
- billing/payment actions;
- support or recovery path;
- API/token card when relevant;
- audit-safe admin/operator actions.

Required contracts:

- account state machine;
- permission matrix;
- entitlement/access contract;
- env/secrets contract for token display/generation.

Minimum verification:

- state table covers guest, unlinked, unpaid/inactive, active, stale cookie, and already-authenticated cases;
- dashboard never exposes another user’s data;
- support/payment links preserve safe `next` where needed;
- token creation/revocation is server-authorized.

## Admin Or Operator Dashboard

Core components:

- separate admin route group or app shell;
- explicit role checks;
- audit log for sensitive actions;
- read/write separation for dangerous controls;
- dry-run or preview mode for bulk changes.

Required contracts:

- permission matrix;
- admin action contract with idempotency and audit fields;
- data authority contract;
- rollback/safe-stop contract.

Minimum verification:

- non-admin users receive 403/redirect before data access;
- admin reads do not leak secrets or raw provider payloads;
- writes are idempotent where repeated webhooks/clicks are possible;
- audit records capture actor, target, action, timestamp, result, and evidence label.

## Protected Media Pipeline

Core components:

- source media ingest path;
- transcoding or packaging step;
- private object layout;
- manifest/key/segment route handlers;
- media signing key in server-only env;
- player with safe error states.

Required contracts:

- media access contract;
- env/secrets contract;
- deploy contract for web-server internal redirect config when used.

Minimum verification:

- no protected media exists under public static paths;
- object paths cannot escape media root;
- signed routes fail closed when env is missing;
- route logs are useful for operators but do not print tokens.

## Immutable Deploy Pipeline

Core components:

- canonical publish checkout;
- clean-tree gate;
- exact ref/SHA selection;
- release build directory or platform release id;
- env source path;
- service restart or platform promotion;
- release fingerprint route;
- smoke command and handoff.

Required contracts:

- deploy/release contract;
- rollback contract;
- env/secrets contract.

Minimum verification:

- build happens before live switch;
- current pointer or platform release matches expected SHA;
- service health is green;
- `/release.json` or equivalent fingerprint matches expected release;
- smoke covers changed surface and critical account/data paths.

## Local Supabase Loop

Core components:

- `supabase/config.toml`;
- migrations;
- seed;
- generated app env;
- auth redirect URLs;
- storage buckets and policies if media/files are local;
- reset command.

Required contracts:

- Supabase data authority contract;
- env/secrets contract;
- auth state machine when login is in scope.

Minimum verification:

- local reset succeeds;
- generated `.env.local` is ignored;
- auth redirect works locally;
- RLS/API tests prove protected paths;
- migration dry-run or reset catches schema drift.
