# SEO/AEO/GEO Principal+ skill bundle

A public-clean, reusable skill for evidence-backed website audits and upgrade plans across:

- **SEO** — search engine visibility and indexability;
- **AEO** — answer-engine readiness and direct answer blocks;
- **GEO / LLMO** — AI-search, entity clarity, structured data, and agent-readable discovery.

## Quick start

```bash
pip install -r requirements.txt
bash scripts/test.sh
python3 scripts/seo_audit_cli.py fixture-report --fixture fixtures/schema-missing-site.json
```

Run a live public crawl:

```bash
python3 scripts/seo_audit_cli.py audit \
  --base https://example.com \
  --output /tmp/example-audit.json \
  --max-pages 20
python3 scripts/seo_audit_cli.py report \
  --input /tmp/example-audit.json \
  --output /tmp/example-audit-report.md
```

## What's included

- `SKILL.md` — Hermes skill contract.
- `AGENT_HANDOFF.md` — install and verification handoff for another agent.
- `references/` — public-clean methodology docs.
- `scripts/` — crawl, score, report, compare, mock measurement adapters, and privacy scan.
- `fixtures/` — synthetic benchmark cases.
- `tests/` — regression tests.
