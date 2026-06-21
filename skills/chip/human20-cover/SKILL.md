---
name: human20-cover
description: "Generates Human20/Человек 2.0 style cover images and teaches users how to adapt the same premium cover pipeline to their own brand. Use when making Human20 covers or reusable branded square covers with logo, CTA, source/repo/product card, controlled text overlay, and mobile readability QA."
version: 0.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [human20, tg, cover, design, telegram, brand]
    related_skills: [tg, postcraft, chip-img]
---

# human20-cover

Creates the default Human20 cover format and a reusable branded-cover template that users can adapt after installing Powerpack:

- square 1080×1080 PNG;
- dark navy/indigo gradient background;
- official `Человек 2.0 / СРЕДА ВНЕДРЕНИЯ ИИ` logo from Human20 assets;
- top-right topic badge, e.g. `HERMES HOTFIX`;
- large Russian headline;
- optional subtitle;
- central white product/GitHub/code card;
- large CTA, default `Подписаться: @human20`, override with `--cta` for another brand;
- footer, default `Человек 2.0 · Среда внедрения ИИ` + `human20.app`, override with `--footer` and `--url`.

## When to use

Use this skill when:

- a Human20 post or lesson needs a branded cover;
- a user wants the Human20-style cover workflow as a starting template for their own brand;
- `/tg human20 ...` or another configured publishing flow needs a cover;
- a technical/product/source-driven post benefits from a GitHub/code-card visual.

For sibling/non-Human20 brands, preserve the production workflow (premium background + controlled overlay + QA) but adapt the brand pack: logo, CTA, palette, footer, URL, badge language, and examples. Do not blindly clone Human20 copy into another brand.

## Default decision

Best architecture:

1. Keep this as a separate reusable cover skill.
2. Let `tg` call it when mode is `human20` or when Human20/OpenClaw/Hermes content needs branded media.
3. Keep `/tg` responsible for caption, sources, postcraft, preview and publish gates.
4. Keep `human20-cover` responsible only for image format, brand assets, CTA, and cover QA.

Reason: the image format will evolve independently from Telegram caption rules. If buried inside `tg`, it becomes harder to improve without touching delivery logic.

## Asset protection during cleanup

Human20 cover files are reusable brand/template assets, not old generated media. During `/tg`, media-cache, or skill-library cleanup, do **not** delete:

- this skill directory;
- renderer: `scripts/render_human20_cover.py`;
- brand assets supplied through `HUMAN20_BRAND_DIR`, `--brand-dir`, `--logo`, `--mark`, and `--fonts-dir`.

Only one-off rendered outputs in `/tmp`, media cache, or generated-output archives may be deleted. If unsure whether an image is a template or a disposable output, keep it and ask the operator.

## Input schema

Minimum:

```yaml
headline: "Hermes впервые\nне смог починить себя"
badge: "HERMES HOTFIX"
subtitle: "патч для openai-codex stream failure"
card_title: "GitHub: fix-hermes-nonetype"
chips: ["MIT", "Shell patch", "Codex Responses"]
code_lines:
  - text: "TypeError: 'NoneType' object is not iterable"
    tone: error
  - text: "response.output = null → recover from stream events"
    tone: neutral
  - text: "- SDK parser crash"
    tone: error
  - text: "+ Hermes-side recovery"
    tone: success
output: "/tmp/tg_human20_cover.png"
```

## Operating steps

1. Extract the visual thesis from the post: what broke, launched, changed, or became possible.
2. Choose a short badge: `HERMES HOTFIX`, `OPENCLAW`, `AI WORKFLOW`, `HUMAN20`, etc.
3. Write a 1–2 line Russian headline. Keep it readable at phone size.
4. Run the cover-headline hook test before rendering: the headline must state the reader-facing outcome, conflict, or new capability, not describe the internal process. Bad: `Блог готовят под ИИ-поиск` (process label). Good: `Статья должна попасть в ответы ИИ` (outcome/goal). For finance/tool covers, prefer a concrete capability hook like `Агенту дали рынок опционов` over a category label.
5. Use official Human20 assets when available, or user-supplied brand assets:
   - `HUMAN20_BRAND_DIR=/path/to/brand` with `logos/png/h20-lockup-light-720.png`, `logos/png/h20-mark-512.png`, and `fonts/`;
   - or pass `--logo`, `--mark`, and `--fonts-dir` directly;
   - if assets are absent, the renderer falls back to text logo and system fonts so the skill still works after install.
