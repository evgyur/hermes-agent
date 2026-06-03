# create-skill description quality guide

## Core rule
Description должна отвечать на два вопроса:
1. Что делает skill
2. Когда именно его нужно вызывать

## Good description traits
- В 3-м лице
- Описывает capability и trigger contexts
- Содержит типовые пользовательские формулировки или ситуации
- Лучше слегка pushy, чем слишком узкая и тихая

## What to include
- transformation: что skill создаёт, улучшает или реорганизует
- trigger context: когда он нужен
- common phrases: как пользователь обычно это формулирует
- boundary: где skill уже не лучший маршрут

## Anti-patterns

### Too vague
- "Creates skills"
- "Helper for skill work"

### Better
- "Builds or refactors a skill into a concise, testable structure with explicit contracts and strong trigger descriptions. Use when the user wants to turn a workflow or conversation into a reusable skill, improve weak triggering, split a bloated skill, or compare old/new skill behavior."

## Undertrigger defense
Если сомневаешься между слишком общим и слишком узким описанием, смещайся в сторону более явных trigger contexts. Для create-skill это безопаснее, чем молчаливый undertrigger.
