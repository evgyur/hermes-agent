# Reference-image workflow

Use when a user-provided image materially controls the output: identity, likeness, style, layout, poster continuity, mascot continuity, palette, or "как на референсе".

## Non-negotiable

Do not reduce the image to a text description when preservation matters. Prompt-only `image_generate` is for pure text-to-image. Reference-dependent output must send the actual image as `input_image` through the Codex Responses image-generation route or an equivalent future Hermes tool surface.

## Roles for multiple references

Name each reference role in the prompt:

- identity/face;
- style;
- layout;
- palette;
- background;
- object/material;
- target poster;
- logo/asset to preserve.

## QA

Run image understanding before delivery. Check likeness proxy, exact text, physical integration of text when requested, missing/cropped elements, and obvious AI deformations. If QA fails, retry through the same reference workflow; do not downgrade to prompt-only.
