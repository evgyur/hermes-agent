# Backup and storage incidents

Backup failures deserve careful classification. A failed backup timer can mean anything from a harmless volatile-file warning to real data-loss risk.

## First checks

```bash
systemctl --failed --no-pager
systemctl status <backup-unit> --no-pager -l
journalctl -u <backup-unit> -n 200 --no-pager
df -h
df -ih
```

## Classification

- `OK`: latest snapshot exists, integrity check passes, no active failed units.
- `ATTENTION`: snapshot saved but unit failed on volatile files or stale lock; needs cleanup but not immediate data loss.
- `CRITICAL`: no recent snapshot, repository full, repository locked by dead process and backups cannot run, integrity check fails, or storage target is unreachable beyond the allowed window.

## Safe actions

- Verify latest snapshot before resetting failed state.
- For stale locks, prove no live backup process owns the lock before unlocking.
- When storage is full, stop repeated failing timers if they are causing churn, but keep the condition visible in reports.

## Requires explicit approval

- destructive pruning;
- deleting backup repositories;
- changing retention policy;
- excluding important paths;
- re-enabling jobs before a manual backup and check succeed.
