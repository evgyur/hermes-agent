# Text-heavy mobile infographics

Use this reference when the user asks for a readable poster/cheat sheet/memo for a phone or foldable screen, especially with Russian text.

## Pattern that worked

1. If a source image exists, run vision/OCR first:
   - describe the layout/style/colors
   - extract all visible text with structure
   - mark unclear lines as `[неразборчиво]`; do not invent
2. Generate only the background/layout with `image_generate`:
   - `aspect_ratio: portrait`
   - prompt asks for light/dark style, card placeholders, separators
   - explicitly forbid readable text, words, numbers, logos
3. Programmatically render exact text over the generated background using PIL/SVG/HTML:
   - use local fonts with Cyrillic support, e.g. DejaVu Sans/Inter if installed
   - fit/crop the gpt-image background to the target pixel dimensions
   - draw translucent cards, badges, section headers, bullets, footer
4. QA before delivery:
   - verify final dimensions and file type
   - run vision/OCR on the final image for readability, cropping, overlaps, stale text, and neural garbage
   - fix small copy/layout issues before sending

## Honor Magic V5 / Honor foldable inner screen

For a request like “формат под мой телефон”, “формат под внутренний экран Honor Flip/V5”, or a correction after a wrong poster format, use the configured target unless he names another device:

- `2172 × 2352 px`
- light background if he says “на светлом фоне” — do not adapt a dark poster by merely brightening it; rebuild the composition in light mode
- use `image_generate(aspect_ratio="portrait")` for the background, then crop/fit to exact canvas
- keep text in cards/columns sized for reading on the phone, not for desktop print

Hermes `image_generate` does not accept exact pixel dimensions, so exact device sizing must be done in the local composition step.

## Prompt skeleton for background-only generation

```text
Create a clean light-mode business infographic background for a foldable phone inner screen. White / very light warm gray background, subtle paper texture, faint thin grid lines, soft corporate accents, rounded translucent card placeholders arranged in vertical sections, small pastel badge placeholders, premium mobile UI, lots of readable white space. IMPORTANT: no words, no letters, no numbers, no logos, no readable text; only abstract layout shapes and subtle separators. High resolution, crisp, elegant.
```

## Common pitfall

Do not ask GPT-image-2 to render long Russian copy directly. It will likely introduce misspellings, missing words, or fake glyphs. Use it for the background and exact local rendering for the text.