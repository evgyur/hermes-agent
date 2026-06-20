# Cartoon cover workflow

Recovered source:
`/home/hermes/workspace/human20-app-prod-email/skills/human20-meeting-publisher/references/cover-artwork.md`

This is the default thumbnail/poster style for both Human20 lessons and meetings.

## Output contract

- Final file: `frontend-v2/public/files/thumbnails/<id>.jpg`.
- Site payload path: `/files/thumbnails/<id>.jpg`.
- Canvas: `1280x720`, 16:9, RGB JPEG, high quality.
- Public-safe: no private links, tokens, chat URLs, transcripts, or unreleased customer data.
- Beta first. Do not overwrite prod without explicit operator approval.

## Visual direction

Make the cover a high-energy lesson/meeting thumbnail, not a generic still frame.

- Use the best video frame or provided portrait as the edit target.
- Preserve the real person, pose, room, key props, and useful signs.
- Apply a `50% cartoon / 50% photo` hybrid:
  - visible clean contour lines;
  - simplified painted shading;
  - saturated highlights;
  - still recognizable as a real photo/person.
- Add topic-specific sticker effects only when they match the material.
- Keep overlays away from eyes, mouth, hands, microphone controls, and critical UI/signage.

## Image edit prompt template

```text
Edit the provided lesson/video frame into a 50% cartoon / 50% photo hybrid cover thumbnail.

Use case: style-transfer
Asset type: Human20 lesson/meeting cover thumbnail
Primary request: create a high-energy cover for the topic: <topic>
Style/medium: hybrid photo-cartoon, high-end YouTube thumbnail look, visible ink-like contour lines, smooth painted shading, realistic photo depth, saturated neon or studio lighting, crisp graphic highlights.
Composition/framing: preserve the original person identity, pose, room, important props, and webcam framing; make the face and gesture the main focal point.
Topic effects: <topic-specific effects>
Lighting/mood: punchy, readable at small size, poster-like contrast.
Constraints: keep the same person recognizable; keep important signs readable; keep enough clean space for a lower-third title band.
Avoid: full anime conversion, face distortion, hand distortion, extra people, clutter, watermark, logo, unreadable text.
```

## Topic presets

- `travel`: dotted flight path, airplane, map pins, passport stamps, suitcase, compass, clouds.
- `skills`: skill cards, lightning marks, terminal chips, connected nodes.
- `agents`: agent nodes, relay lines, small command chips, automation sparks.
- `none`: no decorative topic effects.

## Deterministic title overlay

Model-generated Cyrillic is draft-only. Final text must be rendered locally.

Default lower-third:

- full-width bottom band, 18–24% of canvas height;
- rich green Human20-style background;
- large bold Cyrillic display text, white/warm cream;
- subtle dark shadow/outline;
- 32–56 px horizontal padding;
- text must not touch edges or cover face, mouth, hands, mic, or signage.

## Script behavior to preserve

Recovered script:
`/home/hermes/workspace/human20-app-prod-email/skills/human20-meeting-publisher/scripts/generate_cover.py`

Behavior:

- accepts `--video` + `--timestamp` or `--source-image`;
- center-crops to 16:9;
- resizes to `1280x720`;
- applies deterministic pseudo-cartoon stylization unless `--no-stylize`;
- draws topic effects;
- draws exact lower-third title;
- writes JPEG to `frontend-v2/public/files/thumbnails/<id>.jpg`;
- writes reusable `imagegen-prompt.txt` to private work dir.

Command shape:

```bash
python skills/human20-publisher/scripts/generate_cover.py \
  --item-id "lesson-YYYY-MM-DD-topic" \
  --title "EXACT TITLE" \
  --video "/path/to/source-video.mp4" \
  --timestamp "00:01:30" \
  --topic-effects skills \
  --site-root frontend-v2
```

Or from a generated/edit draft:

```bash
python skills/human20-publisher/scripts/generate_cover.py \
  --item-id "meeting-YYYY-MM-DD-topic" \
  --title "EXACT TITLE" \
  --source-image "/tmp/cover-draft.png" \
  --topic-effects agents \
  --site-root frontend-v2
```

## QA checklist

- [ ] Face and main gesture are recognizable.
- [ ] Hybrid photo-cartoon, not full anime.
- [ ] Topic effects match the material and do not cover key details.
- [ ] Exact lower-third title, readable at small size, not clipped.
- [ ] JPEG is `1280x720`.
- [ ] Site path is `/files/thumbnails/<id>.jpg`.
- [ ] No private chat links/tokens/transcript fragments are visible.
- [ ] Browser/vision smoke confirms it reads inside the target list/detail page.
