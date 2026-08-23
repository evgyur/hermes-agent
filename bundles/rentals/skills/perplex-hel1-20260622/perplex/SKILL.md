---
name: perplex
description: Default web search via Perplexity Sonar API. Use for ANY web search — news, facts, research, prices, etc. Always includes social + web sources. ~$0.006/query.
metadata:
  clawdbot:
    emoji: 🔎
    command: /perplex
---

# Perplex — Default Web Search

**This is the default search tool.** Use Sonar API for all web searches.
Cost: ~$0.006 per query (~₽0.6). Always include social + web sources.

Hard rail on this Hermes install: `web.search_backend: perplexity`, and `/opt/hermes-agent` has a bundled `plugins/web/perplexity` provider. Even if the generic `web_search` tool currently dispatches to Perplexity Sonar internally, do **not** use `web_search` as the visible search action for Chip's open-ended searches. Use the Perplexity workflow/API/helper (`perplex` / Sonar with `search_sources: ["social", "web"]`) for search, and reserve direct extraction/browser tools for specific known URLs or interactive pages. If an accidental generic `web_search` call happens, acknowledge it as a workflow miss rather than defending that the backend was Perplexity.

## Chip workflow correction — do not use visible generic web_search

Chip explicitly corrected this on 2026-06-07: even if the runtime backend behind `web_search` is Perplexity, do **not** treat generic `web_search` as the normal operator-facing search path. For all open-ended searches — facts, prices, schedules, news, travel details, product comparisons — use this Perplexity/Sonar workflow directly, with `search_sources: ["social", "web"]`. Use `web_extract`/browser only when opening a concrete known URL or verifying a specific source page.

Response discipline after this correction:
- Do not say “web_search is okay because it routes to Perplexity” as a defense.
- Search via Perplexity first; then fetch official/vendor/source pages directly when exact prices, hours, tariffs, or policies matter.
- If generic `web_search` appears in a Telegram tool preview for an open search, treat it as a workflow miss and correct the habit.

## Human20 Keys routing on Human20/Hermes bots

On Human20-managed Hermes bots, Perplexity/Sonar must route through Human20 Keys / H20 gateway rather than storing an upstream Perplexity key on the bot.

Primary env vars:
- `H20_KEYS_API_KEY`
- `H20GW_GATEWAY_KEY`

Do **not** add `PERPLEXITY_API_KEY` / `PPLX_API_KEY` unless Chip explicitly approves a direct upstream fallback for that install.

Primary endpoint for this bot class:

```text
http://127.0.0.1:18741/v1/chat/completions
```

Accepted Sonar aliases observed behind H20 gateway:
- `sonar`
- `pplx-sonar`
- `perplexity/sonar`

Implementation note: Hermes `plugins/web/perplexity/provider.py` and `tools/web_tools.py` both need to recognize the H20 env vars; otherwise the provider can work by curl but still look unavailable to `web_search`.

## Quick Usage (API — Primary Method via Human20 Keys)

```bash
source /home/hermes/.hermes/.env
key="${H20_KEYS_API_KEY:-$H20GW_GATEWAY_KEY}"
curl -s http://127.0.0.1:18741/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $key" \
  -d '{
    "model": "sonar",
    "messages": [{"role": "user", "content": "YOUR QUERY"}],
    "temperature": 0.2,
    "search_sources": ["social", "web"]
  }'
```

### MANDATORY: Always include `"search_sources": ["social", "web"]`

This ensures results from Twitter/X, Reddit, forums AND regular web.

### Direct upstream fallback

Only use direct `https://api.perplexity.ai/chat/completions` with `PERPLEXITY_API_KEY` / `PPLX_API_KEY` when Human20 routing is unavailable **and** Chip explicitly approves storing an upstream key on that machine.

## Helper Script

File: `/opt/clawd-workspace/scripts/perplex_search.sh`

```bash
perplex "your search query"
# or with model:
perplex "deep research topic" sonar-pro
```

## Python One-Liner

```python
import os, requests

def perplex(q, model="sonar"):
    key = os.environ.get("H20_KEYS_API_KEY") or os.environ.get("H20GW_GATEWAY_KEY")
    if not key:
        raise RuntimeError("Missing H20_KEYS_API_KEY/H20GW_GATEWAY_KEY; do not fall back to raw Perplexity without explicit approval")
    r = requests.post(
        "http://127.0.0.1:18741/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": q}], "temperature": 0.2, "search_sources": ["social", "web"]},
        timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    return d["choices"][0]["message"]["content"], d.get("citations", []), d.get("usage", {})
```

