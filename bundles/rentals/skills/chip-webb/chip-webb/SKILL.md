---
name: chip-webb
description: Use when building, auditing, or deploying a production website that needs local Supabase, account/dashboard flows, protected video hosting, release promotion, rollback, smoke checks, or incident-safe operational rigor.
---

# Chip Webb

Build production sites as systems, not page dumps. Start from source-of-truth, data authority, account state, media access, deploy provenance, and verification evidence.

## First Move

1. Read the repo instructions: `AGENTS.md`, app-level `README.md`, deploy docs, env examples, package scripts, and any existing skill/runbook for the product.
2. Identify the live source of truth: authoring checkout, canonical publish repo, production host, runtime path, env path, services, and release fingerprint route.
3. Classify the work: frontend-only, backend/API, database/auth, video/media, account/dashboard, deploy/rollback, or incident.
4. Write down proved, inferred, unproved. Do not deploy or migrate from inferred facts.
5. Choose the smallest path that preserves the production contract.

For reusable production patterns, read [references/production-patterns.md](references/production-patterns.md).
For gates and test prompts, read [references/verification.md](references/verification.md).

## Staff Mode

For non-trivial sites, do not stop at the checklist. Load only the references needed for the decision:

| Need | Read |
| --- | --- |
| Choose architecture, backend, Supabase, video, or deploy model | [references/decision-framework.md](references/decision-framework.md) |
| Start from a production-ready shape | [references/blueprints.md](references/blueprints.md) |
| Define auth, access, media, deploy, rollback, or env contracts | [references/contracts.md](references/contracts.md) |
| Hunt for production failure paths before implementation | [references/failure-modes.md](references/failure-modes.md) |
| Generate project artifacts | [references/templates.md](references/templates.md) |
| Judge whether output is middle, senior, staff, or architect-level | [references/staff-review-rubric.md](references/staff-review-rubric.md) |

## Non-Negotiables

- One live app has exactly one write database.
- Secrets live in env stores and generated runtime env files, never in committed examples or chat output.
- Local/mock modes are allowed for development, but production claims require runtime or smoke evidence.
- Protected account content must be gated by server-derived session/access state, not client guesses.
- Protected video must not be shipped as public static files. Use signed access, path validation, and private storage or internal redirects.
- Deploy only an exact commit from the canonical publish path. No dirty tree deploys, live-directory hotfixes, manual symlink switches, or "temporary" production patches.
- Rollback by verified release id and captured current state, not by memory or old host scripts.
- Completion claims require fresh evidence: command output, route smoke, release fingerprint, or journey proof.

## Website Architecture Pattern

Use this shape unless the target repo proves a different one:

| Layer | Rule |
| --- | --- |
| Web app | Framework pages/components stay thin; route handlers own server-only mutations and cookies. |
| Data | Repository boundary supports `mock`, `api`, and `supabase` or equivalent modes. |
| Auth | Server resolves session state before protected rendering. |
| Account | Dashboard renders access, billing, profile, linked identities, tokens, and next actions from normalized server state. |
| Media | HLS/private assets route through signed access and storage adapters. |
| Deploy | Immutable release dirs plus current symlinks/pointers plus `/release.json`. |
| Ops | Scripts generate env, check DB contracts, run preflight, deploy, smoke, and record handoff. |

## Local Supabase Playbook

1. Inspect existing Supabase reality:
   - `supabase/config.toml`
   - `supabase/migrations/*`
   - `supabase/seed.sql`
   - env examples and scripts that generate frontend/backend env
   - RLS policies, auth redirect URLs, storage buckets, and email templates
2. Keep app and legacy projects separated with explicit prefixes such as `APP_SUPABASE_*` and `LEGACY_SUPABASE_*`.
3. If a repo already has managed-project scripts, use them. Look for scripts that:
   - check the selected Supabase project contract
   - apply migrations with a dry-run mode before mutation
   - generate frontend env from the selected project profile
   - generate backend env from the selected project profile and data source
4. If the repo lacks a local Supabase runbook, create a safe local template instead of pretending one exists:
   - `supabase start`
   - `supabase db reset`
   - set local redirect URLs in `supabase/config.toml`
   - generate `.env.local` from local anon/service keys
   - run auth/account/video smoke against local URLs
5. Before production DB work, prove which DB runtime is live. Do not apply migrations to a remembered cloud project, legacy project, or backup target.

## Account And Dashboard Playbook

Design the account area around explicit states:

| State | Meaning | Expected behavior |
| --- | --- | --- |
| `guest` | no valid session | render public/login path or redirect with safe `next` |
| `authenticated_unlinked` | account exists but required identity is missing | route to linking flow before protected API calls |
| `authenticated_needs_activation` | identity exists but paid/invited entitlement is missing | show activation/payment path |
| `authenticated_active` | required identity and access are present | render dashboard and protected content |

