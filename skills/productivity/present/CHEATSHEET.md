# /present Cheatsheet

`/present` turns structured notes into standalone HTML documents or fullscreen slide decks.

## Modes

- `auto` — detect report/offer/general from title and content.
- `general` — long scrollable visual document.
- `slides` — fullscreen deck with keyboard/click navigation.
- `report` — report layout, tables, analysis, comparisons.
- `offer` — proposal / negotiation brief layout.
- `h20` — Human 2.0 style template.

## Run locally

```bash
python3 skills/productivity/present/scripts/generate_present.py \
  --mode slides \
  --title "Product Strategy" \
  --subtitle "Short deck" \
  --input ./input.md \
  --output ./present_strategy.html
```

If `--output` is omitted, the script writes `present_<slug>.html` in the current directory.

## Minimal input

```md
## Why now
Short paragraph.

@flow Signal::What changed::📡
@flow Action::What we do next::⚙️
@flow Result::What improves::✅

## Old vs new
@beforeafter Before::Long text without structure::After::Visual deck with clear blocks
```

## Quick validation

- Plain `/present` must be a scrollable document, not a deck.
- `/present slides` must contain deck markup and controls.
- Final HTML must not show raw markdown structure in visible content.
- Slides mode should export/send PNG preview when the runtime supports it, but HTML is always the canonical artifact.
