---
name: guardianangel
description: "Generic infrastructure guardian skill for scheduled health reports, silent-on-green watchdogs, and careful incident triage across servers, services, containers, backups, and scheduled jobs. Use when setting up, auditing, or fixing recurring operational monitoring without leaking private infrastructure details."
tags:
  - monitoring
  - ops
  - infrastructure
  - cron
  - watchdog
---

# GuardianAngel

GuardianAngel is a public-clean operations skill for infrastructure health monitoring. It helps an agent build or maintain a scheduled guardian report without hard-coding private hosts, chat IDs, tokens, or organization-specific runbooks.

## Trigger
Use this skill when the user asks to:

- create or improve a daily infrastructure health report;
- inspect failed systemd units, containers, cron jobs, backups, or disk pressure;
- make a watchdog silent on green and noisy only for actionable failures;
- triage a recurring monitoring alert without blindly restarting or deleting things;
- package monitoring findings into a concise operator report.

## Input
Collect only the minimum needed facts:

- target hosts or host aliases;
- safe access method, for example SSH alias or local shell;
- services to check;
- optional container runtime, backup system, scheduler, and delivery target;
- alert thresholds for disk, memory, stale backups, failed jobs, and HTTP checks.

Never ask for secrets in chat. If credentials are needed, tell the operator to place them in the runtime's secret store or local environment.

## Output Contract
Return a compact operator report with:

1. **Status** — `OK`, `ATTENTION`, or `CRITICAL`.
2. **Summary** — one line naming the highest-risk finding.
3. **Hosts** — load, memory, disk, failed units, important services.
4. **Services** — HTTP/API probes and container health.
5. **Schedulers** — recurring jobs with failed or stale state.
6. **Backups** — latest verified snapshot age and broken timers/jobs.
7. **Actions taken** — only verified safe actions, with commands summarized.
8. **Remaining risks** — exact blocker or next safe step.

## Workflow

```md
GuardianAngel Progress:
- [ ] 1) Map targets and privilege boundaries
- [ ] 2) Run read-only host checks
- [ ] 3) Classify findings by impact
- [ ] 4) Repair only scoped, reversible issues
- [ ] 5) Re-run focused checks
- [ ] 6) Update scheduled job/script if the monitor itself was wrong
- [ ] 7) Deliver the final report
```

### 1. Map targets first

Build a small access map before touching anything:

```text
Host:
Role:
Access path:
Privilege model:
Critical services:
Schedulers:
Backup system:
Known exclusions:
Missing information:
```

If the map is partial, do read-only checks only. Do not infer production topology from stale memory.

### 2. Start read-only

Use bounded checks:

```bash
hostname
whoami
uptime
df -h
free -h
systemctl --failed --no-pager
```

For containers:

```bash
docker ps --format 'table {{.Names}}	{{.Status}}' 2>/dev/null || true
docker system df 2>/dev/null || true
```

For HTTP services:

```bash
curl -fsS -o /dev/null -w '%{http_code} %{time_total}
' --max-time 8 https://example.com/health
```

### 3. Classify, do not panic

Use this ladder:

- `OK` — no failed units/jobs, endpoints green, backups fresh, disk within threshold.
- `ATTENTION` — degraded but not immediately user-impacting: high disk usage, stale non-critical backup, old paused job error, quiet-but-recent scheduler skip.
- `CRITICAL` — active outage, failed critical service, backup chain broken, disk effectively full, data-loss risk, delivery failures for alerts.

A failed transient unit may be stale. Verify live services before restarting anything.

### 4. Repair carefully

Safe repair examples:

- `systemctl reset-failed <stale-transient-unit>` after verifying the underlying service is healthy;
- patch a monitoring script that emits false alerts;
- add timeouts around slow HTTP/SSH probes;
- clear a known stale lock only after proving no live process owns it.

Unsafe without explicit approval:

- deleting backups, databases, object storage, or large directories;
- running destructive cleanup commands;
- restarting production services during peak usage;
- changing firewall, DNS, payment, authentication, or routing behavior;
- exposing raw logs containing secrets or personal data.

### 5. Verify after changes

A fix is not done until the focused probe is green:

```bash
systemctl --failed --no-pager
systemctl is-active <critical-service>
curl -fsS --max-time 8 <health-url>
```

If the recurring job was changed, run it manually once and verify its scheduler state and delivery error field if available.

### 6. Keep watchdogs silent on green

For script-only watchdogs:

- empty stdout + exit `0` means silent success;
- non-empty stdout should be reserved for actionable alerts;
- known transient dependency errors should go to stderr and usually exit `0` if the domain is safe;
- non-zero exit should mean the monitor itself is broken or a fail-visible guardrail triggered.

See `references/no-agent-watchdogs.md`.

## Report Template

Use this neutral report shape:

```text
🛡 GuardianAngel · YYYY-MM-DD HH:MM TZ

Status: OK|ATTENTION|CRITICAL
Summary: <highest-risk finding or green summary>

1) Hosts
- <host>: load <...>; RAM <...>; disk <...>; failed units <n>

2) Services
- <service/url>: <status> · <latency or reason>

3) Jobs and backups
- scheduler: <enabled>/<total>, red active <n>, red paused <n>
- backups: <profile>: latest <age>, status <ok/stale/broken>

4) Actions / next step
- <what was verified or changed>
```

## Quick Test Checklist

- [ ] The skill can be used without any private hostnames, chat IDs, or secrets.
- [ ] The first checks are read-only.
- [ ] The report distinguishes stale monitor state from active production impact.
- [ ] Watchdog guidance keeps stdout empty on green.
- [ ] Disk cleanup requires explicit approval unless only reporting sizes.
- [ ] Failed backup guidance does not recommend destructive pruning by default.
- [ ] The final report includes evidence, not vague “looks fine” claims.

## Done Criteria

- [ ] Target map is explicit or limitations are stated.
- [ ] Findings are backed by live command output or clearly labeled as assumptions.
- [ ] No secrets, private paths, tokens, chat IDs, or customer names are included.
- [ ] Any change is scoped, reversible, and verified.
- [ ] Recurring monitors are silent on green and actionable on red.
- [ ] The final answer names remaining risks and safe next steps.

## Guardrails

- Do not paste credentials, tokens, private chat IDs, or raw sensitive logs into reports.
- Do not delete data or prune backups without explicit approval and a retention plan.
- Do not blindly restart services just to clear a monitoring alert.
- Do not treat every failed transient unit as an outage.
- Do not hide intentionally disabled backup or monitoring jobs if they still need operator attention.
- Do not claim a system is healthy when only the monitor was repaired.

## References

- [No-agent watchdogs](references/no-agent-watchdogs.md)
- [Backup and storage incidents](references/backup-storage-incidents.md)
- [Disk pressure triage](references/disk-pressure-triage.md)
- [Public safety checklist](references/public-safety-checklist.md)
- [Report template](templates/guardian-report.md)
