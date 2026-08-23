---
name: telegram-chip-mcp-stdio
description: Operate stdio MCP interface from main.py.
---

# mcp-stdio

## Goal
Поднять и проверить MCP-интерфейс telegram-chip (stdio transport).

## Steps
1. Проверить зависимости и env.
2. Запустить `python main.py` в изолированном процессе.
3. Проверить доступность основных tools (`get_chats`, `send_message`, `search_messages`).
4. Зафиксировать ограничения по rate-limit.

## Done Criteria
- процесс запускается без traceback
- tools отвечают корректно
- лог не содержит критических auth ошибок
