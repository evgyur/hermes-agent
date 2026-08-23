---
name: img
description: "Public-clean image generation, reference-image editing, deterministic overlays, exact text/logo/QR composition, and production image QA."
version: 1.0.0-public
license: MIT
metadata:
  hermes:
    tags: [image-generation, image-editing, deterministic-overlay, qa]
    related_skills: [chip-cover]
---

# img — Public Image Engine

`img` is the image-generation and image-composition engine. It chooses the safest route for a visual task: pure text-to-image, reference-image generation, deterministic overlays, or local pixel-preserving edits.

## Contract

Use the host agent's configured image-generation tool for pure text-to-image. Use a reference-image-capable workflow when identity, style, layout, or product/mascot continuity matters. Use deterministic local composition for exact text, official logos, QR codes, tables, labels, captions, and pixel-preserving edits.

Hard rails:

- do not silently switch providers or billing routes;
- do not pretend prompt-only generation preserves a supplied reference;
- do not ask an image model to draw exact logos, QR codes, or production text;
- if exact text matters, render it deterministically after generation;
- if a generation/auth/quota/tooling call fails, report the blocker instead of fabricating output.

## Trigger

Use this skill for:

- image generation and image editing requests;
- posters, infographics, avatars, memes, maps, product/gift posters;
- reference-image edits and style/identity/layout preservation;
- exact text overlays;
- official logo or QR insertion;
- rendering/composition handoff from `chip-cover`.

If the task is specifically a branded cover or cover series, load `chip-cover` first. `chip-cover` writes the visual spec; `img` renders/composes.

## Routing decision tree

```text
Input arrives
  ├─ branded cover / cover series? -> chip-cover first, img renders/composes
  ├─ reference image controls identity/style/layout? -> reference-image workflow
  ├─ exact text/logo/QR/table? -> deterministic overlay/composition
  ├─ pixel-preserving edit? -> local PIL/ImageMagick/OpenCV edit
  └─ pure text-to-image -> configured image generation tool
```

## Pure text-to-image workflow

1. Preserve the user's explicit constraints.
2. Expand the prompt internally with subject, output type, composition, lighting, style, palette, camera/rendering detail, and negative constraints.
3. Choose only the aspect ratios supported by your runtime.
4. Generate the image.
5. If exact pixel size was requested, resize/export deterministically after generation.
6. Run QA for production-facing assets.
7. Deliver the final media or return a path to the caller skill.

## Reference-image workflow

Use when the user provides an image and asks for likeness, face match, style transfer, poster edit, mascot/logo continuity, layout preservation, or “like this reference”.

Rules:

- pass the actual reference image to a reference-image-capable generation/editing route;
- assign reference roles in the prompt: identity/face, style, layout, palette, background, object/material;
- for identity, explicitly prioritize preserving the attached person's identity;
- for mascot or character series, use a canonical asset folder and a manifest;
- for batch variants, create numbered prompt files, generate variants, build a contact sheet, QA, then regenerate only failed variants;
- run visual QA before delivery;
- if QA/user says the output does not match, retry through the reference workflow, not prompt-only.

See `references/reference-image-workflow.md` and `references/reference-assets-contract.md`.

## Deterministic edit / overlay workflow

Use deterministic processing when preserving pixels or exact content matters:

- crop/resize/upscale;
- local portrait cleanup or masked edits;
- background removal and QA on checker/dark backgrounds;
- meme captions;
- exact text;
- official logos;
- QR codes;
- benchmark/data rows;
- SVG/HTML diagrams, banners, avatars, and brand-safe technical graphics.

Use PIL/SVG/HTML composition. Generate QR locally. Fetch/use official logo assets; never ask an image model to draw real logos or QR codes.

See `references/deterministic-overlays.md`.

## Text-heavy / exact text workflow

For posters, cheat sheets, maps, title covers, benchmark tables, and anything where spelling matters:

1. Generate a no-text background when needed: no text, no letters, no logos, no watermark.
2. Add all exact text deterministically with language-capable fonts.
3. Use bounded text boxes and autofit.
4. QA exact spelling, readability, clipping, overlap, and stale context.
5. Repair and QA again if needed.

Do not deliver pretty-but-wrong text.

## Logo / QR / official asset workflow

- Use official/sourceable logo assets or user-provided assets.
- Preserve aspect ratio.
- Use SVG rendering when needed.
- Generate QR codes locally with high error correction.
- Verify placement, no overlap/cropping, and QR scannability when feasible.

## QA contract

Before delivery for production assets, verify:

- file exists;
- dimensions/format;
- exact text;
- no pseudo-text/watermark/fake logo;
- no clipping/overlap;
- source/media match;
- logo/QR correctness when present.

See `references/production-asset-qa.md`.

## Output contract

Return either:

- delivered media for direct image requests;
- or a structured handoff for caller skills:

```yaml
image_path: /absolute/path.png
mode_used: image_generate | reference_image | deterministic_overlay | local_edit
size: 1080x1080
qa_summary: ok | needs_fix | blocked
notes: []
```

## Done criteria

- Correct route chosen.
- Required helper script/path exists.
- Final image exists.
- QA run when required.
- No silent provider fallback.
