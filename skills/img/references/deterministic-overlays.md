# Deterministic overlays

Use deterministic composition when visible content must be exact or when the base image must be preserved.

## Use for

- exact Russian/Cyrillic titles;
- meme captions;
- command cheat sheets;
- QR codes;
- official logos;
- benchmark rows and numeric claims;
- social covers where CTA text must be exact;
- any repair request about missing/misspelled/cropped text.

## Rules

1. Generate backgrounds with `NO readable text, NO letters, NO numbers, NO logos, NO watermark`.
2. Draw exact text locally with PIL/SVG/HTML using a Cyrillic-capable font.
3. Preserve logo aspect ratio and use official/sourceable assets only.
4. Generate QR codes locally; never ask an image model to draw QR.
5. Use bounded text boxes with autofit; fail QA instead of clipping.
6. Run vision/OCR QA when the asset is user-visible.

## Repair loop

- text clipped -> reduce font, adjust line breaks, expand box, rerender;
- fake text in background -> regenerate no-text background;
- logo wrong -> replace with official asset, rerender;
- QR unreadable -> regenerate QR with higher error correction and nearest-neighbor scale;
- text looks pasted on a 3D object -> switch to reference-image generation and integrate as physical engraving/display, not overlay.
