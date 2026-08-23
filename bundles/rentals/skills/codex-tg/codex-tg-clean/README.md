# chip-codex-tg-public

Публичный skill и runbook для Telegram Codex cockpit на базе OpenClaw + Codex App Server.

## Что это
Это не "просто бот в Telegram". Это рабочий cockpit:
- отдельный чат
- topics вместо каши
- отдельный agent
- `/cas_*` как control surface
- после bind можно писать обычным текстом

## Для кого
Для тех, кто хочет работать с Codex из Telegram нормально, а не держать всё в одной бесконечной личке.

## Что внутри
- `SKILL.md` — когда и как использовать skill
- `references/onboarding-ru.md` — понятная инструкция для ежедневной работы
- `references/runbook.md` — как поднять и поддерживать cockpit
- `references/public-private-split.md` — что должно оставаться приватным

## Главное правило UX
**Один topic = один workstream.**

Если мешать несколько задач в одной ветке, cockpit быстро превращается в шум.

## Plugin submodule
- `references/openclaw-codex-app-server` — pinned public submodule to `https://github.com/pwrdrvr/openclaw-codex-app-server.git`
- используется как implementation reference для `/cas_*`, binding и bridge-layer

## Что мы реально улучшили
Этот репозиторий не просто пересказывает исходную идею Telegram+Codex, а собирает более практичную версию cockpit для живой работы.

### 1) Нормализовали саму модель cockpit
Вместо расплывчатого «бота для Codex в Telegram» здесь зафиксирован рабочий контракт:
- отдельный supergroup,
- topics как основной интерфейс,
- один topic = один workstream,
- `Codex Control` как control surface,
- после bind можно писать обычным текстом.

Это убирает главный дефект «одной бесконечной лички»: смешивание разных задач, статусов и контекстов в одном потоке.

### 2) Сделали skill ориентированным на реальный operator workflow
В исходных материалах обычно не хватает слоя между plugin-кодом и реальной эксплуатацией.

Здесь добавлены:
- понятный onboarding,
- runbook для поднятия и сопровождения,
- quick/manual checklists,
- public/private split,
- media ingress contract.

То есть это уже не просто implementation repo, а полноценный operational shell вокруг него.

### 3) Уточнили media ingress contract
Одна из самых важных практических доработок — явный контракт для media ingress:
- voice notes должны превращаться в transcript для Codex,
- inbound files должны доходить до Codex как local path/context,
- а не требовать обязательной обратной пересылки через Telegram.

Это делает cockpit ближе к реальной рабочей среде на сервере, а не к демо-сценарию с пересылкой файлов туда-сюда.

### 4) Вытащили operator knowledge из головы в документацию
В реальном использовании всплывают не только команды, но и footguns:
- как не сломать bindings,
- как проверять `/cas_*`,
- как понимать topic-per-workstream,
- что должно оставаться приватным,
- где проходит граница между skill-layer и plugin-layer.

Эта версия делает такие вещи явными, а не оставляет их «неявным знанием автора».

### 5) Разделили cockpit shell и plugin implementation
Здесь явно зафиксировано разделение ролей:
- этот repo = публичный cockpit shell, docs, onboarding, runbook;
- `openclaw-codex-app-server` = implementation dependency.

Это лучше, чем смешивать всё в один слой, потому что:
- проще развивать UX и docs отдельно,
- проще обновлять plugin как зависимость,
- меньше дрейф между operator docs и implementation reality.

## Почему эта версия лучше исходного контура
Короче: она не обязательно «умнее кодом», но она **сильнее как рабочий продукт**.

### Better because
- она объясняет не только **что это**, но и **как этим реально пользоваться каждый день**;
- она оформляет cockpit как систему работы, а не как набор разрозненных команд;
- она документирует operator contract, а не только implementation details;
- она лучше готова к повторному развёртыванию на другом сервере/аккаунте;
- она чище отделяет public knowledge от private/live-specific деталей.

## Какие практические фичи мы добавили поверх исходного подхода
Ниже — список улучшений, которые особенно важны для реальной эксплуатации:
- topic-per-workstream UX вместо хаотичного single-thread usage;
- явный control-topic (`Codex Control`) как точка bind/status/resume;
- русскоязычный onboarding для ежедневного использования;
- operator runbook вместо «смотри код и разбирайся сам»;
- отдельный public/private split, чтобы public repo не тащил live-секреты;
- media ingress contract для voice notes и inbound files;
- submodule-связь с `openclaw-codex-app-server`, чтобы implementation reference был pinned и прозрачный.

## Важно
Этот public repo специально очищен от live/private привязок.

Здесь нет:
- реальных chat IDs,
- персональных agent IDs из боевого контура,
- локальных runtime paths конкретного сервера,
- приватных операторских деталей, которые относятся только к одному live setup.

Именно поэтому его можно держать публичным, не утаскивая с собой внутренний cockpit state.
