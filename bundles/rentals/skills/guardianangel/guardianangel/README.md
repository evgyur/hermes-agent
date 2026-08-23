# GuardianAngel public-clean skill

GuardianAngel is a generic infrastructure monitoring skill for agents. It turns private operational patterns into a public-safe checklist for health reports, silent-on-green watchdogs, and careful alert triage.

## What is included

- `SKILL.md` — the main skill contract.
- `references/no-agent-watchdogs.md` — stdout/stderr semantics for script-only watchdogs.
- `references/backup-storage-incidents.md` — safe backup/storage incident handling.
- `references/disk-pressure-triage.md` — read-only disk investigation commands.
- `references/public-safety-checklist.md` — privacy and publication checks.
- `templates/guardian-report.md` — reusable report template.
- `scripts/privacy_scan.py` — local package scanner for obvious private markers.
- `scripts/test.sh` — no-dependency validation script.

## Install

Copy the whole `guardianangel/` folder into your agent's skills directory, then load the skill by name `guardianangel`.

Do not install from only the raw `SKILL.md` if your runtime expects linked references; this package is a multi-file skill.

## Safety posture

This archive is public-clean. It intentionally contains no private hostnames, customer/project names, chat IDs, secrets, tokens, local environment paths, or internal runbooks.
