---
name: postcraft
description: Use when writing, rewriting, shortening, humanizing, de-slopping, and editorially sharpening short-form Russian or English posts from a topic, draft, notes, transcript, or source link without handling channel-specific delivery.
metadata:
  hermes:
    tags: [writing, editing, copywriting, posts, deslop]
---

# postcraft — editorial core for short posts

`postcraft` turns rough material into clear short-form copy. It writes, rewrites, sharpens, shortens, preserves source facts, and removes generic AI/editorial slop.

It does **not** publish, send Telegram previews, generate images, or manage channel routing. Adapters such as `tg` handle those layers.

## Trigger

Use this skill when the user asks to:

- write a short post from a topic, notes, link, transcript, or screenshot;
- rewrite, humanize, shorten, sharpen, or de-slop a draft;
- turn source material into a clear public-facing post;
- make text more natural, direct, specific, or less AI-like;
- prepare variants with different angles.

Do not use it for pure strategy, long-form articles, landing pages, image generation, or Telegram publishing mechanics unless paired with a channel adapter.

## Core stance

- Better exact than inflated.
- Better slightly uneven and human than perfectly smooth and empty.
- Do not invent facts, quotes, motives, numbers, or source details.
- Every paragraph must add payload: fact, mechanism, example, consequence, opinion, or action.
- Cut filler before polishing style.
- Preserve the user's useful roughness when it carries voice or tension.
- Match text length to material strength. Thin material should stay short.

## Lane policy

### Fast lane

Use for low-risk drafts, local rewrites, short edits, and user-owned material.

Steps:

1. Identify reader and job of the post.
2. Lock the main point in one plain sentence.
3. Draft directly.
4. Remove generic phrases and weak transitions.
5. Return one clean version, or up to three variants if requested.

### Strict lane

Use for source-backed posts, news, finance, health, security, legal-ish claims, or anything public/high-stakes.

Steps:

1. Extract source facts: names, dates, numbers, claims, links, uncertainty.
2. Define editorial intent:
   - reader;
   - desired shift;
   - preserve items;
   - risk flags;
   - one throughline.
3. Draft from the throughline, not from a generic summary template.
4. Check every factual claim against the source brief.
5. Remove hedges that are only there to sound safe, but preserve real uncertainty.

### Recovery lane

Use when editing an already shown draft or when the user corrects tone/source.

Steps:

1. Use the latest visible user-provided draft/correction as source of truth.
2. Preserve scope unless the user asks to shorten.
3. Patch locally for small changes; rewrite fully if the structure is broken.
4. Return the full revised text, not just advice.

## Before writing

Ask silently:

- Who is the reader?
- What should they understand, feel, or do after reading?
- What facts must survive exactly?
- What is the strongest honest angle?
- What can be cut without losing payload?
- What claims need verification?

If source context is missing and materially affects accuracy, fetch it when tools are available or ask one concise clarifying question.

## Anti-slop rules

Avoid or rewrite:

- `важно понимать`, `стоит отметить`, `в современном мире`, `давайте разберёмся`;
- `главный вывод простой`, `суть простая`, `идея в том`, `это меняет всё`;
- lazy contrast as a default rhythm: `это не X, это Y`, `не просто X, а Y`;
- vague verdict labels: `интересный кейс`, `важный сигнал`, `рабочая история`, `сильное направление`;
- empty drama: `парадокс`, `нерв`, `переломный момент`, unless the mechanism is named;
- corporate fog: `осуществляет влияние`, `является фактором`, `в рамках`, `реализовать процесс`;
- padded endings that restate the obvious.

Replace with concrete actor/action/mechanism:

- instead of `это важный сигнал` → say what changed and why it matters;
- instead of `идея простая` → state the idea directly;
- instead of `не X, а Y` → name the mechanism without mirror contrast.

## Style rules

- Start with payload, not throat-clearing.
- Use short paragraphs.
- Prefer active verbs.
- Keep one thought per paragraph.
- Use concrete nouns and examples.
- If a sentence sounds like a press release, rewrite it.
- If a line exists only because it is smooth, cut it.

For Russian posts:

- write natural Russian, not translated English;
- use `ИИ` in ordinary prose unless a product/source name requires `AI`;
- translate ordinary English terms when a normal Russian equivalent exists;
- keep product names, tickers, code/API names, model names, and exact source anchors as-is.

## Source preservation

For source-backed work, preserve:

- names and roles;
- numbers and units;
- dates and time windows;
- links or source attribution;
- uncertainty level;
- any user-specified angle.

Do not preserve raw source noise that does not belong in the final post: tracking URLs, UI labels, irrelevant metadata, repeated boilerplate, or unrelated entities.

## Output contract

Default output:

- one clean final text;
- no process explanation unless there is a blocker;
- no invented facts;
- no channel-specific formatting unless requested.

If the user asks for variants, return up to three clearly different angles.

If a claim could not be verified, say that compactly after the text:

```text
Проверка: источник не был доступен, поэтому фактические claims оставил мягкими.
```

## Quick test checklist

- [ ] The post has a clear reader and job.
- [ ] The first two sentences contain real payload.
- [ ] No generic AI intro or moralizing outro survives.
- [ ] Source facts, numbers, names, and uncertainty are preserved.
- [ ] The draft does not invent motives, quotes, or data.
- [ ] The final text is shorter and sharper than the first safe draft would be.

## Done criteria

- The user gets usable copy, not advice about copy.
- The output matches the requested scope and genre.
- Slop was removed structurally, not only by banning a few phrases.
- Channel delivery, preview, and publishing are left to the relevant adapter skill.
