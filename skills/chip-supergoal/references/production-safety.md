# Production safety

Use for phases that touch production services, deploys, credentials, DNS, payments, auth, model/provider routing, gateways, cron jobs, database migrations, or destructive changes.

## Required additions

- Permission boundary: what can be read, changed, restarted, deployed, or sent.
- Approval boundary: which actions require explicit human approval.
- Rollback path: backup, revert, feature flag, migration rollback, or documented non-reversible step.
- Live verification: health check, smoke test, log probe, API probe, or user-visible artifact.
- Residual risk: what was not touched and why.

## Blockers

Do not treat `/goal` continuation as approval for money/DNS/secrets/grants/destructive production/mass-public posting. Print the appropriate blocked marker and stop.


## Safe-lane vs live-lane approval matrix

Broad instructions like “делай всё до конца”, “use Shaw to do it all”, or “полный апрув” cover safe-lane work only:

- repo edits, tests, docs, private skill edits, generated packages, local validation, commits, and requested private repo pushes;
- non-mutating readbacks and dry-run/smoke probes that do not spend money, expose secrets, mutate DNS, grant access, or post publicly.

Exact bounded approval is still required for:

- money movement, trading, wallet creation/funding/signing/approval, CLOB keys, withdrawals;
- DNS, secrets, grants/access changes, destructive production operations, public/mass sends, irreversible migrations.

When exact approval is needed, ask once for a bounded manifest naming target, action type, identities/resources, limits, geofence/status when relevant, and rollback/stop condition. Do not keep asking for piecemeal approvals after a sufficient manifest.