## Models & Pricing (Perplexity)

| Model | Cost/req | Use case |
|-------|----------|----------|
| `sonar` | ~$0.006 | Default — fast web search |
| `sonar-pro` | ~$0.02 | Deeper research, more sources |
| `sonar-reasoning` | ~$0.01 | Multi-step reasoning |
| `sonar-reasoning-pro` | ~$0.05 | Best quality, complex research |

Default: **sonar** (cheapest, good enough for 90% of searches).

## Perplexity Agent API / Search as Code options (June 2026)

Perplexity now has a separate **Agent API** surface in addition to Sonar chat completions and raw Search API. Treat this as an upgrade path for wide/deep research, batch evidence gathering, and tasks where an agent should orchestrate search/code itself.

### Surfaces

1. **Sonar Chat Completions** — current default for `/perplex` quick answers.
   - Endpoint used by this install via H20 gateway: `POST /v1/chat/completions` with `model: "sonar"` and `search_sources: ["social", "web"]`.
   - Best for: one-off answers with citations, lightweight research.

2. **Search API** — raw ranked results, no prose answer.
   - Endpoint: `POST https://api.perplexity.ai/search`.
   - Returns structured `results[]` with `title`, `url`, `snippet`, `date`, `last_updated`.
   - Parameters include `query`, `max_results` (1-20), `country`, `search_domain_filter`, `search_context_size`, `max_tokens`, `max_tokens_per_page`.
   - Pricing: **$5 / 1K requests**, no token cost.
   - Best for: discovery/enrichment pipelines, URL collection, exact source lists before extraction.

3. **Agent API** — multi-provider Responses-style API with tools.
   - Endpoint: `POST https://api.perplexity.ai/v1/agent`; OpenAI-compatible alias: `POST /v1/responses`.
   - Models use provider-prefixed IDs such as `openai/gpt-5.5`, `anthropic/claude-*`, `google/gemini-*`, `xai/grok-*`.
   - Tools include `web_search`, `fetch_url`, `sandbox`, `finance_search`, `people_search`.
   - Best for: deep/wide research where the model should plan searches, fetch pages, run code, aggregate evidence, and return a final answer.

4. **Search as Code (SaC)** — Perplexity's new agentic search architecture, exposed through the Agent API + Sandbox Tool.
   - Concept: model generates code in a secure sandbox and uses an Agentic Search SDK to control retrieval, ranking, filtering, fanout, rendering, and extraction.
   - Use it when plain Sonar would blend sources or when a task needs programmable search loops, deduping, CVE/vendor mapping, large table construction, or evidence QA.

### Agent API tool pricing anchors

- `web_search`: **$0.005 / invocation** ($5 / 1K)
- `fetch_url`: **$0.0005 / invocation** ($0.50 / 1K)
- `people_search`: **$0.005 / invocation**
- `finance_search`: **$0.005 / invocation**
- `sandbox`: **$0.03 / session**; 20-minute billing window per container, not a runtime cap. Search SDK calls made inside sandbox are billed separately at the usual search/tool rate.
- Model tokens are billed separately at Agent API model rates; every response includes `usage.cost` / tool-call details.

### Agent API `web_search` tool options

Recommended context presets:

| `search_context_size` | total/page token budget | Use |
|---|---:|---|
| `low` | 300 / 300 | simple facts, cheap previews |
| `medium` | 1,000 / 1,000 | normal research, comparisons |
| `high` | 4,000 / 4,000 | source-heavy and complex research |

Filters:
- `search_domain_filter`: allow/deny up to 20 domains or URLs; prefix deny entries with `-`.
- `search_recency_filter`: `hour`, `day`, `week`, `month`, `year`.
- `search_after_date_filter` / `search_before_date_filter`: publication date in `MM/DD/YYYY`.
- `last_updated_after_filter` / `last_updated_before_filter`: update date in `MM/DD/YYYY`.
- `user_location`: `country`, `region`, `city`, `latitude`, `longitude`.
- Explicit budgets: `max_tokens`, `max_tokens_per_page`; do not combine explicit budgets with `search_context_size`.

