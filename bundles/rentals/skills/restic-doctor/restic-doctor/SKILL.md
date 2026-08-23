---
name: restic-doctor
description: "Helps set up, verify, repair, and operate encrypted backups with restic/rclone on Linux servers, VPS, desktops, or NAS. Use when the user asks to configure backups, check whether backups are working, restore a file, debug restic/rclone/systemd/timer failures, or create a safe backup runbook. Also trigger on 'restik-doctor', 'backup doctor', 'restic backup', 'проверь бэкапы', 'настрой бэкапирование'."
version: 1.0.0
author: Public skill template
license: MIT
metadata:
  hermes:
    tags: [backup, restic, rclone, devops, linux, recovery]
---

# restic-doctor

Public-safe workflow for setting up and operating encrypted backups with **restic**.

This skill is intentionally generic:
- no private hosts
- no real bucket names
- no real keys
- no personal paths
- no provider-specific secrets

Use placeholders and ask the user to fill them locally.

## When to use

Use this skill when the user wants to:

- set up backups on a server, VPS, laptop, desktop, or NAS
- use restic with local disk, SFTP, S3-compatible storage, Backblaze B2, Wasabi, Hetzner Storage Box, Cloudflare R2, Google Drive via rclone, etc.
- verify that backups actually run
- restore a file or directory
- debug failed backups
- add systemd timers or cron
- create a backup policy
- check retention, prune, repository health, or encryption
- recover from a damaged or missing machine

Common phrases:
- “настрой бэкапирование”
- “проверь бэкапы”
- “restic backup”
- “backup doctor”
- “restik-doctor”
- “как восстановить файл из restic”
- “бэкапы падают”
- “сделай нормальный backup runbook”

## Safety rules

Backups are only useful if restore works. Never claim “done” until verification is done.

Before touching anything destructive:

- Do **not** delete snapshots unless the user explicitly approves.
- Do **not** run `prune` before confirming the retention policy.
- Do **not** overwrite restored files in-place unless the user explicitly asks.
- Prefer restoring into a temporary directory first.
- Never print secrets into chat or logs.
- Never store restic passwords directly in shell history.
- Use environment files with strict permissions.
- If using cloud storage, verify credentials with a harmless list/check command first.

## Output contract

For setup or audit tasks, return:

1. **Verdict**
   - working / partially working / broken / not configured

2. **Backup target**
   - source paths
   - repository backend
   - schedule
   - retention policy

3. **Evidence**
   - commands run
   - latest snapshot
   - test restore result
   - timer/cron status

4. **Fixes made**
   - files created/changed
   - permissions changed
   - timers enabled
   - scripts added

5. **Remaining risks**
   - untested paths
   - missing offsite copy
   - missing alerting
   - weak retention
   - no restore drill

6. **Next restore command**
   - exact command the user can run later

## Required discovery

Before changing configuration, inspect the machine.

Run:

```bash
whoami
hostname
uname -a
df -h
mount | sort
systemctl --version || true
restic version || true
rclone version || true
```

Find existing backup config:

```bash
systemctl list-timers --all | grep -Ei 'restic|backup|rclone' || true
systemctl list-units --all | grep -Ei 'restic|backup|rclone' || true
crontab -l 2>/dev/null | grep -Ei 'restic|backup|rclone' || true
sudo crontab -l 2>/dev/null | grep -Ei 'restic|backup|rclone' || true
```

Search likely config locations:

```bash
sudo find /etc /opt /usr/local/bin "$HOME" \
  -maxdepth 4 \
  \( -iname '*restic*' -o -iname '*backup*' -o -iname '*rclone*' \) \
  2>/dev/null | sort
```

Do not use broad filesystem scans on huge servers unless needed.

## Recommended package install

