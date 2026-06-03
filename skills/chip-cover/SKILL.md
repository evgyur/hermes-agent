---
name: chip-cover
description: "Universal branded cover system for Hermes/Powerpack. Use for creating, redesigning, QAing, or systematizing cover images and cover series for Human20, HLRU, Telegram posts, Dzen/articles, product launches, and future brands. Routes brand-specific cover families, writes visual specs, calls img for generation/composition, enforces logo/CTA/text/safe-zone rules, and hands final media to tg only for Telegram preview/publish."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cover, branded-media, design-system, telegram, human20, hlru, image-qa]
    related_skills: [img, tg, hallmark, refero-web-design]
---

# chip-cover — Universal Cover Art Director

`chip-cover` designs and governs branded covers and cover series. It is an art-director/router skill, not a generic image generator and not a Telegram publisher.

## Contract

Responsibilities:

- choose brand family: Human20, HLRU, or new/future brand;
- turn post/topic/source into a cover thesis;
- write `visual_spec`, `background_prompt`, `overlay_spec`, and `qa_spec`;
- call/use `img` for generation, reference edits, and deterministic composition;
- enforce exact logo/CTA/text/safe-zone rules;
- maintain cover-series rules and manifests;
- return `cover_path` + `qa_verdict` + optional `TG_MEDIA_PATH_OVERRIDE`.

Non-responsibilities:

- do not publish Telegram posts;
- do not replace `img` as image engine;
- do not ask image models to render exact Russian text/logos/QR;
- do not clone Human20 visuals into unrelated brands by default.

## When to use

Use for branded Telegram post covers, Dzen/article/product cover images, cover series, new cover-family design, cover QA/revision, and requests like “сделай обложку”, “серия обложек”, “как <brand>”, “обложка к посту”. Human20/HLRU brand packs are examples; Powerpack does not bundle private ready-made cover assets.

If the user only asks for a generic picture, poster, meme, avatar, or image edit without brand/cover system requirements, use `img` directly.

## Core architecture

```text
chip-cover decides visual spec
  -> img generates background/key art or performs reference edit
  -> chip-cover overlays exact logo/text/CTA deterministically
  -> vision/QA verifies
  -> tg previews only if Telegram post flow is active
```

## Brand routing

```text
human20 / человек 2.0 / среда / h20 / /tg human20 -> brands/human20
hlru / Hyperliquid RU / /tg hlru -> brands/hlru (requires user-supplied logo/background assets)
new channel/product/series/system -> brands/_template new-family flow
generic "картинка" -> img, not chip-cover
generic "обложка" with unclear brand -> ask one question: Human20, HLRU, or new family?
```

## Modes

- `create`: create one cover from topic/post/source.
- `revise`: revise existing cover/media.
- `qa`: audit cover without changing it.
- `series`: create multiple covers with one DNA.
- `new-family`: design a new reusable cover system.
- `render-only`: render a locked spec.
- `handoff-tg`: return `TG_MEDIA_PATH_OVERRIDE` for `tg`.

## Input schema

See `references/visual-spec-schema.md`.

## General cover principles

A cover sells a conflict, outcome, or capability — not a vague topic label. It must work phone-first and use exact deterministic text/logo handling.

Load `references/cover-principles.md` for the full rule set.

## Series rules

A series keeps fixed DNA but varies composition and metaphor. Do not freeze a new family after one cover; make three different topics first, QA all, then write the brand pack.

Load `references/series-rules.md`.

## Human20 brand pack

Use `brands/human20/DESIGN.md` as an example brand pack. Renderer: `scripts/render_human20_cover.py` uses fallback fonts/marks when private brand assets are unavailable.

## HLRU brand pack

Use `brands/hlru/DESIGN.md` as an example market-cover brand pack. Renderer: `scripts/render_hlru_cover.py` requires user-supplied approved assets; ready-made HLRU backgrounds/logos are intentionally not bundled.

## Hallmark / Refero role

Hallmark and Refero are research/taste inputs for new cover families, not runtime dependencies for every cover. When used, write the extracted direction into the relevant brand DESIGN file under `brands/` so the next run does not need to repeat research.

See `references/hallmark-cover-lessons.md` and `references/refero-reference-lock.md`.

## `img` handoff

Use `img` for generated no-text background/key art, reference-image edits, deterministic final composition, and exact text/logo/QR rendering.

Do not ask image models to render final exact cover text/logo/CTA. Use no-text background + deterministic overlay.

See `references/background-generation.md` and `references/deterministic-overlay.md`.

## `tg` handoff

For Telegram flows, return:

```yaml
TG_MEDIA_PATH_OVERRIDE: /absolute/path.png
ready_for_preview: true
qa_verdict: ok
```

`tg` owns preview/publish gates. `chip-cover` never publishes.

See `references/tg-handoff.md`.

## Output Contract

Every successful cover run returns:

- `cover_path`: absolute PNG path;
- `brand`: selected brand family;
- `visual_spec`: headline, badge, CTA, composition, assets, and thesis;
- `qa`: verdict `ok | needs_fix | blocked` plus concrete checks;
- `tg_handoff`: `TG_MEDIA_PATH_OVERRIDE=<cover_path>` only when Telegram preview is in scope.

Never report publish success from `chip-cover`; publishing belongs to `tg`.

## QA contract

Before handoff: final PNG exists and has correct size; headline is readable; exact text is not misspelled/cropped; official logo is present; CTA is correct; no generated fake text/logos; safe margins are respected; visual thesis matches source/post; brand family is recognizable; no obvious AI/template slop.

See `references/cover-qa.md`.

## Compatibility shims

- `chip-img` -> `img`.
- `chip/human20-cover` -> `chip-cover` with `brand: human20`.
- Existing brand-specific TG flows should call `chip-cover` for cover media and `tg` for delivery, with local brand assets supplied by the runtime.

## Quick Test Checklist

- [ ] Human20 topic routes to `brands/human20`.
- [ ] HLRU topic routes to `brands/hlru`.
- [ ] Generic image request routes to `img`, not `chip-cover`.
- [ ] New brand/series request routes to `new-family` workflow.
- [ ] `chip-cover` returns `cover_path` + `qa_verdict`, not a publish action.
- [ ] Generic overlay renderer produces 1080×1080 PNG without bundled private assets.
- [ ] Brand-specific renderers compile; runtime supplies approved brand assets when needed.

## Done Criteria

- Root skill loads.
- Human20 and HLRU brand packs exist.
- General references exist.
- Renderers compile.
- Compatibility aliases exist.
- At least one cover smoke test has real file evidence.
