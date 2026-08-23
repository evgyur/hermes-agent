---
name: telegram-chip-ops-hardening
description: Resolve AUTH_KEY_DUPLICATED, FloodWait, and session/rate-limit incidents.
---

# ops-hardening

## Goal
Починить инциденты auth/session/rate-limit без потери доступа.

## Steps
1. Идентифицировать тип инцидента (auth key duplicate / flood / session invalid).
2. Остановить конкурирующие процессы с тем же session string.
3. При необходимости перевыпустить session string и обновить env.
4. Перезапустить один источник истины и проверить логи.

## Done Criteria
- ошибка не повторяется после рестарта
- один активный процесс использует session
- отправка/чтение сообщений снова работают
