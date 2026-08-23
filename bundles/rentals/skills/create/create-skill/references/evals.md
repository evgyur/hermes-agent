# create-skill evals and benchmarking

## Goal
Не просто собрать skill, а показать, что он стал полезнее, понятнее или надёжнее.

## Default policy
- Для проверяемых workflow-skills eval-loop включён по умолчанию.
- Для субъективных skills допускается `review-only`, но это должно быть названо явно.
- Для `compare` без изменений iteration loop не обязателен, новый `SKILL.md` не нужен, но evidence обязателен.
- Для `compare` default benchmark mode = `old/new`; `review-only` использовать только если нет надёжного baseline.
- Если пользователь прямо просит обойтись без eval/benchmark, зафиксируй это как conscious waiver.

## Benchmark modes

### `old/new`
Используй для `refactor`, `compare`, или улучшения существующего skill.

Подходит когда:
- уже есть текущая версия skill
- важно понять, стало ли лучше, а не просто "красивее"
- есть риск сломать triggering или внешний интерфейс

### `with/without`
Используй для `extract` или `interview`, когда создаётся новый skill с проверяемым workflow.

Подходит когда:
- можно сравнить исполнение с новым skill и без него
- задача имеет наблюдаемый результат
- сравнение окупается по времени

### `review-only`
Используй когда benchmark честно не окупается.

Подходит и для `compare`, если у тебя нет надёжного baseline, а пользователю нужен честный verdict без имитации точности.

Подходит когда:
- результат в основном субъективный
- сравнение слишком дорогое или искусственное
- важнее экспертный review, чем baseline

## Minimal eval loop
1. Сформируй 2-3 realistic test prompts.
2. Для каждого кратко опиши ожидаемое поведение.
3. Выбери benchmark mode.
4. Сделай первый review.
5. Перепиши skill хотя бы один раз, если review нашёл проблему.
6. Коротко зафиксируй, что именно улучшилось.

Для recurring digest / messy-conversation extract:
- по умолчанию часто честнее `review-only`, если нет хорошего baseline
- отдельно проверь cadence, sources, sections, owner steps, deadlines, done criteria

## What to review
- trigger quality
- clarity of instructions
- completeness of output contract
- excessive rigidity vs useful freedom
- backward compatibility for refactors

## Minimal handoff note
Укажи:
- benchmark mode
- why chosen
- what failed or looked weak in the first draft
- what changed after rewrite
