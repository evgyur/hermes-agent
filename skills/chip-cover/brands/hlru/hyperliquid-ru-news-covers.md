# Hyperliquid_ru_news Telegram cover style

Use this when Chip asks for covers for the Russian Hyperliquid community / `@hyperliquid_ru_news`, or asks to adapt the Human20-cover workflow to Hyperliquid.

## Visual direction

- Aim for premium crypto/editorial design, not dashboard UI assembled from boxes.
- Best base for `/tg hlru`: use the prepared random pool first: skill-local `assets/hyperliquid_ru/backgrounds/manifest.json` with 20 black/emerald PNGs. Render through `scripts/render_hlru_cover.py --background random`. Detail/reference: `references/hyperliquid-ru-random-cover-pool.md`.
- Generate a fresh `/design` / GPT-Image 2 background only if Chip asks for a new direction or the 20-image pool is exhausted/rejected.
- Palette: matte black / graphite base with deep emerald → teal accents. Avoid flat bright green fills that look cheap.
- Background should have negative space on the left and a high-end abstract liquidity/topographic/glass shape on the right.
- Avoid generated background cards/panels that are cropped at the bottom. If the generated base has bottom UI junk, regenerate a cleaner base instead of fighting it with overlays.

## Typography

- Main expressive headline may use a display/tech font such as Unbounded.
- Secondary text must be calmer and more readable: Golos Text, IBM Plex Sans, Manrope, Inter/Onest-style sans. Avoid fonts that visually “shake”/drift in small Russian text.
- For top/bottom service lines, use bolder clean sans; add only minimal shadow if needed.
- Check mobile readability with vision QA. If Chip says the text is unreadable, fix typeface/size/contrast, not just color.

## Top pill and logo rules

- Top pill text: `РУССКОЯЗЫЧНОЕ КОМЬЮНИТИ HYPERLIQUID`.
- Make it a filled pill with premium gradient, not an underline. Text must be uppercase and optically centered both horizontally and vertically.
- Pill color should be deep emerald/teal, not a flat neon-green slab.
- Logo belongs top-right unless Chip says otherwise.
- Logo container height must match the top pill height and align to the same top edge.
- In a short 60–70px top bar, the full circular logo text is not readable; use the central `_RU`/compact lockup crop inside the top-right container.

## CTA

- CTA belongs in the lower part of the cover: `Подписаться: @hyperliquid_ru_news`.
- Do not place CTA in the top pill. Keep the top pill for community identity only.
- Ensure CTA is not close enough to the edge to feel cropped.

## QA checklist

- [ ] No text escapes its intended field.
- [ ] No cropped generated cards/panels at the bottom.
- [ ] Top pill has a filled background, uppercase centered text, and no underline.
- [ ] Top-right logo container has the same height/top alignment as the pill.
- [ ] Secondary fonts are stable and readable; no “drifting” small text.
- [ ] Overall feel is premium crypto editorial, not hand-made dashboard/UI collage.
