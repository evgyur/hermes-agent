# Hermes web_search hardening for Perplexity

Use this when Chip complains that memory/rules are not reliable enough for web search routing, or when Perplexity/Sonar must be enforced as the default search path.

## Goal
Make the rule technical, not just mnemonic:
- `web.search_backend: perplexity` should resolve to a real provider.
- Accidental generic `web_search` calls should dispatch through Perplexity Sonar.
- Direct `web_extract` / browser use for specific known URLs remains allowed.

## Provider pattern
Create a bundled web backend under:

```text
/opt/hermes-agent/plugins/web/perplexity/
  __init__.py
  provider.py
  plugin.yaml
```

Provider requirements:
- `name == "perplexity"`
- `supports_search() == True`
- `supports_extract() == False`
- accept both `PERPLEXITY_API_KEY` and `PPLX_API_KEY`
- POST to `https://api.perplexity.ai/chat/completions`
- default model: `sonar`
- payload must include:

```json
"search_sources": ["social", "web"]
```

## Core web_tools wiring
In `tools/web_tools.py`:
- include `"perplexity"` in accepted configured backend names.
- include Perplexity near the top of fallback candidates when a Perplexity key exists.
- `_is_backend_available("perplexity")` should check `PERPLEXITY_API_KEY` or `PPLX_API_KEY`.

## Regression tests
Extend `tests/plugins/web/test_web_search_provider_plugins.py`:
- plugin registry includes `perplexity`.
- capability flags are search-only.
- availability accepts preferred key and alias key.
- mock `requests.post` and assert default payload includes:

```python
assert posted["json"]["model"] == "sonar"
assert posted["json"]["search_sources"] == ["social", "web"]
```

Run the web plugin test suite after changes. If xAI web provider is gated by env, use the repo's expected env flag for that suite.

## Reporting rule
When reporting this to Chip, do not frame it as “I will remember”. Say what technical rail was added and what test proves it. Chip's complaint in this class is about unreliable memory; answer with runtime guard + regression evidence.