### Direct Agent API examples (requires direct Perplexity API access)

Do **not** assume the local H20 gateway exposes `/v1/agent` or `/search`; verify first. If unavailable through H20, direct upstream `PERPLEXITY_API_KEY` needs explicit Chip approval.

Raw Search API:

```bash
curl -s https://api.perplexity.ai/search \
  -H "Authorization: Bearer $PERPLEXITY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "canonical vendor advisory CVE-2025 high severity fix version",
    "max_results": 10,
    "search_context_size": "high",
    "search_domain_filter": ["cisa.gov", "nvd.nist.gov", "microsoft.com", "github.com"]
  }' | jq
```

Agent API with search + fetch + sandbox:

```bash
curl -s https://api.perplexity.ai/v1/agent \
  -H "Authorization: Bearer $PERPLEXITY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-5.5",
    "input": "Find canonical vendor advisories for these CVEs. Return CVE, vendor advisory URL, product, fixed version, evidence quote.",
    "tools": [
      {"type": "web_search", "search_context_size": "high", "filters": {"search_recency_filter": "year"}},
      {"type": "fetch_url"},
      {"type": "sandbox"}
    ],
    "instructions": "Use sandbox/search as code for fanout, dedupe by canonical vendor domain, fetch advisory pages, and quote evidence. Do not rely on snippets alone."
  }' | jq
```

### Routing rule

- Fast answer / normal search: **Sonar** via H20 gateway.
- Need raw ranked URLs or batch discovery: **Search API**.
- Need deep/wide programmable research, tables, evidence QA, or code-based aggregation: **Agent API + web_search/fetch_url/sandbox (SaC)**.
- High-volume async production enrichment with strict per-request economics can still use **Parallel Task/Search**; compare it against Perplexity Agent API after a live cost/quality smoke.

## Parallel API — integrated companion for heavy workflows

Use Parallel when you need predictable **per-request** economics at scale (enrichment, large batches, deep async tasks).

### Quick pricing anchors (Parallel)

| API | Price | Latency | Best for |
|-----|-------|---------|----------|
| Task API | `$0.005 - $2.4 / request` | `5s - 30min` async | Deep research, enrichment pipelines |
| Search API | `$0.004 - $0.009 / request` | `<3s` sync (base) | Fast ranked URL retrieval |
| Chat API | `$0.005 / request` | `<5s` sync | Web-grounded chat UX |

### Processor ladder (Task API, per 1K requests)

`Lite $5` → `Base $10` → `Core $25` → `Core2x $50` → `Pro $100` → `Ultra $300` → `Ultra2x $600` → `Ultra4x $1200` → `Ultra8x $2400`

### When to pick Parallel vs Perplexity

- **Perplexity Sonar**: ad-hoc queries, analyst-style search, fast answer + citations.
- **Parallel Search/Task**: production pipelines, async deep tasks, strict cost planning, large-scale batch enrichment.

### API shape (Parallel quickstart reference)

Search endpoint pattern:
- `POST https://api.parallel.ai/v1beta/search`
- Headers: `x-api-key: $PARALLEL_API_KEY`, `parallel-beta: search-extract-2025-10-10`

## Response Format

```json
{
  "choices": [{"message": {"content": "Answer with [1][2] citations..."}}],
  "citations": ["https://source1.com", "https://source2.com"],
  "usage": {"cost": {"total_cost": 0.0057}}
}
```

## Credential Rotation / Verification Pattern

On Human20-managed Hermes bots, rotate/verify the Human20 gateway token first (`H20_KEYS_API_KEY` / `H20GW_GATEWAY_KEY`). Do not request or store raw `PERPLEXITY_API_KEY` / `PPLX_API_KEY` on those bots unless Chip explicitly approves a direct upstream fallback.

When Chip explicitly provides a direct Perplexity key and says to use it, treat it as an operator instruction: update the configured env files, verify with a live `sonar` request, and report only paths/status. Do **not** lecture about the key being pasted or “leaked”; Chip prefers execution over security theater unless he explicitly asks for risk analysis.

If a prior search failed because `PERPLEXITY_API_KEY` was missing, immediately persist the supplied key before retrying the search. Preferred minimum durable target is `/home/hermes/.hermes/.env` with mode `600`; also update `/opt/clawd-workspace/.env` only when readable/writable. Never print the key back. Verify with a tiny live `sonar` request that includes `"search_sources": ["social", "web"]`, then rerun the originally requested search/action.

