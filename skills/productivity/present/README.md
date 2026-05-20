# present

`present` is a bundled Hermes skill for generating standalone HTML presentations, reports, offers, and fullscreen slide decks.

## Complete installation

A complete installation includes:

- `SKILL.md`
- `references/`
- `scripts/`
- `templates/`

If only `SKILL.md` is present, rendering will not work because the generator and templates are missing.

## Modes

- `/present <topic/text>` → scrollable HTML document.
- `/present slides <topic/text>` → fullscreen slide deck.
- `/present report <topic/text>` → report template.
- `/present offer <topic/text>` → offer/proposal template.
- `/present h20 <topic/text>` → Human 2.0 style template.

## Editorial cleanup

The generator can use an optional `vendor/postcraft/scripts/deslop_text.py` cleanup helper if present. The bundled version also has a conservative built-in fallback, so it remains self-contained without vendored private data.
