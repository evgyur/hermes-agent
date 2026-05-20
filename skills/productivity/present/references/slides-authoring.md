# Slides authoring guide for `/present slides`

Use this mode when the output must behave like a real slide deck, not a long document.

## What makes a good deck

A good deck is:
- fast to scan
- visually segmented
- easy to present aloud
- opinionated in its headings
- sparse enough to fit one screen per idea

A bad deck is just a report cut into chunks.

## Recommended structure

Typical sequence:

1. Cover
2. Why this matters
3. Core mechanism / flow
4. Roles / actors / system boundaries
5. Economics / metrics / constraints
6. Safeguards / non-goals
7. What is already done
8. What happens next
9. Closing status / CTA

Not every deck needs all 9, but most strong decks follow this rhythm.

## Headline rule

Prefer claim-headlines over label-headlines.

Better:
- `Партнёрский пул фиксирован на 45%`
- `Амбассадор создаёт два типа ссылок`
- `Лид фиксируется в Supabase в момент входа`

Worse:
- `Экономика`
- `Ссылки`
- `База данных`

## Content limits per slide

Aim for one of these per slide:
- 1 core claim + up to 4 bullets
- 1 visual block (`@flow`, `@compare`, `@beforeafter`, `@chart`, `@timeline`, `@phase`)
- 1 table if the comparison truly needs a table
- 2 small cards only if they are tightly related

Avoid mixing too many shapes on one screen.

## Visual block selection

Choose the strongest shape for the meaning:

- process, funnel, architecture -> `@flow`
- tradeoff, old/new, option A/B -> `@compare` or `@beforeafter`
- metrics, percentages, ranking -> `@chart`
- phases, rollout, chronology -> `@timeline` or `@phase`
- entities / actors / components -> `@entity` or cards

## Split rule

If a slide contains:
- more than 1 big table
- more than 5 bullets
- more than 2 paragraphs
- both detailed process and detailed economics

split it into two slides.

## Writing rule

Write short, linked, spoken-friendly Russian:
- concise, but not telegraphic
- no buzzword soup
- no empty filler
- no obvious repetition from slide title into every bullet

## Final smell test

Before calling the deck done, ask:
- can I understand each screen in 3-5 seconds?
- would this be readable from across the room?
- is each title carrying a point, not just a category?
- does this feel like a presentation, not a memo?
