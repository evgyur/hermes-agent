---
name: img
description: "Canonical image generation, reference-image editing, deterministic overlays, exact text/logo/QR composition, and production image QA for Hermes/Powerpack users. Use for /img, image generation/editing, posters, infographics, covers, reference-image edits, exact text overlays, QR/logo insertion, and visual prompt refinement. For branded cover systems, route visual planning through chip-cover first, then use img as the rendering/composition engine."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  clawdbot:
    command: /img
    emoji: 🎨
  hermes:
    tags: [image-generation, image-editing, gpt-image-2, codex-oauth, deterministic-overlay, qa]
    related_skills: [chip-cover, tg]
    config:
      image_gen.provider: openai-codex
      image_gen.model: gpt-image-2-high
---

# img — Canonical Image Engine

`img` is the canonical image generation and editing engine for Hermes/Powerpack users.

It absorbs the former `chip-img` production rules. `chip-img` is now only a compatibility alias.

## Contract

Use Hermes `image_generate` for pure text-to-image requests and the Codex reference-image workflow for reference-dependent edits. Use deterministic local composition for exact text, official logos, QR codes, captions, and pixel-preserving edits.

Hard rails:

- rely on Hermes config `image_gen.provider=openai-codex` + `image_gen.model=gpt-image-2-high` for normal `/img` generation;
- do not use fal.ai, FAL_KEY, Nano Banana, OpenRouter, or OpenAI API-key billing as silent fallback;
- do not pass unsupported fields to `image_generate` (`model`, `size`, `quality`, `image`, `images`) unless the tool schema changes;
- if reference images matter, do not pretend prompt-only generation preserves them;
- if exact text matters, render text deterministically.

## Trigger

Use this skill for:

- `/img <prompt>`;
- image generation or image editing requests;
- posters, infographics, avatars, memes, maps, visual prompts;
- reference-image edits and style/identity/layout preservation;
- exact Russian text overlays;
- official logo or QR insertion;
- rendering/composition handoff from `chip-cover`.

Do not use for pure diagrams when a diagram skill is better, unless the user explicitly wants a rendered image.

For branded covers or cover series, load/use `chip-cover` first. `chip-cover` writes the visual spec; `img` renders/generates/composes.

## Routing decision tree

```text
Input arrives
  ├─ branded cover / cover series? -> chip-cover first, img renders/composes
  ├─ reference image controls identity/style/layout? -> Codex reference-image workflow
  ├─ exact text/logo/QR/table? -> deterministic overlay/composition
  ├─ pixel-preserving edit? -> local PIL/ImageMagick/OpenCV edit
  └─ pure text-to-image -> Hermes image_generate(prompt, aspect_ratio)
```

## Pure text-to-image workflow

1. Read the user brief.
2. Preserve explicit constraints.
3. Expand the prompt internally with subject, output type, composition, lighting, material, style, palette, camera/rendering detail, and negative constraints.
4. Choose Hermes-supported aspect ratio only: `square`, `landscape`, or `portrait`.
5. Call `image_generate` with `prompt` and `aspect_ratio` only.
6. If exact pixel size was requested, resize/export deterministically after generation.
7. Run QA when the asset is production-facing or contains text/logo constraints.
8. Deliver media or return the file path to the caller skill.

## Reference-image workflow

Use when the user provides an image and asks for likeness, face match, style transfer, poster edit, mascot/logo continuity, layout preservation, or “как на референсе”.

Rules:

- send the actual reference image as `input_image` through the Codex Responses image-generation route;
- prefer `scripts/codex_reference_image_generate.py`;
- assign reference roles in the prompt: identity/face, style, layout, palette, background, object/material;
- for identity, say preserving the attached person’s identity is higher priority than composition;
- run vision QA before delivery;
- if QA/user says it does not match, retry through reference-image workflow, not prompt-only.

See `references/reference-image-workflow.md`, `references/identity-preserving-codex-image.md`, and `references/target-poster-face-swap.md`.

## Deterministic edit workflow

Use local deterministic processing when preserving pixels or exact content matters:

- crop/resize/upscale;
- brighten face, dim background, avatar prep;
- meme captions;
- exact Russian text;
- official logos;
- QR codes;
- benchmark/data rows;
- cover overlays.

Use PIL/SVG/HTML composition. Generate QR locally. Fetch/use official logo assets; never ask an image model to draw real logos or QR codes.

See `references/deterministic-overlays.md` and scripts:

- `scripts/add_meme_caption.py`
- `scripts/compose_logo_qr.py`
- `scripts/photo_4k_superres.py`

## Text-heavy / exact text workflow