For secrets with shell-sensitive characters, avoid inline `export KEY=...` or command strings that may be logged or parsed oddly. Use a small Python writer or stdin/temp-file handoff, then delete any temp secret file after verification. Build the variable name in code if needed to avoid accidental secret/log masking interfering with the command text.

When a Perplexity key is rotated, verify both files and live processes. Do not stop after editing `.env`: long-running gateways may still hold the old key in `/proc/<pid>/environ` until restart/reload.

Recommended checks:

```bash
OLD='pplx-old...' NEW='pplx-new...'

# Local Hermes
python3 - <<'PY'
from pathlib import Path
old='pplx-old...'; new='pplx-new...'
for p in [Path('/home/hermes/.hermes/.env')]:
    s=p.read_text(errors='ignore') if p.exists() else ''
    print(p, {'old': old in s, 'new': new in s, 'has_var': 'PERPLEXITY_API_KEY' in s})
PY

# Remote OpenClaw/Hermes env files
ssh chip@138.201.30.209 'OLD="$OLD" NEW="$NEW" python3 - <<'"'"'PY'"'"'
from pathlib import Path
import os
old=os.environ['OLD']; new=os.environ['NEW']
files=[
  '/home/chip/.openclaw/gateway.systemd.env',
  '/home/chip/.openclaw/.env',
  '/home/chip/.openclaw/secrets/perplexity.env',
  '/home/chip/.hermes/.env',
  '/opt/clawd-workspace/.env',
]
for p in map(Path, files):
    s=p.read_text(errors='ignore') if p.exists() else ''
    print(str(p), {'old': old in s, 'new': new in s, 'has_var': 'PERPLEXITY_API_KEY' in s})
PY'

# Live OpenClaw process env (chip user systemd)
ssh chip@138.201.30.209 'export OLD="$OLD" NEW="$NEW"; python3 - <<'"'"'PY'"'"'
import os, subprocess
pid=subprocess.check_output(['systemctl','--user','show','openclaw-gateway','-p','MainPID','--value'], env={**os.environ,'XDG_RUNTIME_DIR':'/run/user/1004'}, text=True).strip()
old=os.environ['OLD']; new=os.environ['NEW']
data=open(f'/proc/{pid}/environ','rb').read().decode('utf-8','ignore').split('\0') if pid and pid!='0' else []
print({'pid':pid,'old_in_process':any(old in e for e in data),'new_in_process':any(new in e for e in data),'perplex_vars':[e.split('=',1)[0] for e in data if 'PERPLEXITY' in e or 'PPLX' in e]})
PY'
```

If files are correct but live process still has the old key, restart/reload the gateway rather than re-editing env files.

## Hermes web_search hard rail

For Hermes runtime hardening, use `references/hermes-web-search-backend-hardening.md`: add/verify the bundled `plugins/web/perplexity` provider, wire `web.search_backend: perplexity` in `tools/web_tools.py`, accept `H20_KEYS_API_KEY` / `H20GW_GATEWAY_KEY` for Human20-managed bots and `PERPLEXITY_API_KEY` / `PPLX_API_KEY` only for explicitly approved direct upstream installs, and regression-test that every Perplexity request sends `"search_sources": ["social", "web"]`. This turns Chip's Perplexity preference into a runtime rail instead of a memory-only rule.

### Direct Page Extraction Fallbacks

Hard workflow correction from Chip: when the user provides a concrete URL, open/extract that URL first. Do not start by searching from the description, guessing the project, or asking the search model to infer the link. If the normal page is blocked, try known render/fullpage/raw endpoints, Jina Reader, browser, or curl variants before falling back to broad search. Broad search is only for discovery after the supplied source fails or when extra corroboration is needed.

Use direct extraction before trusting a search-summary answer when the task asks about a specific URL/person/project, paid plan, API endpoint, provider docs, or a user-corrected distinction between similarly branded products. Search models can confidently blend similarly named people/products or invent scale metrics if the target page is not fetched. Example: Kimi Code subscription endpoints differ from Moonshot pay-as-you-go API endpoints; fetch official Kimi Code docs before answering.r when extra corroboration is needed.

