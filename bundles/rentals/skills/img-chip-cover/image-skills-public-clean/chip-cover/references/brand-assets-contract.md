# Brand assets contract

This public skill does not include private logos, mascots, source examples, or generated background pools. Add your own brand pack here:

```text
chip-cover/
  brands/
    <brand-slug>/
      DESIGN.md
      assets.md
      qa.md
      examples.md
      renderer.md
  assets/
    <brand-slug>/
      manifest.json
      logos/
        primary.svg
        primary.png
        mono.svg
      mascots/
        latest/
          manifest.json
          contact-sheet.jpg
          01-neutral.png
      backgrounds/
        manifest.json
        bg_01.png
      examples/
        approved-cover-01.png
        approved-cover-02.png
```

`assets/<brand-slug>/manifest.json` example:

```json
{
  "brand": "brand-slug",
  "version": "2026-01-01",
  "logo_primary": "logos/primary.svg",
  "logo_png": "logos/primary.png",
  "mascot_latest": "mascots/latest",
  "background_pool": "backgrounds",
  "approved_examples": "examples",
  "notes": "Describe identity, safe zones, forbidden distortions, and required QA checks."
}
```

What to include for good results:

- official logo SVG and PNG;
- transparent mascot/product renders;
- approved covers/screenshots as style references;
- brand colors and fonts;
- CTA wording rules;
- forbidden examples and model failure modes;
- platform safe zones.

What not to include:

- secrets, API keys, private chat exports, credentials;
- unlicensed third-party logos unless you have rights;
- production-only paths that another operator cannot access.
