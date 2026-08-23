# Production Patterns

This reference captures reusable patterns extracted from a production membership site without carrying project-specific names, hosts, domains, payment constants, service names, or secrets.

## What To Extract From A Source Project

When learning from an existing site, extract the shape, not the identity:

| Extract | Do not copy |
| --- | --- |
| Source-of-truth policy | Hostnames, user names, org names, domain names |
| Release model | Exact server paths, retired host aliases, service unit names |
| Env contract | Real env values, real secret names when too product-specific |
| Data authority rules | Production database ids, project refs, account numbers |
| Auth/account state machine | Brand-specific copy and user identifiers |
| Media access pattern | Signing keys, bucket names, internal storage paths |
| Verification gates | Product-specific smoke URLs unless working in that repo |

## Site Source Of Truth

A robust production site has a written source-of-truth map:

- current frontend directory
- current backend/API directory
- canonical publish checkout
- deployable branch or ref
- production host or platform
- runtime current path or platform release id
- runtime env path or secret store
- service/process names or platform apps
- release fingerprint route, commonly `/release.json`
- retired hosts, old scripts, and legacy paths that must not be used

Reusable rule: deploy from the canonical publish path only. Treat temporary checkouts, worktrees, release directories, backup folders, and manually restored copies as references, not publish sources.

## Frontend And Data Boundary

For app-like sites, keep screens independent from the data provider:

- pages/components render contracts, not raw database rows
- a repository or data-access layer owns `mock`, `api`, `supabase`, or equivalent modes
- route handlers own server-only mutations, cookies, webhooks, and secret access
- frontend env is generated from a source contract rather than hand-edited
- visual/design guardrails run before broad lint when the repo has them

Reusable rule: switching between mock/local, backend API, and Supabase should not require a page rewrite.

## Supabase Contract

For Supabase-backed products:

- separate current app keys from legacy/reference project keys
- use explicit prefixes such as `APP_SUPABASE_*` and `LEGACY_SUPABASE_*` when a legacy project exists
- keep migrations, seed, auth redirect config, storage config, RLS, and email templates in repo
- generate frontend/backend env from one shared env contract
- provide a dry-run path for managed or remote migrations
- keep one live write database per app process
- treat backups and legacy registries as read-only unless an approved cutover says otherwise

If the repo has no local Supabase runbook, add one instead of pretending local setup is proven:

```bash
supabase start
supabase db reset
supabase status
```

Then generate `.env.local` from local URL, anon key, service-role key, and DB URL. Never commit generated `.env.local` or print key values in chat.

## Auth And Account Pattern

Normalize account state on the server before rendering protected areas:

- `guest`
- `authenticated_unlinked`
- `authenticated_needs_activation`
- `authenticated_active`

The exact names may differ, but the state machine should distinguish identity, required linked account, paid/invited entitlement, and active access.

Critical paths:

- guest opens a protected page
- new user starts auth
- callback exchanges code and preserves safe `next`
- existing user signs in
- user with missing linked identity reaches the linking flow
- user with missing entitlement reaches activation or payment
- active user reaches the dashboard
- stale cookies degrade safely
- password reset is tested separately from signup

Reusable rule: paid/account sites need state machines, not scattered `hasAccess` booleans.

## Protected Video Pattern

Protected media should be private-by-default:

- store source media outside public static paths
- mint a server-side access token bound to content id and expiry
- serve the HLS manifest through a route handler that verifies access
- rewrite segment and key URLs to protected route handlers
- validate segment filenames and reject path traversal
- serve segments/keys through private storage reads or internal web-server redirects
- test token binding, unsafe filenames, missing assets, and unauthorized access

Public `mp4` files in a public directory are acceptable for public marketing media, not paid lessons or member-only recordings.

## Deploy Pattern

A production-grade deploy should:

- fetch and verify the chosen ref
- fail on dirty canonical publish trees
- build from an exact commit
- create an immutable release directory or platform release id
- install release metadata
- run preflight before switching live traffic
- switch live pointers only after build/preflight success
- restart services or promote the platform release in dependency order
- verify service health and release fingerprint
- smoke changed routes and critical journeys
- record SHA, release id, commands, checks, unchecked paths, residual risk, and rollback path

Reusable rule: if a deploy script can leave a half-built release live, it is not production-grade.

## Rollback Pattern

Rollback is not "go back to whatever worked":

1. Freeze current release id, current SHA, current live pointer, active services, failing route, and last known good release.
2. Decide whether rollback is data-safe. If new writes landed after the bad release, blind rollback may be unsafe.
3. Roll back by official release id or platform rollback mechanism.
4. Verify release fingerprint, health, changed routes, auth/account paths, and write paths.
5. Record what data or user actions happened after the bad release.

## Known Anti-Patterns

- editing a legacy frontend when production uses a newer app directory
- using retired host aliases or old deploy scripts
- running migrations against a remembered database
- treating a backup database as a live writer
- relying on browser localStorage for authoritative paid progress/state
- putting protected media in a public static directory
- claiming auth fixed after testing only one route
- claiming deploy success without reading the live release fingerprint
- hand-editing live release directories and forgetting to backport to git
