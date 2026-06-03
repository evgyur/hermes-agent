# Human20 Helper Skill

MVP helper для Human20.

Source of truth теперь находится прямо в `skills/human20-helper/`.
Старый каталог `ai-projects/human20-helper/` не должен считаться runtime-источником для skill.

## Что делает текущая версия

- читает структуру и guidance из Human20 через direct MCP session flow;
- не зависит от проблемного OpenClaw MCP bridge;
- ищет и рекомендует Human20-скилы по задаче пользователя;
- локально сверяет прохождение уроков по workspace/config/memory/project evidence;
- определяет следующий непройденный или не подтверждённый этап;
- возвращает ссылку на урок и practical next step;
- умеет делать continuation по конкретному уроку;
- умеет выбирать режим по простому текстовому запросу;
- умеет test-only trainer/orchestrator fallback для последовательного lesson flow.

## Конфиг

Поддерживаются 2 способа:

1. Через переменные окружения.
2. Через локальный `.env` рядом со скриптами, то есть в корне skill-проекта `skills/human20-helper/.env`.

Используются переменные:

- `HUMAN20_BEARER_TOKEN`
- `HUMAN20_MCP_URL` (optional, по умолчанию `https://human20.app/mcp`)

`HUMAN20_BEARER_TOKEN` can be either the raw token from the Human20 profile or `Bearer <token>`.
The helper normalizes both forms before sending requests.

```powershell
git clone https://github.com/evgyur/human20-helper.git
cd human20-helper
Copy-Item .env.example .env
```

## Commands

Direct MCP client:

```bash
python3 skills/human20-helper/scripts/human20_mcp_client.py tools/list
python3 skills/human20-helper/scripts/human20_mcp_client.py tools/call --tool get_workshop
python3 skills/human20-helper/scripts/human20_mcp_client.py tools/call --tool get_progress
```

Local evidence audit:

```bash
python3 skills/human20-helper/scripts/local_evidence.py
```

Human-readable summary:

```bash
python3 skills/human20-helper/scripts/helper_flow.py --mode human
```

What changed since date:

```bash
python3 skills/human20-helper/scripts/helper_flow.py --mode changed-since --since 2026-04-01T00:00:00Z
```

Continuation for one lesson:

```bash
python3 skills/human20-helper/scripts/helper_flow.py --mode continue --lesson lesson-4
```

Test-only trainer/orchestrator mode:

```bash
python3 skills/human20-helper/scripts/helper_flow.py --mode test-trainer
```

Smart entrypoint:

```bash
python3 skills/human20-helper/scripts/entrypoint.py "где я сейчас"
python3 skills/human20-helper/scripts/entrypoint.py "что нового"
python3 skills/human20-helper/scripts/entrypoint.py "урок 4"
python3 skills/human20-helper/scripts/entrypoint.py "тестовый режим"
```

Старые технические команды тоже поддерживаются:

```bash
python3 skills/human20-helper/scripts/entrypoint.py status
python3 skills/human20-helper/scripts/entrypoint.py where-am-i --user-id tg:123
python3 skills/human20-helper/scripts/entrypoint.py skill-search "telegram digest"
python3 skills/human20-helper/scripts/entrypoint.py skill-recommend "какой скил подойдёт для Telegram канала" --human
python3 skills/human20-helper/scripts/entrypoint.py chat-search "openclaw"
python3 skills/human20-helper/scripts/entrypoint.py lesson-context lesson-1 --user-id tg:123
```

## Skill recommendations

Для вопросов вроде `какой скил мне подойдёт`, `подбери скил для Telegram`,
`посоветуй навык для дайджеста` entrypoint теперь сразу идёт в Human20 MCP:

1. вызывает `recommend_human20_skills`;
2. если точная рекомендация пустая, делает fallback-поиск по каталогу через `get_human20_skills_catalog`;
3. возвращает название, slug, объяснение, страницу на human20.app, ZIP и GitHub, если они есть.

Пример:

```bash
python3 skills/human20-helper/scripts/entrypoint.py "какой скил мне подойдёт для Telegram канала"
```

## Homework sync

Human20 MCP now exposes `get_homework_catalog`, which returns the canonical task catalog for a lesson:

- `task_id`
- `label`
- `description`
- `group`
- `completed`

Helper write-back still stays guarded:

- read live state first;
- verify local evidence;
- write only when confidence is high;
- verify live state after write;
- block write-back if live task ids contradict expected lesson tasks.

## Ограничения текущей фазы

- guided progression уже поднят в runtime, но это ещё не полноценный beginner-first course companion;
- test-only trainer mode не пишет в Human20 и нужен только как безопасная симуляция;
- evidence engine пока опирается на фиксированные локальные признаки и ещё требует дальнейшего усиления.

## Safety

- Read-only by default for discovery commands.
- Push messages must go through backend-owned MCP tools: `preview_user_message` first, then `send_user_message`.
- Do not put bearer tokens, Telegram bot tokens, Supabase keys, or user exports into this repo.