For Russian posters, command cheat sheets, maps, title covers, benchmark tables, and anything where spelling matters:

1. Generate a no-text background when needed: no text, no letters, no logos, no watermark.
2. Add all exact text deterministically with Cyrillic-capable fonts.
3. Use bounded text boxes and autofit.
4. QA exact spelling, readability, clipping, overlap, and stale context.
5. Repair and QA again if needed.

Do not deliver pretty-but-wrong text.

## Logo / QR / official asset workflow

- Use official/sourceable logo assets or user-provided assets.
- Preserve aspect ratio.
- Use CairoSVG/PIL if SVG rendering is needed.
- Generate QR codes locally with high error correction.
- Verify placement, no overlap/cropping, and QR scannability when feasible.

Operator-specific example: when Chip explicitly asks to place real company logos on an editorial/news cover, execute with official/sourceable logos unless the request would mislead, forge endorsement, phish, or impersonate.

## Cover handoff contract

When called by `chip-cover`, receive a structured visual/render request and return a concrete file.

Input shape:

```yaml
mode: cover_background | cover_reference_edit | cover_final_compose
brand: human20 | hlru | <new-brand>
canvas: {width: 1080, height: 1080, aspect: square}
background_prompt: "..."
negative_prompt: "no text, no letters, no logos, no watermark"
reference_images: []
overlay_spec_path: /tmp/chip-cover/specs/<id>.overlay.yaml
output_base: /tmp/chip-cover/backgrounds/<id>.png
output_final: /tmp/chip-cover/finals/<id>.png
qa_required: true
```

Return shape:

```yaml
image_path: /absolute/path.png
mode_used: image_generate | codex_reference | deterministic_overlay | local_edit
size: 1080x1080
qa_summary: ok | needs_fix | blocked
notes: []
```

`img` does not decide brand CTA, logo rules, or cover series DNA. That belongs to `chip-cover`.

## Aspect ratio / exact size rules

- `square`: social tiles, covers, avatars, icons.
- `landscape`: article covers, banners, wide visuals.
- `portrait`: stories, vertical posters, phone layouts.

If exact pixel size is requested and generation returns a provider default, resize deterministically with PIL/Lanczos and deliver the exact-size copy.

## QA contract

Before delivery for production assets, verify:

- file exists;
- dimensions/format;
- exact text;
- no pseudo-text/watermark/fake logo;
- no clipping/overlap;
- source/media match;
- no stale prior-task names;
- logo/QR correctness when present.

For branded covers, let `chip-cover` own brand-specific QA and use `img` QA for rendering/composition mechanics.

See `references/production-asset-qa.md`.

## Failure handling

If generation/auth/quota/tooling fails:

- state the exact blocker;
- do not fabricate a result;
- do not silently change providers;
- do not downgrade reference-image tasks to prompt-only;
- do not deliver exact-text assets without deterministic verification.

## Output Contract

Return either:

- delivered media as `MEDIA:/absolute/path` for direct image requests;
- or a structured handoff for caller skills: `image_path`, `mode_used`, `size`, `qa_summary`, and `notes`.

For `/tg` assets, return/handoff the file path for preview rather than sending unsolicited DM media.

## Output style

After successful image generation, reply in Russian with one short sentence and include `MEDIA:/absolute/path` when delivering media directly.

For `/tg` post assets, do not DM by default. Return/handoff the media path for the Telegram preview flow.

## Done Criteria

- Correct route chosen: pure generation, reference image, deterministic edit, or cover handoff.
- Required helper script/path exists.
- Final image exists.
- QA run when required.
- No silent provider fallback.

## Quick Test Checklist

- [ ] `/img cyberpunk city at night` calls `image_generate` with prompt + aspect_ratio only.
- [ ] Reference face/avatar request uses Codex reference-image workflow.
- [ ] Exact Russian title uses deterministic overlay.
- [ ] Official logo/QR insertion uses deterministic composition.
- [ ] Exact 1080×1080 request exports exact dimensions.
- [ ] Branded cover request routes through `chip-cover` first.

## References

- `references/openai-gpt-image-2.md`
- `references/gpt-image-2-prompting.md`
- `references/reference-image-workflow.md`
- `references/deterministic-overlays.md`
- `references/production-asset-qa.md`
- `references/text-heavy-mobile-infographics.md`
- `references/identity-preserving-codex-image.md`
- `references/target-poster-face-swap.md`
- `references/mascot-reference-text-workflow.md`
- `references/integrated-3d-text-on-mascots.md`
- `references/travel-map-infographic-workflow.md`
- `references/travel-infographic-map-workflow.md`
