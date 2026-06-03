# Travel infographic map workflow

Use when Chip asks for a generated travel map, bucket-list poster, route map, or “same style as this travel infographic” with exact places/labels.

## Pattern

1. If a visual reference image is provided, use the reference-image GPT-Image-2/Codex workflow for the **base illustration**. Tell the model the reference is style-only, not content to copy.
2. Generate the base with **no readable text**: no labels, no numbers, no logos, no watermark, no social UI. Ask for clean side margins/whitespace for later callouts.
3. Pull the place list from the relevant travel skill/reference file before inventing labels. For Summer 2026 Da Nang/Hoi An, use `travel/summer2026` → `references/danang-hoian.md`.
4. Add title, labels, marker numbers, legends, and callout boxes deterministically with PIL/HTML/SVG using Cyrillic-capable fonts. Do not rely on image generation for readable Russian/Vietnamese labels.
5. QA with vision before delivery:
   - exact title and labels readable;
   - no fake/gibberish leftover text from the model;
   - no clipped text;
   - connector/route lines do not cross label text;
   - enough contrast on mobile.
6. If QA finds label/line issues, repair deterministically and re-QA. Common fix: draw leader lines and markers **before** text panels, then draw panels/text on top so labels remain clean.
7. Deliver only the final image unless Chip asks for the prompt/source.

## Pitfalls

- Do not let the model render the final map text; it will hallucinate labels or break Cyrillic.
- Do not copy social overlay/watermark/logo artifacts from the reference image.
- Do not overfit a decorative map into literal geography. For trip artifacts, prioritize recognizable landmarks + readable callouts over map accuracy.
- Do not treat a busy pretty map as done until vision QA confirms labels and lines are not fighting each other.
