# Principal+ rubric

A Principal+ SEO/AEO/GEO workflow is not a prettier checklist. It is an operating system that can repeatedly produce verified outcomes across different site classes.

## Levels

| Level | Capability | Evidence |
|---|---|---|
| Middle | Finds obvious gaps: title, description, sitemap, H1, basic schema. | Manual checklist or one crawl. |
| Senior | Builds direct-evidence audits, phases fixes, verifies results. | Crawl JSON, scorecard, deploy/audit proof. |
| Principal+ | Makes the method portable and testable. | Fixtures, confidence, prioritization, safe adapters, CLI reports, CI/privacy gates. |

## Principal+ dimensions

1. Direct public evidence.
2. Evidence tiers.
3. Confidence and residual gaps.
4. Impact/effort prioritization.
5. Multi-site fixture coverage.
6. Safe measurement adapters.
7. Executable reports.
8. Completion gates.

## Pass/fail gate

A final audit can claim Principal+ only if:

- `bash scripts/test.sh` passes;
- scorecard rows include evidence tier, confidence, impact, effort, priority, residual gap;
- at least five fixture archetypes are covered;
- measurement adapters run without credentials in mock/offline mode;
- final report states residual gaps honestly.
