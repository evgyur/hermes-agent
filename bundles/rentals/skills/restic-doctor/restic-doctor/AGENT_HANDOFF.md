# restic-doctor — public skill handoff

This is a public-clean Hermes skill for configuring, auditing, verifying, and restoring encrypted backups with `restic` and optionally `rclone`.

## Install

Copy the folder into a Hermes skills directory:

```bash
mkdir -p ~/.hermes/skills/restic-doctor
cp SKILL.md ~/.hermes/skills/restic-doctor/SKILL.md
```

Then start a new Hermes session or reload skills if your runtime supports it.

## Trigger examples

- “настрой бэкапирование”
- “проверь бэкапы”
- “restic backup”
- “restik-doctor”
- “restore file from restic snapshot”
- “repository is already locked”

## What to read first

1. `SKILL.md` — full workflow and safety contract.

## Privacy boundary

This archive contains no real infrastructure details, hostnames, keys, bucket names, passwords, tokens, or private paths. All secrets are represented as placeholders like `REPLACE_WITH_*`.

## Verification

After install, test with:

```text
Проверь, работают ли мои restic-бэкапы на этом сервере.
```

The agent should inspect existing systemd timers/cron/restic config first, avoid destructive actions, and require restore verification before saying backups work.
