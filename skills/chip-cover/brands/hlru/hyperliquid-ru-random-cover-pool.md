# Hyperliquid RU random cover pool

Session learning from building `/tg hlru` cover assets.

## Assets

- Background manifest: skill-local `assets/hyperliquid_ru/backgrounds/manifest.json` (`$HLRU_ASSET_DIR/...` at runtime)
- Background files: skill-local `assets/hyperliquid_ru/backgrounds/hlru_bg_*.png`
- Count: 20 prepared black/emerald premium crypto backgrounds.
- Renderer/random picker: `scripts/render_hlru_cover.py`
- Sample rendered set: `<local-sample-output-dir>/`

## Default workflow for `/tg hlru`

1. Write the caption normally under `chip_style` guardrails.
2. Build a cover with `render-hlru-cover.py` using `--background random` unless composition needs a specific id.
3. Use title lines separated by `|`; highlight one line with `--highlight <0-based-index>`.
4. Use up to 3–4 facts as `left=right` rows.
5. Preview with the actual media attached; never only say that the media was created.

Example:

```bash
scripts/render_hlru_cover.py \
  --title 'Cabal|запускает|фонды|на HyperEVM' \
  --highlight 2 \
  --facts 'launchpad=для tokenized funds|one click=фонд сразу торгуется|$10K=барьер старых vaults' \
  --background random \
  --out /tmp/hlru_cover.png
```

## QA notes

- Backgrounds are 1024×1024; renderer outputs 1080×1080 final covers.
- Vision QA of the sample set found no critical composition failures; all 20 can stay in random rotation.
- If a future title is very long, select a calmer background id manually instead of regenerating art.

## Style pitfall captured from the same session

Do not write lazy verdict labels around crypto mechanics:

- Bad: `Механика необычная`.
- Better: name the exact mechanism and whether it is actually unusual in crypto ETF context.

Do not outsource simple arithmetic:

- Bad: `по оценке материала, это около $115 млн` when the amount and price are available.
- Better: compute it directly and cite the source for inputs.
