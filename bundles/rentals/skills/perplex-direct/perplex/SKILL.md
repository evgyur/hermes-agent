---
name: perplex
description: Direct Perplexity Sonar web search via the official Perplexity API. Use for web research, current facts, news, recommendations, product/pricing research, GitHub/source discovery, and any task where the user explicitly asks to search with Perplexity or /perplex. This clean version uses only PERPLEXITY_API_KEY or PPLX_API_KEY and must not route through Human20 Keys, H20 gateway, generic web_search, or other search backends.
---

# Perplex Direct

Use this skill for Perplexity-backed web research.

Hard rule: use the official Perplexity API directly:

```text
https://api.perplexity.ai/chat/completions
```

Do not use Human20 Keys, H20 gateway, `H20_KEYS_API_KEY`, `H20GW_GATEWAY_KEY`, generic `web_search`, or any non-Perplexity search backend for this skill.

## Credentials

Require one of:

- `PERPLEXITY_API_KEY`
- `PPLX_API_KEY`

If neither variable is set, stop and report that direct Perplexity credentials are missing. Do not fall back to another backend.

## Required Request Defaults

Always include:

```json
{
  "search_sources": ["social", "web"],
  "temperature": 0.2
}
```

Default model:

```text
sonar
```

Use `sonar-pro` only when the user asks for deeper research or when source quality matters more than cost.

## Helper Script

Use `scripts/perplex_direct.py` for deterministic direct API calls.

Examples:

```bash
python scripts/perplex_direct.py "open source AI coaching bots GitHub examples"
python scripts/perplex_direct.py "deep research query" --model sonar-pro --json
python scripts/perplex_direct.py --check-env
```

The script prints the answer, citations, model, and usage cost when available.

## Research Discipline

- For open-ended discovery, query Perplexity first.
- For concrete URLs found by Perplexity, fetch or inspect the source page directly before relying on a summary.
- For pricing, legal, medical, financial, or API behavior, prefer official/vendor/source pages after Perplexity discovery.
- Separate verified facts from inference.
- Return source links in the final answer.

## Failure Behavior

If the API returns an error, report the Perplexity HTTP/status error directly.

If a query needs current sources and direct Perplexity is unavailable, do not use another search provider unless the user explicitly approves a fallback.
