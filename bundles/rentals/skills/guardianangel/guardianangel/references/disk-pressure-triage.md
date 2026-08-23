# Disk pressure triage

Disk pressure can break containers, logs, schedulers, databases, and deployments. Investigate before deleting.

## Read-only commands

```bash
df -hT / /home /opt /srv /var 2>/dev/null || df -hT
sudo du -xhd1 / 2>/dev/null | sort -hr | head -n 30
sudo du -shx /home/* /opt/* /srv/* /var/lib/* 2>/dev/null | sort -hr | head -n 50
sudo find / -xdev -type f -size +5G -printf '%s %p
' 2>/dev/null | sort -nr | head -n 50
journalctl --disk-usage 2>/dev/null || true
docker system df 2>/dev/null || true
```

## Report before action

Name the largest directories, likely owners, and whether they look like runtime data, backups, caches, logs, build artifacts, or unknown.

## Safe cleanup candidates

Only after verification:

- package manager caches;
- old build caches;
- test caches;
- orphaned stopped containers/images if the operator approves;
- old logs after retention policy is clear.

Never delete databases, object storage, backup roots, or application data just because they are large.
