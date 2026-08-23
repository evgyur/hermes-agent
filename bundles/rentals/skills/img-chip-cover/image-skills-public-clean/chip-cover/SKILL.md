---
name: chip-cover
description: "Public-clean branded cover art-director/router. Designs cover specs, enforces brand/text/safe-zone rules, and calls img for generation/composition."
version: 1.0.0-public
license: MIT
metadata:
  hermes:
    tags: [cover, branded-media, design-system, image-qa]
    related_skills: [img]
---

# chip-cover — Public Cover Art Director

`chip-cover` designs and governs branded covers and cover series. It is an art-director/router skill, not a generic image generator and not a publisher.

## Contract

Responsibilities:

- choose or create the brand family;
- turn a topic/source/post into a cover thesis;
- write `visual_spec`, `background_prompt`, `overlay_spec`, and `qa_spec`;
- call/use `img` for generation, reference edits, and deterministic composition;
- enforce exact logo/CTA/text/safe-zone rules;
- maintain cover-series rules and manifests;
- return `cover_path` + `qa_verdict`.

Non-responsibilities:

- do not publish posts;
- do not replace `img` as image engine;
- do not ask image models to render exact text/logos/QR;
- do not clone one brand's visuals into unrelated brands by default.

## When to use

Use for branded covers, article covers, social post covers, product launch covers, cover series, new cover-family design, cover QA/revision, and requests like “make a cover”, “series of covers”, “in our brand style”, “cover for this post”.

If the user only asks for a generic picture, poster, meme, avatar, or image edit without brand/cover system requirements, use `img` directly.

## Core architecture

```text
chip-cover decides visual spec
  -> img generates background/key art or performs reference edit
  -> chip-cover overlays exact logo/text/CTA deterministically
  -> QA verifies
  -> caller receives final cover_path
```

## Brand routing

```text
known brand name / channel / product -> brands/<brand-slug>/DESIGN.md
article cover / website hero -> 16:9 editorial mode unless square is requested
social cover / Telegram / X / LinkedIn -> platform-specific safe zones
new channel/product/series/system -> brands/_template new-family flow
generic "picture" -> img, not chip-cover
generic "cover" with unclear brand -> ask one question: existing brand or new family?
```

## Modes

- `create`: create one cover from topic/post/source.
- `revise`: revise existing cover/media.
- `qa`: audit cover without changing it.
- `series`: create multiple covers with one DNA.
- `new-family`: design a new reusable cover system.
- `render-only`: render a locked spec.

## General cover principles

A cover sells a conflict, outcome, or capability — not a vague topic label. It must work phone-first and use exact deterministic text/logo handling.

Load `references/cover-principles.md` for the full rule set.

## Series rules

A series keeps fixed DNA but varies composition and metaphor. Do not freeze a new family after one cover; make three different topics first, QA all, then write the brand pack.

Load `references/series-rules.md`.

## Brand assets contract

This public bundle intentionally does not include private logos, mascots, examples, or generated background pools. Add your own assets under:

```text
chip-cover/assets/<brand-slug>/
chip-cover/brands/<brand-slug>/
```

Use `brands/_template/` as the starting point. See `references/brand-assets-contract.md`.

## `img` handoff

Use `img` for generated no-text background/key art, reference-image edits, deterministic final composition, and exact text/logo/QR rendering.

Do not ask image models to render final exact cover text/logo/CTA. Use no-text background + deterministic overlay.

## Output contract

Every successful cover run returns:

```yaml
cover_path: /absolute/path.png
brand: selected-brand-family
visual_spec:
  headline: ...
  badge: ...
  cta: ...
  composition: ...
  assets: ...
  thesis: ...
qa:
  verdict: ok | needs_fix | blocked
  checks: []
```

Never report publish success from `chip-cover`; publishing belongs to the caller/publishing skill.

## QA contract

Before handoff: final PNG exists and has correct size; headline is readable; exact text is not misspelled/cropped; official logo is present if required; CTA is correct; no generated fake text/logos; safe margins are respected; visual thesis matches source/post; brand family is recognizable; no obvious AI/template slop.

For non-English covers, QA every visible label: translate generic UI/workflow labels unless they are product names, tickers, handles, code/API anchors, or source anchors.

See `references/cover-qa.md`.

## Done criteria

- Correct brand/mode selected.
- Visual spec is explicit.
- `img` route is selected for rendering/composition.
- Final image exists.
- QA verdict is included.
- No private assets or hardcoded private project assumptions.
