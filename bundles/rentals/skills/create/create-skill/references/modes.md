# /create-skill modes

## extract
Input: готовый материал (док/транскрипт/процесс).
Defaulting rule: если пользователь приносит готовый диалог/текст и просит "сделай из этого skill" без mode, выбирать `extract`.
Process:
- extract goal
- extract cadence / recurring pattern
- extract inputs and sources
- extract output sections / format
- extract operator steps
- extract done criteria
- if source is incomplete -> return assumptions + gaps
Output: production-ready skill или usable draft skill с явным I/O контрактом.
Freedom: medium (структура фиксирована, реализация адаптируется под контекст).

## interview
Input: тема и цель.
Process: 8 вопросов (по одному), затем сборка skill.
Output: skill draft + тесты + риски.
Freedom: high на фазе discovery, medium на фазе сборки.

## refactor
Input: существующий skill path.
Process starts with legacy inventory:
- commands
- modes
- expected outputs
- trigger phrases
- inbound links / dependencies
Output:
- orchestrator SKILL.md,
- modules/*/SKILL.md and/or references split,
- split plan,
- legacy reference snapshot,
- compatibility map (old command/mode -> new route),
- compat shims / preserved entrypoints,
- migration notes.
Freedom: low для внешних интерфейсов (ломать нельзя), medium для внутренней структуры.

## compare
Input: old source, new source, goal/context.
Use when: нужен verdict по old/new без обязательного изменения skill.
Method:
- default to `old/new`
- fall back to `review-only` only when old source, baseline, or comparable evidence is missing
- compare trigger quality, output contract clarity, workflow usefulness, and regressions
Output:
- verdict
- what improved
- what regressed
- must-fix
- confidence/evidence
- recommended next step
Freedom: medium (анализ структурирован, но выводы зависят от контекста).
