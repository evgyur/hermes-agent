# Public safety checklist

Use this before sharing a GuardianAngel-derived report, skill, or archive publicly.

## Remove

- personal names and private organization names;
- hostnames, IPs, SSH aliases, chat IDs, bot names, account names;
- local absolute paths from private machines;
- API keys, tokens, session files, env files, cookies, credentials;
- raw logs that may contain secrets or personal data;
- internal project names and incident timelines that identify the operator.

## Keep

- generic workflows;
- neutral command examples;
- thresholds as placeholders;
- report shape and classification logic;
- safety guardrails.

## Verify

Run the included scanner:

```bash
python3 scripts/privacy_scan.py .
```

Then manually skim every file in the archive. Automated scans are not enough.
