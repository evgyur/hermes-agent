# Safe measurement adapters

Credentialed analytics are useful but sensitive. This skill treats them as optional, approval-gated evidence.

## Default mode

Adapters run in mock/offline mode by default and emit:

```json
{
  "source": "lighthouse",
  "available": true,
  "score": 0.91,
  "evidence_tier": "external_current_source",
  "confidence": "high",
  "residual_gap": "none"
}
```

## Approval-gated sources

- Google Search Console
- Yandex Webmaster
- PageSpeed Insights
- CrUX
- Any private analytics dashboard

No test or public bundle should require these credentials.
