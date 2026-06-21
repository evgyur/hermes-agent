# Target-poster face swap with identity reference

Use when the operator says “вот эта фото круче”, “ничего не меняй, только моё лицо”, “вставь моё лицо”, or asks to preserve an existing poster/composition while replacing a person’s face.

## Core rule

This is a two-reference edit, not a new generation:

1. `target_poster` / base image — must remain visually unchanged.
2. `face_ref` / identity image — used only for face/identity.

Send both as real `input_image` items to GPT-Image-2 via Codex Responses. Do not describe either image only in text.

## Prompt shape

Use a prompt like:

```text
Edit image 1. Image 1 is the target poster. Image 2 is the face identity reference.

Perform a minimal face-swap edit only.

KEEP IMAGE 1 THE SAME: same poster, same body, same robot/worm, same background, same dust, same typography, same logos/text, same clothing, same pose, same camera angle. Do not redraw the whole poster.

Replace only the rider's face with the identity from image 2.

GAZE/POSE: preserve the target pose. If the user asks “по ходу движения”, the new face must be in three-quarter profile and the eyes must look toward the movement direction, not at the viewer/camera.

Identity from image 2: preserve the specific face/identity 1:1; do not invent a generic actor; preserve distinctive hair, eyes, stubble, accessories/tubes.

Preserve exact text: <list all text exactly>.
```

## QA

Run `vision_analyze` before delivery and check:

- target composition still reads as the same poster;
- non-face elements were not redesigned;
- exact text/logos survived;
- face has reference identity cues;
- requested gaze direction is correct.

If QA says gaze is still frontal or the person looks generic, do not deliver. Regenerate with a stronger minimal-edit / gaze instruction.

## Fallback warning

Do not silently use FAL/Nano Banana fallback for this class. It may preserve style but often drifts identity. Only use fallback if the operator explicitly accepts possible likeness drift.