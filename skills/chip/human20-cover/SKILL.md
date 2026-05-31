---
name: human20-cover
description: "Generates Human20/Человек 2.0 Telegram cover images in the approved dark GitHub/code-card format. Use when making Human20 TG posts, especially via /tg human20, when a post needs a branded square cover with official Человек 2.0 logo, large CTA, source/repo card, code/error/product details, and mobile-readable Russian text."
version: 0.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [human20, tg, cover, design, telegram, brand]
    related_skills: [tg, postcraft, chip-img]
---

# human20-cover

Creates the default Human20 Telegram cover format:

- square 1080×1080 PNG;
- dark navy/indigo gradient background;
- official `Человек 2.0 / СРЕДА ВНЕДРЕНИЯ ИИ` logo from Human20 assets;
- top-right topic badge, e.g. `HERMES HOTFIX`;
- large Russian headline;
- optional subtitle;
- central white product/GitHub/code card;
- large CTA exactly `Подписаться: @human20`;
- footer `Человек 2.0 · Среда внедрения ИИ` + `human20.app`.

## When to use

Use this skill when:

- Chip says a Human20 post needs a branded cover;
- `/tg human20 ...` or Human20/OpenClaw/Hermes post needs a cover;
- Chip likes the Hermes hotfix graphic format and asks for the same visual family;
- a post is technical/product/source-driven and benefits from a GitHub/code-card visual.

For sibling/non-Human20 brands where Chip asks for a Human20-like cover system, load `telegram-cover-design` first instead of cloning the Human20 template literally. The lesson from Hyperliquid_ru_news: preserve the production workflow (generated premium background + controlled overlay + QA), but redesign the visual language for the new brand; cheap pills/cards over a good background read as "колхоз".

Do not use for ordinary non-Human20 posts unless Chip explicitly asks for this format.

## Default decision

Best architecture:

1. Keep this as a separate reusable cover skill.
2. Let `tg` call it when mode is `human20` or when Human20/OpenClaw/Hermes content needs branded media.
3. Keep `/tg` responsible for caption, sources, postcraft, preview and publish gates.
4. Keep `human20-cover` responsible only for image format, brand assets, CTA, and cover QA.

Reason: the image format will evolve independently from Telegram caption rules. If buried inside `tg`, it becomes harder to improve without touching delivery logic.

## Asset protection during cleanup

Human20 cover files are reusable brand/template assets, not old generated media. During `/tg`, media-cache, or skill-library cleanup, do **not** delete:

- this skill directory: `/home/hermes/.hermes/skills/chip/human20-cover/`;
- renderer: `scripts/render_human20_cover.py`;
- Human20 brand assets: `/home/hermes/workspace/human20-app/frontend-v2/public/brand/`.

Only one-off rendered outputs in `/tmp`, media cache, or generated-output archives may be deleted. If unsure whether an image is a template or a disposable output, keep it and ask Chip.

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
5. Use official Human20 assets when available:
   - `/home/hermes/workspace/human20-app/frontend-v2/public/brand/logos/png/h20-lockup-light-720.png`
   - `/home/hermes/workspace/human20-app/frontend-v2/public/brand/logos/png/h20-mark-512.png`
   - fonts under `/home/hermes/workspace/human20-app/frontend-v2/public/brand/fonts/`
5. Render with `scripts/render_human20_cover.py` or a task-specific Python/PIL variant.
   - If adapting the Human20 cover language to a new brand/community, do not build the entire visual identity as plain code geometry unless the existing template already carries the taste. For a new premium crypto/community cover, first create a high-quality generated background/key art, then deterministically overlay logo, headline, metrics, and CTA.
   - All visible text must sit inside bounded fields: card, pill, headline panel, or CTA pill. Use auto-fit sizing and fail QA if text crosses the field boundary.
6. Run one vision QA pass:
   - official logo visible;
   - CTA exactly `Подписаться: @human20`;
   - Russian text readable;
   - no gibberish, broken repo names, cropped text, or random logos;
   - no text overflow outside cards/pills/safe margins;
   - image matches the post thesis and is visually strong enough to compare with the approved Human20 cover quality.
7. For `/tg`, pass the final PNG as `TG_MEDIA_PATH_OVERRIDE` and let `send-preview.sh` verify delivery.

## Output Contract

Every successful run returns:

- `cover_path`: absolute PNG path;
- `visual_spec`: headline, badge, subtitle, card title, chips and code/product lines used;
- `qa_verdict`: `ok`, `needs_fix`, or `blocked`;
- `tg_handoff`: `TG_MEDIA_PATH_OVERRIDE=<cover_path>` for `/tg` preview delivery.

## Quick Test Checklist

- [ ] Script renders a 1080×1080 PNG without network access.
- [ ] Official Human20 lockup is visible in the top-left.
- [ ] CTA is exactly `Подписаться: @human20`.
- [ ] Cover headline is a hook, not a process label: it names the outcome, tension, or usable capability.
- [ ] Text fits inside cards/buttons and is readable on phone.
- [ ] No misspelled repo/product names.
- [ ] Avoid decorative glyphs in small chips (`★`, emoji, unusual symbols). They can render as tofu/broken boxes in the cover; use plain text like `80k stars` instead.
- [ ] Code block auto-fits up to 4 short lines inside the black field; run QA if a line is unusually long.
- [ ] Top-right badge/pill uses dynamic bbox centering, not fixed text coordinates.
- [ ] One vision QA pass returns `ok` before `/tg` send; if Chip rejects the headline as weak, regenerate the cover and send a new ChipCR preview rather than explaining inline.

Autofit implementation notes and a 4-line QA fixture live in `references/layout-autofit-pitfalls.md`.

## Quick command

```bash
python3 /home/hermes/.hermes/skills/chip/human20-cover/scripts/render_human20_cover.py \
  --headline $'Hermes впервые\nне смог починить себя' \
  --badge 'HERMES HOTFIX' \
  --subtitle 'патч для openai-codex stream failure' \
  --card-title 'GitHub: fix-hermes-nonetype' \
  --chips 'MIT|Shell patch|Codex Responses' \
  --code $"TypeError: 'NoneType' object is not iterable|error" \
  --code $'response.output = null → recover from stream events|neutral' \
  --code $'- SDK parser crash|error' \
  --code $'+ Hermes-side recovery|success' \
  --output /tmp/tg_human20_cover.png
```

## `/tg human20` contract

When `tg` receives `/tg human20 ...`:

1. write/verify caption through `tg → postcraft`;
2. select relevant Human20 visual angle and optional tag/chip;
3. call this skill/script to make a cover;
4. run one image QA pass;
5. send preview through ChipCR only, with exact verify-gate.

## Done criteria

- PNG exists and is readable on mobile.
- Uses official Human20 logo/lockup, not an invented logo.
- CTA says exactly `Подписаться: @human20`.
- Footer has `Человек 2.0 · Среда внедрения ИИ` and `human20.app`.
- No source/media mismatch.
- `/tg` preview state has `ok:true`, `verified:true`, `sender_verified: Evgeny "Chip"`, `has_media:true`.
