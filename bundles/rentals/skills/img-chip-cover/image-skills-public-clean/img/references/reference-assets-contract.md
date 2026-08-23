# Reference asset contract

Place brand or project references outside this public skill, then point the skill/run at them.

Recommended structure:

```text
assets/
  brand-name/
    manifest.json
    logos/
      primary.svg
      primary.png
      mono.svg
    mascots/
      latest/              # symlink or copied canonical set
        manifest.json
        contact-sheet.jpg
        01-neutral.png
        02-explaining.png
    colors.json
    fonts.json
    examples/
      cover-01.png
      cover-02.png
```

`manifest.json` should include:

```json
{
  "brand": "brand-name",
  "version": "2026-01-01",
  "allowed_uses": ["covers", "posters", "reference_generation"],
  "logo_primary": "logos/primary.svg",
  "logo_png": "logos/primary.png",
  "mascot_latest": "mascots/latest",
  "notes": "Describe identity constraints, forbidden distortions, and required QA checks."
}
```

Good reference sets include:

- official logo SVG + PNG;
- canonical mascot/character/product renders on transparent or clean background;
- 3–10 approved examples;
- brand colors and font names/files;
- forbidden examples if the model often makes a specific mistake.

Do not store secrets, private chat exports, credentials, or customer data in the asset folder.
