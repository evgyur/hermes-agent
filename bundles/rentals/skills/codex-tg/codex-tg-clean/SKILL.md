---
name: chip-codex-tg-public
description: "Builds or maintains a Telegram-based Codex cockpit: a dedicated supergroup with topics, a bound OpenClaw agent, working /cas_* commands, and a clean topic-per-workstream workflow. Use when someone wants a better Telegram interface for Codex than a single DM thread."
---

# chip-codex-tg-public

Скилл для настройки и обслуживания Telegram-чата, где **один topic = один поток работы с Codex**.

## Когда использовать
Используй этот скилл, когда пользователь хочет:
- отдельный Telegram-чат под Codex
- topics / ветки вместо хаоса в одной личке
- выделенный agent под Telegram cockpit
- рабочие `/cas_*` команды внутри Telegram topics
- понятный онбординг для реального использования, а не только техдок
- воспроизводимый публичный runbook без приватных привязок

## Что делает скилл
1. Создаёт или обслуживает Telegram supergroup под Codex
2. Включает topics
3. Добавляет Telegram-бота и поднимает его в admin
4. Создаёт базовую структуру тем
5. Привязывает чат к отдельному OpenClaw agent
6. Проверяет `/cas_*` команды и inline-кнопки
7. Даёт понятный онбординг для ежедневной работы

## Базовая структура topics
По умолчанию использовать такую схему:
- Inbox / Triage
- Codex Control
- Runtime / Ops
- Product / Build
- Research / Scratch

Если у пользователя уже есть своя проектная структура — адаптировать, а не навязывать эту сетку.

## Правила работы
- **Один topic = один workstream**
- Не мешать несколько независимых задач в одной ветке
- `Codex Control` держать для bind/status/control
- Проектную работу вести в профильных topics
- В активной задаче cockpit не должен молчать дольше 150 секунд
- Если run ещё идёт, в тот же topic нужен короткий progress update
- Если два окна heartbeat подряд нет видимого результата, cockpit должен считать run зависшим и сделать automatic nudge активного Codex run
- Если Codex закончил, задал вопрос или упёрся в blocker, это нужно немедленно relay-ить обратно в cockpit topic, а не оставлять во внутреннем состоянии рантайма
- При правках конфига помнить, что массивы вроде `bindings` могут перезаписываться целиком
- Media ingress базово должен работать так: voice notes превращаются в transcript для Codex, а inbound files прокидываются в Codex как local server path/context, а не как обязательный Telegram outbound attachment

## Быстрый сценарий для пользователя
1. Зайти в `Codex Control`
2. Отправить `/cas_resume`
3. Выбрать existing thread или `New`
4. После bind писать уже обычным текстом
5. Для новой отдельной задачи уходить в профильный topic

## Что проверить после настройки
1. Чат существует и topics включены
2. Бот — admin
3. Отдельный agent привязан к этому чату
4. `/cas_resume` и `/cas_status` отвечают
5. Inline-кнопки работают в topics
6. Voice notes в bound topics доходят до Codex как transcript
7. Inbound files в bound topics доходят до Codex как local file path/context
8. Через 150 секунд длинного run в topic появляется progress heartbeat
9. Если следующий heartbeat-цикл тоже пустой, cockpit делает visible stall notice и auto-nudge
10. Финальный ответ, вопрос к пользователю и blocker не теряются и сразу relay-ятся в topic
11. Онбординг не врёт о реальном UX

## References
- [Публичный онбординг на русском](references/onboarding-ru.md)
- [Публичный runbook](references/runbook.md)
- [Quick test checklist](references/quick-test-checklist.md)
- [Manual review checklist](references/manual-review-checklist.md)
- [Public/private split notes](references/public-private-split.md)
- [Media ingress contract](references/media-ingress-contract.md)

## Output contract
Когда этот скилл используется, вернуть:
1. структура cockpit
2. как пользователь начинает работу
3. какие `/cas_*` команды нужны ежедневно
4. как устроен heartbeat / stall-recovery contract
5. какие конфиг-риски есть
6. что осталось operator-specific и не должно утекать в public
