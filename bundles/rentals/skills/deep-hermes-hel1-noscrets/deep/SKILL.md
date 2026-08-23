---
name: deep
description: "Run analyst-grade deep research through Parallel Task API for open-ended web intelligence questions. Use for /deep, deep research, competitive/technical/vendor landscape research, source-backed reports, and when normal search is too shallow."
metadata:
  clawdbot:
    emoji: 🧠
    command: /deep
  hermes:
    tags: [research, web, parallel, deep-research, citations]
    related_skills: [perplex, par, last30days]
---

# /deep — Parallel Task API Deep Research

Запускает глубокий ресёрч через Parallel Task API: многошаговый веб-поиск, синтез, markdown-отчёт, цитаты и raw JSON. Это не обычный быстрый поиск; использовать, когда нужен настоящий research memo с источниками и выводами.

## Trigger

Используй этот skill, когда пользователь:

- пишет `/deep ...`;
- просит «deep research», «глубокий ресёрч», «сравни лучшие open-source решения», «найди рынок/ландшафт/экосистему»;
- хочет source-backed report по технической, рыночной или конкурентной теме;
- просит обновить или запустить Parallel Task API deep research.

Не используй для простого факта, одного URL, текущей цены или быстрой справки — там хватит обычного web/search.

## Quick Start

```bash
~/.hermes/skills/deep/scripts/parallel_deep.py \
  "Research question" \
  --processor ultra \
  --schema text \
  --timeout 3600 \
  --output /tmp/deep_report.md
```

Slash-compatible legacy shape, если нужен старый путь:

```bash
/opt/clawd-workspace/scripts/deep.sh "research question" pro 1800
```

Но canonical Hermes runner теперь: `~/.hermes/skills/deep/scripts/parallel_deep.py`.

## Requirements

- `PARALLEL_API_KEY` в `~/.hermes/.env` или environment.
- Не печатать ключ в ответах, логах, markdown-отчётах или skill content.
- Prompt держать до **15,000 chars**: Parallel явно оптимизирует Deep Research под компактные research prompts.

## Processor Policy

- `ultra` — default для важных founder/technical decisions, landscape research, vendor/tool selection.
- `ultra-fast` — когда нужен хороший отчёт быстрее.
- `pro` — нормальный default для средних задач.
- `pro-fast` — интерактивные быстрые исследования.
- Follow-up по `previous_interaction_id` можно делать дешевле/быстрее, потому что контекст уже собран.

Deep Research может идти до **45 минут**. Для Telegram/операторских задач ставь timeout 3600 и сохраняй артефакты в файл.

## Workflow

1. Переформулируй вопрос в research prompt: цель, scope, must-cover, exclusion criteria, output schema.
2. Если тема про software/open-source, явно проси:
   - official docs/repos;
   - GitHub activity/license/maintenance;
   - examples/SDK/API coverage;
   - integration risk;
   - recommended shortlist;
   - citations with URLs.
3. Для agent-constitution / SOUL.md / AGENTS.md / CLAUDE.md / Cursor rules research используй `references/agent-constitution-soulmd.md`: фокусируйся на layer separation, precedence, injection resistance, proof standards, privacy, anti-patterns, а не на persona-writing.
3a. Для Hermes hooks / middleware / gateway lifecycle research используй `references/hermes-hooks-research-caveats.md`: сверяй exact config shape с official docs и live `hermes hooks ...`, отделяй official/source evidence от community/blog examples, и явно предупреждай про fail-open guardrails + необходимость canary/audit hooks.
4. Запусти helper script.
5. Сохрани markdown + raw JSON.
6. Прочитай результат, сделай operator synthesis на русском: что выбрать, почему, риски, следующий шаг.
7. Если `/deep` был про практическую интеграцию/бота/API/workflow, не останавливайся на отчёте. Найди загруженный umbrella-skill, который управляет этим классом задач, и добавь туда минимум один executable или reusable artifact: `scripts/<helper>.py`, `references/<implementation-contract>.md` или patch существующего helper. Затем реально прогони happy-path команду и включи в ответ результат проверки. Пользовательская коррекция из маршрутов: research без runnable script считается недоделанным.
8. Проверь качество источников перед уверенным выводом: отдели official docs / papers / OWASP / primary sources от слабых блогов, future-dated claims, сомнительных CVE/model-limit цифр. Не повторяй сомнительное как факт; явно дай caveat. Для legal/tax research deep-отчёт — это исследовательская опора, а не финальная инструкция: если отчёт предлагает серое действие без прямого официального источника (например, разделить один платёж между разными налоговыми режимами), пометь confidence как средний/низкий и в синтезе предпочти безопасный операционный вариант.
8a. Для аудита Hermes memory/skills не принимай `/deep` вывод как автоматическое решение: сверяй с официальными Hermes docs и локальными user-corrections. Явные коррекции Chip (например, “URL first: open/extract the supplied link before search/guess”) могут быть durable USER-profile pet peeves даже если generic research классифицирует их как workflow.
8. Проверь качество источников перед уверенным выводом: отдели official docs / papers / OWASP / primary sources от слабых блогов, future-dated claims, сомнительных CVE/model-limit цифр. Не повторяй сомнительное как факт; явно дай caveat. Для legal/tax research deep-отчёт — это исследовательская опора, а не финальная инструкция: если отчёт предлагает серое действие без прямого официального источника (например, разделить один платёж между разными налоговыми режимами), пометь confidence как средний/низкий и в синтезе предпочти безопасный операционный вариант.
8a. Для аудита Hermes memory/skills не принимай `/deep` вывод как автоматическое решение: сверяй с официальными Hermes docs и локальными user-corrections. Явные коррекции Chip (например, “URL first: open/extract the supplied link before search/guess”) могут быть durable USER-profile pet peeves даже если generic research классифицирует их как workflow.
9. Если пользователь явно исправил scope (“not X”, “не про X”), внеси исключение прямо в prompt и в финальную рамку. Не позволяй deep report схлопнуться в соседнюю более документированную тему.
8. Если пользователь явно исправил scope (“not X”, “не про X”), внеси исключение прямо в prompt и в финальную рамку. Не позволяй deep report схлопнуться в соседнюю более документированную тему.
9. Если локальный foreground timeout/gateway interruption случился после печати `RUN_ID`, не считай задачу проваленной: remote Parallel run может продолжать выполняться. Полли существующий run/result или запусти узкий `pro-fast` follow-up, затем верифицируй ключевые claims targeted search/extract.
10. Если deep runner завис/таймаутнулся **до печати `RUN_ID`** или не даёт stdout из-за сетевого/API-hang, не блокируй практический ответ бесконечным ожиданием. Убей/останови локальный процесс, сделай targeted web/search/extract по ключевым claims, честно укажи caveat `Parallel не выдал run_id`, и верни grounded operator synthesis по доступным источникам. Для legal/tax/product decisions это предпочтительнее пустого ожидания.
11. Если Parallel вернул `HTTP 402 Payment Required` / insufficient credit, не ретрай бесконечно и не называй это выполненным `/deep`. Скажи `Parallel /deep заблокирован биллингом`, сохрани/покажи prompt при необходимости, затем сделай узкий fallback по первичным источникам обычными web/browser/terminal-инструментами. Если пользователю нужен именно Parallel-отчёт, следующий шаг — пополнить Parallel billing; если нужна практическая рекомендация сейчас, отдавай grounded synthesis с caveat.
10. Если отчёт слабый/широкий — запусти follow-up через `previous_interaction_id` или обычный targeted search по пробелам.

