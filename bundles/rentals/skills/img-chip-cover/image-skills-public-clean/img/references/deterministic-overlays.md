# Deterministic overlays

Use deterministic overlays for exact text, official logos, QR codes, tables, captions, and compliance-sensitive labels.

Default pattern:

1. Generate or choose a no-text background.
2. Reserve clean zones for text/logo/CTA.
3. Render all copy with PIL/SVG/HTML using real fonts.
4. Place logos from official SVG/PNG assets.
5. Generate QR locally and verify it scans.
6. Export final PNG/JPG/PDF.
7. QA clipping, spelling, contrast, safe margins, and stale placeholders.

Do not use transparent fill rectangles over finished art unless you intend to erase/mask content. For outline-only frames, draw outline only.
