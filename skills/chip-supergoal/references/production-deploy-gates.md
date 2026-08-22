# Production deploy gates for Supergoal runs

Use this reference when a Supergoal phase deploys production code, especially when the repo has submodules, generated/untracked product files, or runtime services outside the main web app.

## Core lesson

A deploy phase is not just “run the deploy script after tests pass.” It must prove that the exact code intended for production is committed, reachable from the deploy ref, deployed, and running in every runtime that participates in the user-visible flow.

## Required pre-deploy gates

1. **Main repo status**
   - Capture `git status --short --branch` and `git rev-parse HEAD`.
   - Stage and commit required product files, including new tests/support modules.
   - Keep operational artifacts out of the production commit unless the plan explicitly says they ship.

2. **Submodule status and gitlink gate**
   - If a submodule has product changes, inspect it separately with `git -C <submodule> status --short --branch`.
   - Commit and push the submodule changes first.
   - Then update, stage, commit, and push the parent repo gitlink.
   - Do not deploy the parent until the deploy ref points at the new submodule commit.

3. **Canonical deploy path**
   - Deploy only from the committed production ref named in the phase spec.
   - Avoid runtime-only patches; they create split-brain between git, deploy scripts, and live services.

4. **Runtime symmetry gate**
   - Identify every runtime that must run the new behavior: web app, API, workers, bots, cron, helper services, containers, systemd units.
   - Verify source/ref, env symmetry, restart path, and active process/container for each one.
   - Restart or prove already-running updated code. “Web deployed” is not enough when a bot or worker produces/consumes the flow.

5. **Live smoke gate**
   - Verify release identity, health/services, key routes, and logs.
   - For internal endpoints, smoke both unauthorized and authorized behavior, then clean smoke data.
   - For multi-hop flows, prove the real chain end-to-end or block with the exact external dependency that prevents it.

## Hermes state rail: non-interference contract

For a Hermes self-rollout, the state-database rail protects activation; it
does not own or throttle the SuperGoal execution loop. Planning, coding,
testing, committing, pushing, candidate staging, and read-only preflight must
continue normally. Do not pause or shorten the plan merely because the rail is
installed.

Invoke the bounded read-only database check only immediately before quiescing
`hermes-gateway.service` and immediately after restart, before rollout success
is declared. A healthy check (SQLite header, bounded `PRAGMA quick_check`, and
foreign keys) adds only a few seconds at activation. Refuse the activation only
if the canonical state database cannot be certified; preserve the completed
candidate and report the database blocker rather than presenting the plan as
failed. A post-restart failure enters the reviewed rollback/stop path. The
forensic watcher is passive and must never stop/restart Hermes, repair the
database, or interrupt the current task.

## Phase-spec additions

For production phases, add acceptance criteria like:

- Main repo changes are committed and pushed to the production deploy ref before deploy.
- Any changed submodule is committed/pushed and the parent gitlink points at the new submodule commit before deploy.
- Required untracked product files are staged/committed; local logs and `.supergoal/.../logs` artifacts are intentionally excluded or explicitly committed.
- Runtime services/containers that participate in the flow are verified on the deployed code and restarted when needed.
- Live smoke covers release SHA, service health, route health, unauthorized/authorized internal endpoint behavior, cleanup, and at least one real end-to-end flow.

## Pitfalls

- Dirty submodule + parent deploy = old bot/worker code in production, even if web deploy succeeds.
- New untracked tests/support files can pass locally but vanish from the production commit.
- Restarting only the web service leaves helper bots/workers on old code.
- A successful deploy script is not proof of behavior; verify `/release`/SHA plus the actual user flow.