Use direct extraction before trusting a search-summary answer when the task asks about a specific URL/person/project, paid plan, API endpoint, provider docs, or a user-corrected distinction between similarly branded products. Search models can confidently blend similarly named people/products or invent scale metrics if the target page is not fetched. Example: Kimi Code subscription endpoints differ from Moonshot pay-as-you-go API endpoints; fetch official Kimi Code docs before answering.

### Concrete URL from Chip = open first, search second

When Chip provides a concrete URL, the first move is to open/extract that URL directly. Do **not** start by searching from the surrounding description, guessing the project, or returning likely alternatives. If the canonical page blocks extraction/browser access, try direct render/export endpoints, raw/page-source variants, or known embed/fullpage routes before falling back to web search. Report the actual source you opened.

CodePen-specific pattern: if `https://codepen.io/<user>/pen/<id>` blocks with Cloudflare/403, try `https://cdpn.io/<user>/fullpage/<id>` and inspect the returned HTML for `<title>`, `rel="canonical"`, script/style sources, and inline `#rendered-js`. Use this to verify title, behavior, dependencies, and whether a GitHub/source link actually exists. The lesson is not “CodePen is broken”; it is “use the fullpage endpoint before guessing from search results.”

### Viral image / quote fact-checks

When fact-checking a screenshot, meme, or alleged quote:
- Transcribe the exact visible claim first (OCR/vision is fine for this), then search multiple exact-phrase variants with quotes.
- Search for the distinctive phrase, the alleged speaker, source branding (e.g. C-SPAN), date/location text, and any watermark/handle separately.
- Treat the underlying photo/video frame and the overlaid caption as separate claims; a real broadcast frame can carry a fabricated caption.
- If an explosive quote has zero matches in reputable news/transcript archives, say so explicitly and mark it as likely fabricated/satirical rather than authentic.
- Report concise evidence: exact searches tried, source/transcript presence or absence, visible manipulation cues (meme typography, watermark, mismatched lower-third/ticker).

### Ambiguous UI/demo/link recovery

When Chip gives a terse description like `Views — галерея... Ссылка 🐱 GitHub`, do not stop at generic libraries or package names. Treat capitalized/common UI words as possibly mistranscribed technical terms and search both the literal phrase and the likely canonical concept.

Pattern:
- Search exact visible fragments first, then English-normalized variants.
- If the clue sounds like browser animation/UI state, try canonical terms such as `View Transitions`, `gallery`, `click directly on an image`, `prev button`, `next button`, `GitHub`.
- Prefer official demos/repos over generic component libraries when the user asks for “Ссылка / GitHub”.
- Fetch the candidate page/repo directly before answering; report confidence if multiple matches remain.

Example durable match: `Views — галерея, где изображения перемещаются при нажатии... Они также двигаются сами` mapped to MDN View Transition API examples: `mdn/dom-examples/tree/main/view-transitions`, whose README says the SPA transition-types gallery moves images with prev/next and direct image click.

### Public celebrity / source-context identification from memes

If Chip asks “who is this” for a meme/photo and the likely target is a public figure, do not blanket-refuse as deanon. Safe path:
- Do **not** identify by biometric face matching or private-person OSINT.
- Do identify through public source context: visible caption, event, clothing, venue, adjacent public figures, article/photo captions, and reputable entertainment/news sources.
- Search exact visible text first, then distinctive context queries: event + outfit + venue + nearby celebrity names if visible.
- Verify with at least one public article/image caption that names the person and matches the non-face details.
- Answer directly with confidence and sources; if evidence is only similarity-by-face, say that and stop.

Example pattern: meme caption `VC, CEO, CMO, CTO` + Knicks courtside + orange Chrome Hearts denim/white tank led to public reports naming Kylie Jenner with Timothée Chalamet at Knicks Game 4. The durable lesson is the source-context workflow, not face recognition.

### Pricing / tariff research

When the user asks for prices, tariffs, hosting/VPS costs, cloud calculators, or says “not estimates / exact prices”, do **not** answer from Perplexity prose or third-party rankings. Fetch official vendor pages/APIs and extract concrete numbers. If a page is JS/Nuxt/Next-backed, inspect embedded state files and public pricing APIs before falling back to ranges. See `references/pricing-extraction.md` for the proven extraction pattern and examples.

