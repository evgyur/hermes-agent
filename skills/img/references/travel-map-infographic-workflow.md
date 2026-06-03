# Travel map / place-list infographic workflow

Use when Chip asks to generate a travel map, bucket-list map, city map, cafe/place map, or a poster “in this style” from a reference image.

## Core pattern

1. Treat the reference image as **style/layout direction only** unless Chip explicitly asks to edit/copy it.
2. Generate a clean illustrated base with the image model:
   - ask for no readable text, no labels, no numbers, no logos, no watermark, no social overlay;
   - reserve whitespace for deterministic labels/legend;
   - for city maps, name only the target city/region and explicitly exclude nearby regions that should not appear.
3. Add all real text deterministically with PIL/HTML/SVG:
   - title;
   - numbered pins;
   - map callout labels;
   - bottom/side legend with descriptions;
   - category colors.
4. Run vision QA before delivery. For map infographics, check:
   - exact title/subtitle;
   - map has numbers + names if requested;
   - legend descriptions are readable;
   - names on map match names in legend;
   - no clipping/overlap;
   - no connector lines crossing label text;
   - no unwanted regions/labels from the prompt exclusions;
   - no fake generated text/logos/watermarks.
5. If QA fails, repair deterministically first. Common repairs:
   - draw connector lines before text panels so panels cover line collisions;
   - widen/shorten crowded label boxes;
   - move dense labels apart;
   - align map callout names with legend names exactly;
   - translate accidental English filler into Russian when the user asked for Russian.
6. Deliver only the final image, with one short sentence.

## Chip-specific preferences

- For travel maps, Chip likes the polished illustrated bucket-list style, but wants practical place data, not fake generic landmarks.
- If he asks for “чисто <city>”, explicitly exclude neighboring itinerary regions from the generation prompt and QA the final labels for leakage.
- Prefer Russian legends/descriptions when Chip asks in Russian. Place names can stay in English/Vietnamese; explanations should be Russian.
- Map labels may be short only if Chip did not ask for exact consistency. If he asks “на карте цифры с номерами и названиями”, make the map names match the legend names.

## Prompt template for base generation

```text
Create a premium vertical illustrated travel infographic map in watercolor/isometric travel-poster style, focused ONLY on <city/region>.

Reference role: use the attached image only for high-level style direction: parchment/beige background, lush illustrated map, turquoise sea/green land/tropical details, elegant editorial travel-poster composition. Do not copy exact layout, title, or branding.

Reserve <bottom/side> clean cream/parchment area for deterministic labels/legend later.
Include: <landmarks/places/visual icons>.
Exclude: <neighboring regions/places that should not appear>.

Critical constraints: NO readable text, NO letters, NO numbers, NO labels, NO watermark, NO social media overlay, NO fake logos.
```

## Deterministic overlay checklist

- Use a font with Cyrillic and Vietnamese diacritics support, e.g. DejaVu Sans/Serif.
- Keep title panel separate from map labels.
- Use numbered markers with category colors:
  - food/cafes: terracotta;
  - places/beaches/base: green;
  - day trips/attractions: gold.
- Put descriptions in the legend, not on the busy map.
- When using leader lines, draw them below panels/labels or route them around text.
- Verify final dimensions and run vision QA before sending.
