# Scoring model

`compute_scorecard(audit)` returns a top-level score from 0 to 100 and a 12-row scorecard.

## Row fields

| Field | Meaning |
|---|---|
| `criterion` | Scored dimension. |
| `score` | 0–10 subscore. |
| `evidence` | Compact evidence string. |
| `evidence_tier` | `direct_artifact`, `provided_context`, `external_current_source`, or `assumption`. |
| `confidence` | `low`, `medium`, `high`. |
| `impact` | `low`, `medium`, `high`, `critical`. |
| `effort` | `low`, `medium`, `high`. |
| `priority` | What to fix first. |
| `residual_gap` | What remains broken or unproven. |

## Dimensions

1. Crawlability
2. Indexability
3. Metadata
4. Sitemap quality
5. Structured data coverage
6. Content depth
7. Internal linking
8. AEO answer blocks
9. GEO / agent discovery
10. Entity authority
11. Image SEO/accessibility
12. Performance confidence

## Conservative performance rule

Without Lighthouse/PageSpeed/CrUX evidence, performance remains low-confidence and conservative. Do not invent a green performance score.