Подробный resilient pattern: `references/resilient-deep-research.md`.

## Output Contract

Для финального ответа пользователю верни:

1. `статус` — completed/failed/timeout + run_id без секретов.
2. `артефакты` — markdown report path/file и raw JSON path, если полезно.
3. `короткий вывод` — 3–7 строк на русском, не пересказ всего отчёта.
4. `рекомендация` — лучший вариант/shortlist/следующий шаг.
5. `evidence caveat` — если Parallel не нашёл исходники, были слабые источники или нужен follow-up.

Для больших отчётов прикрепляй файл через `MEDIA:/absolute/path`.

## Deep Research Prompt Template

```text
You are doing analyst-grade deep research for an operator who will make a build/buy/open-source decision.

Topic: <topic>

Goal:
- Find the best current open-source / public tooling, SDKs, repos, docs, examples, and production patterns.
- Separate official protocol/vendor resources from community tools.
- Prioritize tools that are maintained, usable by developers, and relevant to <specific use case>.

Must cover:
1. Official docs, SDKs, APIs, repos, examples.
2. Top GitHub repos/projects with URL, language, license, last activity, stars if available, and what they actually do.
3. Which repos are production-ready vs examples/toys/stale.
4. Integration map: data fetching, pool discovery, position management, swaps/routing, analytics/backtesting, monitoring, execution risk.
5. Gaps: what likely must be built custom.
6. Recommended shortlist and implementation path.
7. Inline citations/source URLs for every material claim.

Output:
- Markdown report.
- Start with executive verdict.
- Then ranked table/list of tools.
- Then architecture recommendation.
- Then risks and next steps.
```

## Quick Test Checklist

- [ ] `skill_view('deep')` loads SKILL.md and lists `scripts/parallel_deep.py` + `references/parallel-task-api.md`.
- [ ] Helper exits cleanly with a clear error if `PARALLEL_API_KEY` is missing.
- [ ] Helper rejects prompts over 15k chars.
- [ ] Helper can create a run and save markdown + raw JSON without printing secrets.
- [ ] Final user response includes artifacts, short synthesis, recommendation, and caveats.

## Done Criteria

- [ ] Research prompt is specific enough for Deep Research, not a vague search query.
- [ ] Uses `text` schema for human reports unless structured JSON is explicitly needed.
- [ ] Saves raw JSON for citations/evidence recovery.
- [ ] Does not expose API key.
- [ ] For technical/tooling research, covers official docs, repos, maintenance, license, integration fit, and build gaps.
- [ ] If the result is shallow, runs a targeted follow-up before presenting a confident conclusion.

## Boundary: не путать со Scout deep-dive

Если речь про кнопку/команду GoClaw Inbox/Scout `deep-dive` по найденному GitHub-репозиторию, это не Parallel `/deep`. Там пользователь ждёт немедленный структурированный repo report, а не async research queue. Загрузи `repo-radar` и, для памяти/Scout runtime, `chip-memory-stack`; применяй контракт: inline metadata+README fetch, artifact save, Telegram sections ➊-➏.

## References

- [Parallel Task API contract](references/parallel-task-api.md)
- [Agent constitution / SOUL.md research pattern](references/agent-constitution-soulmd.md)
- [Hermes hooks research caveats](references/hermes-hooks-research-caveats.md)
- [Manual create/poll fallback](references/parallel-manual-create-poll-fallback.md) — split Parallel task creation from polling when foreground timeout may hide `RUN_ID`; save `run.json`, then poll/result-fetch separately.
- [Runner script](scripts/parallel_deep.py)