For screenshot/social-commerce price requests, use a two-pass pattern:
1. Identify the exact visible product/brand/model from the screenshot first with vision/OCR.
2. Use Perplexity only to discover candidate official/vendor pages.
3. Fetch the candidate pages directly and extract `<title>`, meta description, product text, and ruble/price lines yourself; search models often miss prices that are only in page titles or product templates.
4. If the user asks “with installation/montage” and the vendor only lists product + delivery, explicitly separate: product price, delivery status, installation not listed. Then ground the installed-total estimate in separate installer price pages (e.g. монтаж печи, основание, слив, подвод воды) rather than blending it into the official product price.
5. Return a compact “price now / installed range / caveats” answer, not a research dump.

### Person / panel-participant research

When enriching named people for moderator prep, speaker briefs, bios, or due diligence:
- Search each person separately after the combined query; combined searches often overfit to the user's framing and blur similarly named people.
- Separate **verified public facts** from **role inference** and **moderation angles**.
- For pseudonymous/media personalities, say that the biography is less formalized instead of inventing credentials.
- Do not repeat self-reported returns or marketing claims as facts; label them as claims unless independently verified.
- Prefer concise, usable notes: public profile → role in discussion → what to probe → how to interrupt/productively redirect.

### Media outlet provenance / “whose money, whose insiders”

When Chip asks why a publication keeps appearing in news, who founded it, whose money backs it, or where the insiders come from, do not stop at “it is a news site.” Build a compact provenance read:

- **Founders / prior network**: identify founders, previous newsroom/company, signature product, and why that network creates access (e.g. Politico → Playbook → Axios).
- **Funding / ownership**: separate early investors, strategic/media partners, acquisition/owner, and whether founders retained operating control.
- **Access model**: explain what kind of sources the outlet is structurally close to: White House aides, Hill staffers, lobbyists, CEOs, donors, campaign operatives, foreign-policy circles, etc.
- **Business model / incentive**: note newsletter/native ads/sponsored briefings/subscription/event economics when relevant.
- **Reading stance**: classify the outlet as investigative, wire-service, access journalism, partisan, trade press, or elite briefing. For access journalism, say it clearly: often useful as an indicator of what insiders want the target audience to know, not final truth.
- **Verification move**: cross-check hot claims against Reuters/AP/FT/NYT/WaPo/local primary sources and ask “who benefits from this leak?”

Answer shape Chip likes: `кто основал → откуда инсайды → чьи деньги → как читать`. Keep it short and opinionated; avoid a bland media-history dump.

### Username / handle OSINT

When the target starts from a username, Telegram profile screenshot, handle, or “search all sources” request, use the Maigret + triangulation workflow in `references/username-osint-maigret.md`.

Key rule: Maigret hits are candidate accounts, not identity proof. Treat same-handle profiles as LOW confidence until bridged by real name, avatar, bio, linked URLs, phone/ID, email hash, or project references. Report confidence tiers and explicitly call out likely collisions.

### Tilda / 403 / JS-heavy pages

If `curl`/requests returns 403 or a sparse JS shell, try Jina Reader:

```bash
python3 - <<'PY'
import requests
url='https://r.jina.ai/http://https://example.com/path'
r=requests.get(url, timeout=30, headers={'User-Agent':'Mozilla/5.0'})
print(r.status_code, len(r.text))
print(r.text[:8000])
PY
```

Pattern:
- `https://r.jina.ai/http://https://TARGET_URL` works well for Tilda pages that block direct server fetches.
- Fetch the homepage/about/related pages too (`/`, `/about`, `/hackaton`, etc.) when building an executive summary.
- Treat site claims as self-claims; separate them from third-party confirmation.
- If Perplexity returns facts about the wrong namesake, discard and ground the summary in extracted target-page text + reputable third-party pages.

## Browser Relay (Backup — Free)

Only use when API key is broken or for complex interactive research.

1. `browser action=tabs profile=chrome` → find Perplexity tab
2. Click "New Thread", type query, Enter
3. Wait 12-15s, snapshot results

## Key Rules

- **ALWAYS** include `"search_sources": ["social", "web"]` for Perplexity requests
- Default model: `sonar`
- Use `sonar-pro` only for deep research when asked
- For **high-volume/async pipelines** prefer Parallel Task/Search APIs
- API is preferred over browser relay (faster, more reliable)