Checklist:

- Preserve `next` across login, callback, invite, payment, and reset-password boundaries.
- Treat stale refresh cookies as guest/fallback, not server-render crashes.
- Keep community/workshop/product entitlements separate; do not collapse them into one generic `hasAccess` unless the product truly has one access type.
- Test new user signup, existing sign-in, invite/join link, email callback, password reset, already-authenticated redirect, and inactive access.
- Dashboard should show access status, billing/action state, linked identity, support path, tokens/API if applicable, and the next useful action.

## Video Hosting Playbook

For protected lessons, meetings, or paid media:

1. Store source media outside public static paths.
2. Convert long video to HLS when seeking, buffering, watermarking, or access windows matter.
3. Mint a server-side access token bound to content id and expiry.
4. Serve the manifest through a route handler that verifies the token and rewrites segment/key URLs.
5. Serve segments and encryption keys through route handlers that:
   - verify the same token
   - reject unsafe filenames and path traversal
   - use private storage reads or internal redirect headers
   - set private/no-store cache headers as appropriate
6. Add tests for token/content binding, expiry-sensitive behavior where feasible, unsafe filenames, missing assets, and unauthorized access.

Production pattern: signed HLS access normally uses a server-only media signing key, manifest rewrite, segment filename validation, and either private storage reads or an internal redirect such as `X-Accel-Redirect` to a private media root.

## Deploy Playbook

Before deploy, record:

- canonical publish repo path
- target branch/ref and exact SHA
- live frontend/backend release ids
- active services
- env source paths
- whether the change is frontend-only, backend-only, DB/auth, media, or full promote

Deploy rules:

1. Fetch and verify branch state.
2. Require clean tree in the canonical publish repo.
3. Build from exact committed SHA.
4. Create an immutable release directory.
5. Generate and install release metadata.
6. Switch current pointers only after build/preflight success.
7. Restart services in dependency order.
8. Verify service health and `/release.json`.
9. Run scoped smoke plus critical journey smoke.
10. Leave handoff with SHA, release id, commands, checks, unchecked paths, and residual risk.

Generic promotion shape:

```bash
git fetch origin --prune
git status --short --branch
# Run the repo's official promote/deploy command for the selected ref.
curl -fsS "$APP_BASE_URL/release.json"
# Run the repo's official smoke command against "$APP_BASE_URL".
```

Adapt commands to the target repo. Do not copy source-project hosts, domains, service names, product ids, payment constants, or environment variable names into another project.

## Rollback And Incident Playbook

1. Freeze the facts: current release id, current SHA, current symlink targets, active services, failing route, and last known good release.
2. Decide if rollback is valid. If writes landed in a new database, blind rollback may be unsafe.
3. Roll back by official release procedure or release id. Do not use retired scripts, manual symlinks, or backup folders as source of truth.
4. Verify `/release.json`, health, changed routes, auth/account paths, and any write paths touched by the incident.
5. Record what was rolled back and what data or user actions happened after the bad release.

## Troubleshooting Map

| Symptom | First checks |
| --- | --- |
| Live UI does not match repo | `/release.json`, current release path, canonical repo SHA, CDN/cache, wrong target host |
| Auth callback fails | redirect allowlist, route-handler cookie path, safe `next`, stale cookies, email template link |
| Protected content 403 | session state, identity link, entitlement scope, RLS, service-role lookup |
| Video fails | signing key, manifest path, token/content id mismatch, segment filename, HLS root, internal redirect/nginx |
| Supabase data missing | app vs legacy env, runtime DB URI, migration history, schema cache reload, RLS |
| Deploy "succeeds" but app old | deployed ref, release metadata, symlink/current pointer, service restart, build artifact |

## Done Criteria

- Source-of-truth and live runtime were identified from files or runtime evidence.
- Env and secrets contract is documented without leaking secrets.
- Local development path is runnable or gaps are named with a safe template.
- Account/dashboard states and critical journeys are listed and verified for affected scope.
- Protected video has private storage, signed access, filename validation, and tests or explicit gap notes.
- Architecture choices are justified with decision criteria, not default stack habit.
- Required contracts exist for auth, data authority, media, deploy, rollback, and env/secrets when those surfaces are in scope.
- Known failure modes were checked or explicitly ruled out.
- Deploy/rollback process uses exact commits and release ids.
- Verification evidence covers the actual risk class, not just generic lint/build.
- Handoff states what changed, what was checked, what remains unverified, and rollback/safe-stop path.
