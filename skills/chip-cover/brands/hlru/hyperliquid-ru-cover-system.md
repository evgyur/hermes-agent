# Hyperliquid_ru_news Telegram cover system

Use when Chip asks for covers for `@hyperliquid_ru_news` / `Hyperliquid_ru_news` / Russian-speaking Hyperliquid community, or asks to adapt the Human20 cover workflow to Hyperliquid.

## Brand direction

- Format: square 1080×1080 Telegram cover.
- Mood: premium crypto / DeFi editorial, not cheap dashboard UI.
- Palette: matte black / graphite + emerald / cyan-green glow + white text.
- Brand asset: supplied `_RU` logo. Keep it as a logo badge; do not let it compete with the main title.
- CTA: `Подписаться: @hyperliquid_ru_news`.
- Top brand line: `Русскоязычное комьюнити Hyperliquid`.

## Preferred workflow

1. Generate a strong clean background first via `/design` / image-poster / GPT-Image 2.
2. Prompt for **no text, no numbers, no logos, no UI gibberish, no panels/cards at crop edges**.
3. Use the background as art direction, then overlay real typography and logo with deterministic PIL/script.
4. Keep overlays minimal: editorial typography, thin divider lines, 2–3 metrics at most.
5. Avoid heavy pills, cheap glass cards, terminal-looking boxes, and random dashboard widgets unless the post is explicitly about a terminal/tool.
6. Use better fonts than DejaVu defaults. Good tested stack:
   - `Unbounded` for large title / HYPE accent;
   - `Manrope` for Russian labels and CTA;
   - `JetBrains Mono` only for tiny technical labels if needed.

## Layout rules from Chip corrections

- Top area should contain only the enlarged brand line: `Русскоязычное комьюнити Hyperliquid`.
- Put `Подписаться: @hyperliquid_ru_news` in the lower part, not under the top line.
- Do not put logo in the top area if it competes with the brand line. Prefer bottom-right or a quiet side badge.
- Text must never overflow its field, pill, card, or safe area.
- If using pills/cards, they must look intentional and premium; if they look like generic UI blocks, remove them.
- Generated backgrounds often include half-cards near the bottom crop. Regenerate with `no panels/cards at crop edges` rather than masking a bad crop.

## QA checklist

Before showing or attaching:

- `Русскоязычное комьюнити Hyperliquid` is readable on mobile.
- CTA is in the bottom area, fully visible, not clipped.
- No fake text/gibberish from the image model.
- No text overlaps, no metric label collision.
- No obtrusive or cheap-looking pills/cards.
- Logo is visible but secondary.
- Overall verdict: premium editorial crypto cover, not homemade dashboard.