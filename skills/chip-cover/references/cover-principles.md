# Cover principles

## Thesis first

A cover must answer one of these: what changed, what broke, what became possible, what conflict matters, or what the tool/project is useful for.

Bad: `Новости ИИ`, `Проект на GitHub`, `Обновление в агенте`, `Karpathy написал тред`.
Good: `Агентам дали терминал для рынка`, `Память агента стала продуктовой функцией`, `ИИ-поиск теперь видит твою статью`, `Hermes сам чинит свой gateway`.

## Phone-first readability

- Headline readable at phone feed size.
- Prefer 2-5 words per line.
- Use no more than 3 headline lines unless brand pack allows it.
- Keep badge short.
- Do not rely on tiny code/table text for meaning.

## Typography hierarchy

1. Brand/logo.
2. Headline.
3. Visual object/card/metaphor.
4. Supporting detail.
5. CTA/footer.

Badge/chips never compete with headline.

## Exact text

If text must be exact, do not ask the image model to render it. Generate a no-text background and render exact text deterministically.

## Logo

Use official local assets, official brand/newsroom assets, approved SVG/PNG, existing channel logo, or user-provided logo. Never invent a logo or use placeholder `_RU` as a fake substitute.

## CTA

CTA role depends on audience: public subscribe/follow, existing participant next action, article URL, internal omitted, payment action+URL. CTA is not decoration.

## Anti-slop

Never ship random gradient blob + generic white card, fake browser chrome, invented metrics, fake source names, unreadable code, broken Cyrillic, placeholder logos, too many pills/chips, or Human20 clone styling for another brand.
