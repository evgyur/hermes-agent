---
name: telegram-chip-runtime-api
description: Run and operate FastAPI layer for telegram-chip.
---

# runtime-api

## Goal
Надёжно поднять `api.py` + `telegram_core.py` как единый Telegram API слой.

## Steps
1. Проверить env (только через `.env`, без хардкода).
2. Запустить API процесс (`uvicorn api:app ...` или docker-compose).
3. Проверить `/health` + базовые read/write endpoints.
4. Снять краткий статус ошибок/лимитов.

## Done Criteria
- API process alive
- health/check endpoints OK
- нет auth/session критических ошибок в логах
