# HLRU / Hyperliquid RU cover design system

HLRU covers are premium crypto-market covers for Russian-speaking Hyperliquid readers. They should feel sharp, dark, financial, market-native, and practical — not casino neon, not generic AI SaaS, not a Human20 clone.

## Identity

Channel CTA: `Подписаться: @hyperliquid_ru_news`.

Logo rule: use real approved HLRU/channel logo; never use placeholder `_RU` as a fake logo substitute; never ask image model to draw Hyperliquid/HLRU logo.

Current migrated assets:

```text
runtime-supplied approved logo via HLRU_LOGO_PATH
runtime-supplied background manifest via HLRU_ASSET_DIR/backgrounds/manifest.json
```

## Visual language

Deep black/graphite background; emerald/green signal accent; restrained teal/cyan glow; white/off-white headline; subtle grid/orderbook/chart texture.

Allowed metaphors: orderbook depth, liquidation wave, liquidity pool, perp terminal, agent trader console, vault/collateral, market map.

Avoid: casino neon, coin piles, fake screenshots/charts, overloaded chart gibberish, weak pills/cards, Human20 dark GitHub clone.

## Families

- Market terminal: tools, trading agents, analytics.
- Liquidity metaphor: liquidity, volume, perps, protocol mechanics.
- Signal/news alert: timely changes/releases.

## Renderer

Use `chip-cover/scripts/render_hlru_cover.py`. It uses migrated prepared backgrounds under `chip-cover/assets/hyperliquid_ru/`.

Example:

```bash
python3 scripts/render_hlru_cover.py \
  --title 'Агентам дали|терминал|для рынка' \
  --highlight 1 \
  --facts 'source=Hyperliquid|signal=agent terminal' \
  --background random \
  --out /tmp/hlru_cover.png
```

## QA

Real HLRU/channel logo visible; no `_RU` placeholder misuse; CTA exactly `Подписаться: @hyperliquid_ru_news` unless user overrides; market/usefulness hook; no fake numbers/charts; no casino/clickbait look; readable on phone; final dimensions correct.