### Debian / Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y restic rclone
```

### Fedora

```bash
sudo dnf install -y restic rclone
```

### Arch

```bash
sudo pacman -S --needed restic rclone
```

Verify:

```bash
restic version
rclone version
```

## Minimal backup design

Use this default unless the user specifies otherwise.

### Source paths

Back up:

```text
/etc
/home
/opt
/root
/var/www
```

Skip volatile/cache paths:

```text
/proc
/sys
/dev
/run
/tmp
/var/tmp
/var/cache
/var/log/journal
/home/*/.cache
```

Adjust based on the actual machine.

### Repository

Use one of:

```text
local:/mnt/backup/restic-repo
sftp:user@example.com:/backup/hostname
s3:s3.amazonaws.com/bucket-name/path
b2:bucket-name:path
rclone:remote:bucket-or-folder/path
```

### Retention

Good default for small servers:

```bash
--keep-daily 7
--keep-weekly 4
--keep-monthly 6
```

For important production data, use stronger retention and at least one offsite copy.

## Secret handling

Never paste real secrets into chat.

Create an env file:

```bash
sudo install -m 700 -d /etc/restic
sudo nano /etc/restic/restic.env
sudo chmod 600 /etc/restic/restic.env
```

Example `/etc/restic/restic.env`:

```bash
export RESTIC_REPOSITORY='REPLACE_WITH_REPOSITORY'
export RESTIC_password placeholder:'REPLACE_WITH_STRONG_PASSWORD'

# Optional for S3-compatible storage:
export AWS_ACCESS_KEY_ID='REPLACE_WITH_ACCESS_KEY'
export AWS_SECRET_ACCESS_KEY='REPLACE_WITH_SECRET_KEY'
```

If using rclone:

```bash
rclone config
rclone lsd remote:
```

Then repository can be:

```bash
export RESTIC_REPOSITORY='rclone:remote:path/to/restic-repo'
export RESTIC_password placeholder:'REPLACE_WITH_STRONG_PASSWORD'
```

## Initialize repository

Load env:

```bash
set -a
. /etc/restic/restic.env
set +a
```

Initialize:

```bash
restic init
```

If repository already exists, check it:

```bash
restic snapshots
```

## Backup script

Create `/usr/local/sbin/restic-backup.sh`:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="/etc/restic/restic.env"
HOST="$(hostname -f 2>/dev/null || hostname)"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

restic backup \
  /etc \
  /home \
  /opt \
  /root \
  /var/www \
  --host "$HOST" \
  --one-file-system \
  --exclude-caches \
  --exclude '/home/*/.cache' \
  --exclude '/var/cache' \
  --exclude '/var/tmp' \
  --exclude '/tmp'

restic forget \
  --keep-daily 7 \
  --keep-weekly 4 \
  --keep-monthly 6 \
  --prune

restic check --read-data-subset=1G
```

Install:

```bash
sudo install -m 755 restic-backup.sh /usr/local/sbin/restic-backup.sh
```

Or create directly with an editor:

```bash
sudo nano /usr/local/sbin/restic-backup.sh
sudo chmod 755 /usr/local/sbin/restic-backup.sh
```

Run once manually:

```bash
sudo /usr/local/sbin/restic-backup.sh
```

## systemd service

Create `/etc/systemd/system/restic-backup.service`:

```ini
[Unit]
Description=Restic backup
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/restic-backup.sh
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
```

## systemd timer

Create `/etc/systemd/system/restic-backup.timer`:

```ini
[Unit]
Description=Run restic backup daily

