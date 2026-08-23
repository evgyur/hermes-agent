# Reference-image workflow

Use this when identity, object continuity, style, composition, or layout must be preserved.

1. Collect references and assign roles:
   - identity/person/character;
   - style/palette;
   - layout/composition;
   - product/object;
   - background/material.
2. Write a prompt that explicitly says what must be preserved and what may change.
3. Use a reference-image-capable image route. Prompt-only generation is not enough.
4. For batches, create numbered prompt files and output filenames.
5. Build a contact sheet.
6. QA the sheet for identity drift, repeated artifacts, text/logo hallucinations, cropping, and composition misses.
7. Regenerate only failed variants with specific negative constraints.
8. Deliver final selected images plus source prompts if the user asks for sources.

Prompt skeleton:

```text
Use the attached reference image as the primary identity/style source.
Preserve: [face/character/product/logo shape/layout].
Change: [pose/background/lighting/context].
Do not add: fake text, fake logos, watermarks, extra limbs, distorted hands, cropped subject.
Output: [aspect ratio, style, mood, production format].
```
