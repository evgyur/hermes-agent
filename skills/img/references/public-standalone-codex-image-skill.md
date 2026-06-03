# Public standalone Codex OAuth image skill packaging

Use when turning the private `/img` workflow into a public-safe reusable package for another agent.

## Package shape

Include two layers:

1. `SKILL.md` contract
   - when to use the skill;
   - prompt expansion rules;
   - exact-text / QA rules;
   - failure policy: no silent fallback to API-key/FAL/OpenRouter;
   - host config hints for Hermes `image_generate`.

2. Portable runtime helper
   - `scripts/gpt_image2_codex.py` or equivalent;
   - calls `https://chatgpt.com/backend-api/codex`;
   - uses Responses API with an `image_generation` tool;
   - sets Codex-like headers: `originator: codex_cli_rs`, Codex-shaped `User-Agent`, and `ChatGPT-Account-ID` decoded from the OAuth JWT when available;
   - maps `square|landscape|portrait` to `1024x1024|1536x1024|1024x1536`;
   - maps `low|medium|high` to GPT-Image-2 quality.

## Token handling

Never bundle tokens, refresh tokens, auth.json, local config, chat IDs, or private paths.

Accept tokens from host-owned sources only:

- `CODEX_ACCESS_TOKEN`
- `OPENAI_CODEX_ACCESS_TOKEN`
- `CHATGPT_ACCESS_TOKEN`
- `--token-file`
- optional Hermes import fallback: `agent.auxiliary_client._read_codex_access_token()`
- optional best-effort scan of `~/.hermes/auth.json` for an `openai-codex` / `codex` access token

The standalone helper may detect an expired JWT and tell the operator to refresh via their auth manager. It should not implement private refresh flows unless those are public and documented.

## Verification before shipping

- YAML frontmatter parses.
- Python helper compiles.
- `python3 scripts/gpt_image2_codex.py --help` works.
- ZIP/tar reads back cleanly.
- Remove `__pycache__` before packaging.
- Scan for private paths (`/home/...`, `/opt/clawd...`), Telegram chat IDs, API keys, OAuth tokens, and local sync markers.
