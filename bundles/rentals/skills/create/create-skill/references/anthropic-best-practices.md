# Anthropic-aligned checklist for skill authoring

Source baseline: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices

## 1) Keep SKILL.md concise
- SKILL.md — только route + workflow + contract.
- Детали, примеры, справочники — в `references/*.md`.
- Не дублируй очевидное (модель уже это знает).

## 2) Description drives discovery
- Писать в 3-м лице.
- Указать: что делает + когда применять + ключевые триггеры.
- Избегать расплывчатых описаний типа "helper"/"utils".

## 3) Pick degree of freedom consciously
- High freedom: когда допустимо несколько подходов.
- Medium: когда нужен шаблон, но есть вариативность.
- Low: когда операция хрупкая и должна быть повторяемой.

## 4) Progressive disclosure only one level deep
- Все reference-файлы должны ссылаться напрямую из SKILL.md.
- Избегать цепочек вида `SKILL.md -> advanced.md -> details.md`.

## 5) Workflow + feedback loop
- Для сложных задач использовать чеклист шагов.
- Добавлять loop: validate -> fix -> re-validate.

## 6) Stable wording
- Единая терминология по всему skill.
- Без time-sensitive формулировок, которые быстро устаревают.

## 7) Testing
- Минимум 3-7 quick checks.
- Отдельный manual-review checklist.
- Для refactor: проверка backward compatibility.
