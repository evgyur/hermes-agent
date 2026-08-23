# Agent handoff: GuardianAngel

Read `SKILL.md` first. This is a public-clean operations skill for infrastructure guardian reports and watchdog repair.

## Use it when

- a user wants a daily/weekly infrastructure report;
- a recurring monitor is noisy or silently broken;
- a failed systemd unit, container, backup, cron job, or health endpoint needs triage;
- you need a concise operator report with evidence and safe next steps.

## Do not do

- do not ask the user to paste secrets;
- do not delete backups, databases, logs, or large directories without explicit approval;
- do not restart production services before verifying live state and impact;
- do not leak private identifiers in the final report.

## Verification commands

From the skill folder:

```bash
bash scripts/test.sh
python3 scripts/privacy_scan.py .
```

Both should exit `0` for the packaged public-clean version.
