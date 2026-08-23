---
name: telegram-chip-tgdl-export
description: Export Telegram messages/media safely via telegram-chip.
---

# tgdl-export

## Goal
Сделать выгрузку сообщений/медиа без утечки секретов.

## Steps
1. Проверить входные параметры (`chat_id`, `limit`, `out_path`).
2. Запустить экспорт через API `tgdl/export` или core helper.
3. Проверить, что файл создан и JSON валиден.
4. Убедиться, что в выдаче нет токенов/сессий.

## Done Criteria
- экспорт завершён без исключений
- артефакт существует в ожидаемом пути
- содержимое пригодно для downstream-анализа