[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now restic-backup.timer
```

Verify:

```bash
systemctl status restic-backup.timer --no-pager
systemctl list-timers --all | grep restic
```

## Verification checklist

A backup is not considered working until all checks pass.

### 1. Snapshot exists

```bash
set -a
. /etc/restic/restic.env
set +a

restic snapshots
```

Expected:
- at least one recent snapshot
- correct host
- expected paths included

### 2. Repository check passes

```bash
restic check
```

For periodic deeper checks:

```bash
restic check --read-data-subset=5%
```

### 3. Test restore works

Restore to temporary directory:

```bash
sudo install -m 700 -d /tmp/restic-restore-test

set -a
. /etc/restic/restic.env
set +a

restic restore latest \
  --target /tmp/restic-restore-test \
  --include /etc/hostname
```

Verify:

```bash
find /tmp/restic-restore-test -type f | head
```

Cleanup:

```bash
sudo rm -rf /tmp/restic-restore-test
```

### 4. Timer works

Run service manually:

```bash
sudo systemctl start restic-backup.service
sudo systemctl status restic-backup.service --no-pager
journalctl -u restic-backup.service -n 100 --no-pager
```

## Restore runbook

### List snapshots

```bash
set -a
. /etc/restic/restic.env
set +a

restic snapshots
```

### Restore one file or folder

Always restore into a safe temporary directory first:

```bash
mkdir -p /tmp/restic-restore

restic restore latest \
  --target /tmp/restic-restore \
  --include /path/to/file-or-folder
```

Then compare and copy manually:

```bash
diff -ru /path/to/original /tmp/restic-restore/path/to/original || true
```

### Restore full snapshot

Only do this intentionally, usually onto a fresh machine or mounted recovery disk:

```bash
restic restore SNAPSHOT_ID --target /restore-target
```

## Common problems

### `Fatal: unable to open config file`

Likely causes:
- repository was not initialized
- wrong `RESTIC_REPOSITORY`
- wrong cloud/rclone path
- missing credentials

Check:

```bash
env | grep -E '^RESTIC_|^AWS_' | sed 's/=.*/=***REDACTED/'
restic snapshots
```

### `wrong password or no key found`

Likely causes:
- wrong `RESTIC_PASSWORD`
- wrong repository
- env file not loaded by script/systemd

Check:

```bash
sudo systemctl cat restic-backup.service
sudo journalctl -u restic-backup.service -n 100 --no-pager
```

### Backup hangs or is slow

Check:
- network speed
- repository backend
- huge paths
- database files
- VM images
- node_modules/cache directories
- insufficient CPU/I/O

Useful command:

```bash
restic backup /some/path --dry-run --verbose
```

### `repository is already locked`

Check locks:

```bash
restic list locks
```

If no backup process is running, unlock:

```bash
restic unlock
```

Do not unlock if another backup is actually running.

### `no space left on device`

Check both local disk and remote storage:

```bash
df -h
restic stats
```

If retention is confirmed by the user:

```bash
restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
```

## Audit mode

When asked to audit existing backups, do not rewrite first. Inspect and report.

Run:

```bash
restic version || true
systemctl list-timers --all | grep -Ei 'restic|backup' || true
systemctl list-units --all | grep -Ei 'restic|backup' || true
journalctl -u restic-backup.service -n 200 --no-pager || true
```

If env file exists:

```bash
sudo sh -c 'test -f /etc/restic/restic.env && ls -l /etc/restic/restic.env'
```

Load env only if safe and needed:

```bash
set -a
. /etc/restic/restic.env
set +a

restic snapshots
restic check
```

Return verdict:

```text
working / partially working / broken / unknown
```

Include exact evidence.

## Alerting

Minimum useful alerting:

- systemd service failure visible in `journalctl`
- optional email/webhook on failure
- periodic manual restore drill

Simple failure hook example:

```ini
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/restic-backup.sh
OnFailure=restic-backup-alert@%n.service
```

Do not add third-party webhooks unless the user provides the endpoint and approves sending failure data there.

## Done criteria

Setup is done only when:

- restic is installed
- repository is initialized or existing repo is confirmed
- backup script exists and runs successfully
- timer or cron is enabled
- latest snapshot exists
- `restic check` passes
- test restore into `/tmp` succeeds
- retention policy is documented
- restore command is shown to the user
- secrets are stored outside chat and protected with `chmod 600`

## Quick test prompts

Use these to test the skill:

1. “Настрой мне ежедневные restic-бэкапы `/etc`, `/home`, `/opt` на S3-compatible bucket.”
2. “Проверь, работают ли мои бэкапы на этом сервере.”
3. “Восстанови файл `/etc/nginx/nginx.conf` из последнего restic snapshot в tmp-папку.”
4. “restic пишет repository is already locked — почини безопасно.”
5. “Сделай backup runbook для нового VPS без привязки к конкретному провайдеру.”
