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

Powerpack `0.21.6` is based on upstream
`c30ac90a92097058ddd6f9db3fa2e3182a7bfdcc`. The exact release commit is shown
by the installer's dry-run and pinned in the resulting receipt.

Release `0.21.6` pins the HEL1 `hermesdev` exact-topic checkpoint, scoped H20
Keys Groq STT transport, and no-repeat Jyotish rectification intake to one
verified server-doctor commit. The installer records these pins in its receipt
while preserving profile files; the pinned component owner remains responsible
for installing or updating those private assets and profile-scoped credentials.