6. Render with `scripts/render_human20_cover.py` or a task-specific Python/PIL variant.
   - If adapting the Human20 cover language to a new brand/community, do not build the entire visual identity as plain code geometry unless the existing template already carries the taste. For a new premium crypto/community cover, first create a high-quality generated background/key art, then deterministically overlay logo, headline, metrics, and CTA.
   - All visible text must sit inside bounded fields: card, pill, headline panel, or CTA pill. Use auto-fit sizing and fail QA if text crosses the field boundary.
7. Run one vision QA pass:
   - logo/brand label visible;
   - CTA matches the chosen brand/action;
   - Russian text readable;
   - no gibberish, broken repo names, cropped text, or random logos;
   - no text overflow outside cards/pills/safe margins;
   - image matches the post thesis and is visually strong enough to compare with the approved Human20 cover quality.
8. For Telegram/publishing flows, return the final PNG path for the caller. Publishing/preview gates belong to the publishing skill, not this renderer.

## Output Contract

Every successful run returns:

- `cover_path`: absolute PNG path;
- `visual_spec`: headline, badge, subtitle, card title, chips and code/product lines used;
- `qa_verdict`: `ok`, `needs_fix`, or `blocked`;
- optional `tg_handoff`: media path only when a publishing flow explicitly requested it.

## Quick Test Checklist

- [ ] Script renders a 1080×1080 PNG without network access.
- [ ] Human20 lockup or fallback brand label is visible in the top-left.
- [ ] CTA matches the intended brand/action and can be overridden with `--cta`.
- [ ] Cover headline is a hook, not a process label: it names the outcome, tension, or usable capability.
- [ ] For 3-line headlines, keep a clean visual air gap before the white card; the card must move down dynamically instead of the headline touching/overlapping it.
- [ ] Text fits inside cards/buttons and is readable on phone.
- [ ] No misspelled repo/product names.
- [ ] Avoid decorative glyphs in small chips (`★`, emoji, unusual symbols). They can render as tofu/broken boxes in the cover; use plain text like `80k stars` instead.
- [ ] Code block auto-fits up to 4 short lines inside the black field; run QA if a line is unusually long.
- [ ] Top-right badge/pill uses dynamic bbox centering, not fixed text coordinates.
- [ ] One vision QA pass returns `ok` before `/tg` send; if the operator rejects the headline as weak, regenerate the cover and send a new preview rather than explaining inline.

Autofit implementation notes and a 4-line QA fixture live in `references/layout-autofit-pitfalls.md`.

## Quick command

```bash
python3 skills/chip/human20-cover/scripts/render_human20_cover.py \
  --headline $'Hermes впервые\nне смог починить себя' \
  --badge 'HERMES HOTFIX' \
  --subtitle 'патч для openai-codex stream failure' \
  --card-title 'GitHub: fix-hermes-nonetype' \
  --chips 'MIT|Shell patch|Codex Responses' \
  --code $"TypeError: 'NoneType' object is not iterable|error" \
  --code $'response.output = null → recover from stream events|neutral' \
  --code $'- SDK parser crash|error' \
  --code $'+ Hermes-side recovery|success' \
  --cta 'Подписаться: @human20' \
  --footer 'Человек 2.0 · Среда внедрения ИИ' \
  --url 'human20.app' \
  --output /tmp/tg_human20_cover.png
```

## `/tg human20` contract

When `tg` receives `/tg human20 ...`:

1. write/verify caption through `tg → postcraft`;
2. select relevant Human20 visual angle and optional tag/chip;
3. call this skill/script to make a cover;
4. run one image QA pass;
5. hand the PNG to the configured publishing/preview flow, which owns delivery verification.

## Key references

- `references/layout-autofit-pitfalls.md` — badge centering and 4-line code-field auto-fit lessons from live Human20 cover fixes.
- `references/visual-contract.md` — approved Human20 cover family, fixed brand elements, variable fields, and QA failures.

## Done criteria

- PNG exists and is readable on mobile.
- Uses official Human20 logo/lockup when supplied, or a clear fallback text brand label when assets are absent.
- CTA/footer/URL match the selected brand and are readable.
- No source/media mismatch.
- If a publishing flow is used, that flow verifies delivery separately.
