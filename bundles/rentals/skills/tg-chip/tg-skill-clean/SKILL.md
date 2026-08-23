---
name: telegram-chip
description: Router skill for unified Telegram core (HTTP API, MCP stdio, tgdl export, auth/session ops).
metadata: {"clawdbot": {"always": false}}
---

# /telegram-chip

Router для operational-задач по telegram-chip.

## Contract

**Primary class:** Router
**Secondary class:** Verifier

**Use when:** the task is about telegram-chip runtime, HTTP API, MCP stdio, export flows, or auth/session operational issues.

**Do not use when:** the task is ordinary message sending through OpenClaw tools and does not require telegram-chip runtime work.

**Verification gate:** do not claim recovery, health, or successful export without naming the proof path (health endpoint, tool list, logs, written export artifact, etc.).

## Когда использовать
- поднять/проверить HTTP API (`api.py`, `telegram_core.py`)
- работать через MCP stdio (`main.py`)
- выгружать историю чатов (tgdl/export)
- чинить `AUTH_KEY_DUPLICATED`, FloodWait, session-конфликты

## Router Map
- runtime API/daemon -> `modules/runtime-api/SKILL.md`
- MCP stdio flow -> `modules/mcp-stdio/SKILL.md`
- exports/tgdl -> `modules/tgdl-export/SKILL.md`
- auth/rate-limit incidents -> `modules/ops-hardening/SKILL.md`

## Output Contract (обязательный)
При выполнении задачи вернуть:
1) что именно запускалось/менялось
2) какие файлы затронуты
3) результат проверки (команда + краткий вывод)
4) риски/ограничения
5) следующий шаг (если нужен)

## Claim wording rule

Prefer:
- “API health endpoint отвечает, recovery подтверждён …”
- “stdio tool list грузится, auth error не воспроизводится …”
- “export создан по пути …”

Avoid:
- “всё ок” без проверки
- “починил” без post-fix evidence
- “бот жив” только по process state без user-facing/runtime check

## Quick Test Checklist
- [ ] `python3 -m py_compile api.py telegram_core.py main.py`
- [ ] если API режим: health endpoint отвечает без 5xx
- [ ] если stdio режим: tool list грузится без auth errors
- [ ] в логах нет `AUTH_KEY_DUPLICATED` после старта
- [ ] export path создаётся и файл пишется

## References
- business / GTM context for `@chipsclawbot`: `references/chipsclawbot-edgelab-gtm-2026-03-27.md`

## Manual Review Checklist
- [ ] в коммит/архив не попали `.env`, session string, реальные токены
- [ ] нет захардкоженных chat id / user id из прод-чатов
- [ ] совместимость старых запусков сохранена
- [ ] README/операционные шаги соответствуют реальному runtime

## Backward-Compat Map
- legacy `/telegram-chip` -> этот router (без изменения команды)
- legacy docs from old SKILL.md -> `references/legacy-SKILL.md`
- существующие скрипты (`main.py`, `api.py`, `session_string_generator.py`) оставлены без переименования
