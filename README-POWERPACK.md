# Hermes Powerpack Gen2

Powerpack Gen2 is a drop-in Hermes distribution for an existing, configured
Hermes installation. It keeps the upstream agent and installer model, then
adds the hardened Telegram, restart-continuity, exact-delivery, state.db, and
operator-safety fixes maintained by Human 2.0.

## Existing Hermes + telegram-chip

Clone the release separately from the running install:

```bash
git clone https://github.com/human20team/hermes-agent-powerpack.git
cd hermes-agent-powerpack
```

Preview the exact code transition without changing anything:

```bash
sudo -n env HOME="$HOME" HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
  bash scripts/install-powerpack.sh --dry-run --dir /opt/hermes-agent
```

Apply it and restart an already-running gateway in one controlled window:

```bash
sudo -n env HOME="$HOME" HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" \
  bash scripts/install-powerpack.sh --dir /opt/hermes-agent --restart
```

For a normal per-user install, omit `--dir`; the installer detects
`$HERMES_HOME/hermes-agent` and otherwise uses that path for a fresh install.

The installer deliberately preserves:

- `config.yaml` and `.env`;
- every profile and session;
- `state.db`, WAL, and SHM files;
- telegram-chip credentials, sessions, and its external runtime.

It refuses dirty or unknown divergent checkouts. Before switching code it
creates `backup/powerpack-<UTC timestamp>` at the old commit. A successful run
writes a non-secret receipt under
`$HERMES_HOME/runtime/receipts/powerpack-install-<version>-<sha>.json`.

## Fresh install

```bash
git clone https://github.com/human20team/hermes-agent-powerpack.git
cd hermes-agent-powerpack
bash scripts/install-powerpack.sh
hermes setup
hermes doctor
```

telegram-chip remains an independent user-owned service. If it was already
configured in Hermes, installing Powerpack does not replace or re-authorize it.

## Release identity

Powerpack `0.21.24` is based on upstream
`c30ac90a92097058ddd6f9db3fa2e3182a7bfdcc`. The exact release commit is shown
by the installer's dry-run and pinned in the resulting receipt.

Release `0.21.24` enters the durable restart path immediately instead of
waiting up to 30 minutes for an autonomous turn to finish. Active Telegram/API
turns are checkpointed before interruption; cron work retains its bounded
30-second drain. The installer now arms and verifies the lossless Telegram
restart inbox before it stops a live gateway, then clears the marker before
starting the replacement process. It also pins the HEL1 `hermesdev` exact-topic checkpoint, scoped H20
Keys Groq STT transport, and no-repeat Jyotish rectification intake to one
verified server-doctor commit. The installer records these pins in its receipt
while preserving profile files; the pinned component owner remains responsible
for installing or updating those private assets and profile-scoped credentials.

Compression now honors the configured total ceiling instead of cutting off
healthy long summaries after a hidden ten-second limit. Shutdown drains deferred
compression cleanup before closing auxiliary clients or `state.db`, and the
provider fallback chain advances through configured routes, the independent
main-agent credential, and generic discovery without retrying one failed route
forever. Completed or payment-exhausted routes remain skipped on later calls.

Legacy restart hints that lack an immutable task/generation identity can no
longer block checkpointing a newer authority-bound Telegram turn. Exact
pending or claimed continuations remain fail-closed and cannot be overwritten.

Telegram team-profile continuations now re-check current authority-group
membership through the live adapter after restart. The short-lived membership
stamp remains absent from durable state, while valid owners can resume and
revoked owners still fail closed before a continuation claim is acquired.

Planned gateway restart recovery now dispatches each synthetic continuation
inside the exact multiplex profile that owns its transcript. A `hermesdev` task
therefore resumes from the profile database instead of being quarantined after
an accidental root-database read. A turn interrupted before its first model
call by gateway shutdown is also no longer mislabeled as a stale `/stop`.

It also keeps the final Telegram delivery and its parent-task barrier in the
same multiplex profile until platform acknowledgement. A completed `hermesdev`
task therefore cannot write its delivery receipt into the default profile and
surface a spurious `parent-task delivery obligation could not bind durably`
error before the real final response.

The locked dependency sync explicitly preserves the messaging extra. Upgrading
an existing Telegram installation therefore cannot recreate its virtual
environment without `python-telegram-bot` and leave the gateway running without
its Telegram adapter.

Privileged upgrades also run `uv` without the caller's persistent cache. This
prevents a root-run install from leaving root-owned files in the configured
Hermes user's home cache.

The privileged upgrade path discovers `uv` in both supported Hermes-local
locations as well as `PATH`, so the documented sudo command does not require a
manual `UV_BIN` override.

Internal status turns without a Telegram message id now survive a concurrent
context compaction only when the state store can identify one byte-identical
active authority row. Ambiguous copies still fail closed, preventing the
periodic `inactive gateway authority without platform identity` error without
weakening external-message identity checks.
