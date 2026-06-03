---
name: perplex
description: "Web research skill for Perplexity Sonar and direct-source verification. Use for current facts, news, pricing, provider docs, OSINT-style web checks, and any search task where answers need citations and source sanity checks."
metadata:
  hermes:
    tags: [web, search, perplexity, research, citations]
---

# Perplex

Perplexity Sonar workflow for web-grounded research. This packaged version is portable: it contains no API keys, host paths, cookies, or private runtime assumptions.

## Trigger
Use for:
- current facts, news, prices, tariffs, provider docs, versions;
- web research with citations;
- source provenance checks;
- screenshot/social claim fact-checks;
- username/handle OSINT with confidence labels;
- tasks where a search-summary answer must be verified against primary pages.

## API pattern
Use the configured web/search tool when available. For direct API calls, read the key from environment only:

```bash
curl -s https://api.perplexity.ai/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PERPLEXITY_API_KEY" \
  -d '{"model":"sonar","messages":[{"role":"user","content":"QUERY"}],"temperature":0.2,"search_sources":["social","web"]}'
```

Never commit or print API keys. Accept `PERPLEXITY_API_KEY` or runtime-provided search backend configuration.

## Rules
- Always include `"search_sources": ["social", "web"]` when using Perplexity API directly.
- Default model: `sonar`; use `sonar-pro` only for deeper research.
- For a specific URL/product/provider/docs/pricing question, fetch the primary page directly after search discovery.
- Separate official facts from search-model prose and third-party summaries.
- Label uncertainty and source conflicts.

## Direct extraction discipline
Read `references/pricing-extraction.md` before exact-price work and `references/username-osint-maigret.md` before username OSINT.

## Output Contract
Return:
1. short answer/verdict first;
2. cited evidence or source list;
3. confidence / what is unverified;
4. next verification step when primary evidence is missing.

## Quick Test Checklist
- [ ] Perplexity API examples read key from env only.
- [ ] Exact pricing tasks fetch official/vendor pages, not only Perplexity prose.
- [ ] Screenshot/quote fact-checks search exact claim variants and separate image from caption.
- [ ] Username hits are confidence-tiered, not treated as identity proof.

## Done Criteria
- [ ] Claims are grounded in current sources or explicitly labeled uncertain.
- [ ] No API keys, cookies, private paths, or account-specific data appear in output or files.
