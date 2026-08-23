# Chip Webb Verification

Use this reference to test a site or this skill's guidance.

## Quick Test Prompts

1. "Use $chip-webb to audit a Next.js paid learning site before deploy. It has Supabase auth, protected lessons, and HLS videos."
2. "Use $chip-webb to design the local development and production deploy process for a new membership website with local Supabase and a personal account."
3. "Use $chip-webb to debug a production incident where users can log in but protected video segments return 403 after a deploy."
4. "Use $chip-webb to decide whether a new paid course site needs a backend API or can use Supabase directly from the web app."
5. "Use $chip-webb to create the release handoff and rollback plan for a self-hosted production app."

Expected behavior:

- The agent reads repo instructions before proposing changes.
- The agent asks for or discovers source-of-truth and release fingerprint.
- The agent separates mock/local evidence from production evidence.
- The agent models account states and critical journeys.
- The agent treats protected video as private/signed media.
- The agent refuses dirty-tree/manual-symlink deploys.
- The agent gives a verification plan tied to the actual changed surface.

## Fast Repo Audit Commands

Use commands that match the target repo. Examples:

```bash
rg --files -g 'AGENTS.md' -g 'README*' -g 'package.json' -g '.env*' -g '*deploy*' -g '*supabase*' -g '*video*' -g 'supabase/**' -g '.github/**'
rg -n "SUPABASE|DATA_PROVIDER|HLS|release.json|redirect|auth|entitlement|X-Accel|deploy|rollback" .
git status --short --branch
```

For JS/TS repos, inspect scripts before choosing checks. Prefer Oxc scripts when available; otherwise use the repo's configured lint/test/build.

## Manual Review Checklist

### Source Of Truth

- [ ] Live app directories are named.
- [ ] Canonical publish path is named.
- [ ] Retired hosts/scripts are explicitly excluded.
- [ ] Release fingerprint route exists or a gap is recorded.
- [ ] Dirty-tree and non-canonical deploys are blocked.

### Env And Secrets

- [ ] Example env has placeholders only.
- [ ] App and legacy DB keys are separated.
- [ ] Generated `.env.local` files are ignored.
- [ ] Service-role keys are server-only.
- [ ] Scripts do not print secrets.

### Supabase

- [ ] `supabase/config.toml`, migrations, seed, and email/auth config were inspected.
- [ ] Local start/reset path exists or a safe gap/template was created.
- [ ] RLS/access policies match account states.
- [ ] Runtime DB authority is known before migration.
- [ ] Backup/legacy targets are not live writers.

### Account

- [ ] Session state machine is explicit.
- [ ] `next` is preserved safely.
- [ ] Stale cookies do not crash server render.
- [ ] Entitlements are scoped by product/community/workshop.
- [ ] Dashboard shows access, billing, identity, support, and next action.

### Video

- [ ] Protected media is outside public static paths.
- [ ] Token binds to content id and expires.
- [ ] Manifest route rewrites segments/keys.
- [ ] Segment/key routes verify token.
- [ ] Path traversal and unsafe filenames are rejected.
- [ ] Player errors are user-safe and logs are operator-useful.

### Deploy

- [ ] Exact SHA/ref is known.
- [ ] Build happens inside a staged/immutable release.
- [ ] Release metadata is installed and verified.
- [ ] Services restart only after preflight/build success.
- [ ] Smoke covers changed routes and critical journeys.
- [ ] Handoff records checked and unchecked paths.

## Evidence Ladder

| Risk | Minimum evidence | Does not prove it |
| --- | --- | --- |
| Static UI text/style | lint or targeted test plus screenshot/manual viewport check when visual | build alone |
| Protected route | focused route/component tests plus local authenticated smoke | seeing a hidden button disappear |
| Auth/onboarding | full journey proof for guest, new user, existing user, callback, reset, inactive state | testing only login form submit |
| Supabase migration | dry-run, migration application log, schema/RLS checks, API/PostgREST check when relevant | local SQL parse only |
| Payment/access | idempotency tests, webhook/status/reconcile smoke, one target DB proof | creating an order UI |
| Protected video | unit tests for token/path validation plus manifest/segment/key smoke | video plays for one active user |
| Production deploy | clean tree, exact SHA, deploy log, service health, release fingerprint, live smoke | successful build artifact |
| Rollback | current release captured, last good release selected, data-safety check, post-rollback fingerprint and smoke | knowing an older commit hash |
| Incident recovery | incident report with root cause, mitigation, data impact, verification, follow-ups | "works now" manual check |

## Staff Verification Matrix

| Surface | Required artifact | Required checks |
| --- | --- | --- |
| Architecture choice | decision record | rejected options and risk tradeoffs are explicit |
| Source of truth | source-of-truth map | live app path, publish path, env source, release route identified |
| Env/secrets | env contract | examples are placeholders, generated env ignored, server-only keys not bundled |
| Supabase | local runbook or managed-project runbook | reset/dry-run, RLS/API checks, one live write DB proof |
| Auth/account | auth journey matrix | guest/new/existing/callback/reset/unlinked/inactive/active/stale paths |
| Access/entitlements | access contract | scoped entitlements, revocation/expiry, server/API enforcement |
| Protected media | media access contract | token binding, unsafe filenames, unauthorized manifest/segment/key |
| Deploy | deployment artifact | exact SHA, clean tree, release metadata, health, smoke |
| Rollback | rollback plan | release id, data-safety gate, verification after rollback |
| Admin/operator | admin action contract | permission check, idempotency, audit, redaction |

## Negative Fixtures

Add negative fixtures for any in-scope P0/P1 surface:

- external or unsafe `next` URL;
- stale session cookie;
- user without required entitlement;
- user with wrong entitlement scope;
- non-admin accessing admin route/API;
- token for one content item used on another;
- path traversal segment filename;
- missing media signing key;
- migration pointed at legacy/backup DB;
- dirty tree before deploy;
- rollback after writes without data-safety review.

If a negative fixture is too expensive to automate, document the manual proof and residual risk in the handoff.

## Completion Audit

Before saying the work is done, require a line-item handoff:

```text
what_changed:
decision_record:
source_of_truth:
commands_run:
verification:
contracts:
failure_modes_checked:
release_or_runtime_evidence:
unchecked_paths:
residual_risk:
rollback_or_safe_stop:
```

If any line is missing for the actual risk class, the work is not complete.
