---
name: seo-aeo-geo-principal
description: Principal+ operating workflow for SEO, AEO, GEO, and LLMO audits/upgrades: crawl a public site, score evidence, prioritize fixes, generate reports, and verify completion without secrets or credentialed analytics.
version: 1.0.0
author: Public-clean export
license: MIT
metadata:
  hermes:
    tags: [seo, aeo, geo, llmo, audit, scorecard, web]
---

# SEO / AEO / GEO Principal+ skill

Use this skill when a user asks to audit or improve a website for search engines, answer engines, AI search/citation, or LLM-readable discovery.

## Trigger

Load this skill for requests like:

- “audit this site for SEO/AEO/GEO”;
- “make the site AI-search ready”;
- “check schema, sitemap, llms.txt, and internal links”;
- “build a search/answer/AI visibility improvement plan”;
- “turn this SEO checklist into an evidence-backed operating system”.

## Output Contract

Return a compact, evidence-backed report with:

1. target URL and crawl scope;
2. scorecard summary: SEO, AEO, GEO, entity authority, discovery, performance confidence;
3. top priorities ranked by impact, effort, confidence, and residual gap;
4. files or commands used;
5. verification evidence;
6. explicit assumptions and blockers;
7. final status: complete, handoff, or blocked.

## Workflow

1. **Scope the audit**
   - Confirm the public base URL.
   - Default to public, read-only crawling.
   - Do not request or use analytics credentials unless the user explicitly approves.

2. **Run direct crawl evidence**
   ```bash
   python3 scripts/seo_audit_cli.py audit \
     --base https://example.com \
     --output /tmp/site-audit.json \
     --max-pages 20
   ```

3. **Score the audit**
   ```bash
   python3 scripts/seo_audit_cli.py score --input /tmp/site-audit.json
   ```

4. **Render a Markdown report**
   ```bash
   python3 scripts/seo_audit_cli.py report \
     --input /tmp/site-audit.json \
     --output /tmp/site-audit-report.md
   ```

5. **Prioritize fixes**
   - Fix crawlability/indexability first.
   - Then metadata, sitemap, structured data, content depth, internal links.
   - Then AEO answer blocks, entity graph, AI discovery endpoints, image accessibility.
   - Keep performance conservative unless a real Lighthouse/PageSpeed/CrUX artifact exists.

6. **Verify changes**
   - Re-run the crawl and score.
   - Compare before/after.
   - Report residual gaps honestly.

## Guardrails

- Public crawl only by default.
- No secrets, cookies, private analytics, or account dashboards without explicit approval.
- No fake performance confidence: if CWV/Lighthouse/PageSpeed/CrUX data is missing, mark it as a residual gap.
- Do not claim production improvement from docs alone; verify with a crawl or clearly label the result as a plan.
- Keep customer/project-specific evidence out of public exports.

## Quick Test Checklist

- [ ] `bash scripts/test.sh` passes.
- [ ] `python3 scripts/seo_audit_cli.py fixture-report --fixture fixtures/schema-missing-site.json` renders a report.
- [ ] `python3 scripts/seo_audit_cli.py compare --before fixtures/schema-missing-site.json --after fixtures/clean-site.json` shows a positive delta.
- [ ] Marker scan contains no customer names, private paths, or local operator identifiers.

## Done Criteria

- A crawl or fixture report exists.
- Scorecard rows include evidence tier, confidence, impact, effort, priority, and residual gap.
- Top priorities are ranked, not just listed.
- Tests and privacy marker scans pass.
- Residual gaps are explicit.
