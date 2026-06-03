# Integrated 3D text on generated mascots

Session lesson: when the user asks to place text on a mascot/object, a deterministic PIL overlay can be useful for quick exact text, but it often reads as a flat sticker/watermark. If the user wants the text to feel like part of the character or product, use the reference-image GPT-Image-2 path and make the text a physical design element.

## Better prompt directions

Use one of these instead of “add text on the body”:

- **Inset OLED/nameplate:** a curved rounded glass panel embedded in the lower shell, bevels/reflections, text rendered as cyan segmented display letters.
- **Dot-matrix display strip:** narrow analog LED strip under the visor, visible tiny pixels, glass cover, built into the shell.
- **Raised channel letters:** small beveled 3D letters mounted to the shell, following curvature, internal blue glow/backlit edges.
- **Forehead micro-badge:** slim arched translucent panel between wings/above visor, clipped into ceramic shell, micro-LED text.
- **Side service module:** attached side pod with bevels/seams, not hanging/floating; text on a tiny integrated display.
- **Glass capsule/hologram:** transparent pill-shaped capsule embedded into lower shell, text floats inside the capsule and casts reflections; avoid a loose floating decal.

## Exact prompt constraints

Always specify:

- Text must be exactly the requested string, e.g. `human20.app`.
- The text is the only readable text in the image.
- It must not be mirrored, garbled, misspelled, or pseudo-text.
- It must be physically integrated into the robot/object, not on the background, not a watermark, not a flat overlay.
- It must not overlap face/visor/eyes/wings unless deliberately using a display panel designed for that area.

## QA

Run image QA before delivery and ask explicitly:

- Is the text exact and readable?
- Does it look like a 3D/inset/display element rather than a sticker?
- Is it on the object, not on the background?
- Does it preserve the mascot identity and avoid covering key features?

If 1–2 variants are weak, regenerate replacements before sending the set. In this session, side badges and holographic labels tended to fail when they looked detached or too decal-like; OLED/dot-matrix/lower glass display variants were strongest.