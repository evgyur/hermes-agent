# Mascot reference-image text integration workflow

Use this when Chip iterates on a generated mascot/logo/icon and asks to add brand text, a badge, display text, engraving, or several stylistic variants.

## Lesson from Human20 mascot session

Chip rejected a deterministic PIL overlay even though it was positioned and QA-read as an engraving: it still looked “налепил на картинку”. For mascot/icon work where the text must feel physically part of the object, use the reference-image `img` generation path, not a post-composited overlay.

## Preferred workflow

1. Use the latest accepted image as the reference, not the earliest base image.
2. Prompt for the text as a physical product detail:
   - embedded OLED/nameplate
   - raised 3D channel letters
   - curved glass capsule display
   - dot-matrix strip
   - laser-etched/debossed ceramic groove
   - side service tag physically integrated into the shell
3. State exact text constraints:
   - only readable text is exactly the requested string;
   - not mirrored, not garbled, no pseudo-text;
   - no extra labels/UI copy.
4. If the user asks to explore, generate a contact sheet plus individual files. Make variants meaningfully different by placement/situation, not tiny prompt tweaks.
5. QA with image understanding before delivery:
   - identity matches reference;
   - exact text is readable;
   - text is physically integrated, not flat sticker/watermark;
   - no arms/fingers unless explicitly requested;
   - no clutter that weakens the mascot silhouette.
6. If QA flags weak variants, regenerate only the weak slots and rebuild the contact sheet before sending.

## Good variant set for mascot text

- OLED nameplate embedded in lower shell.
- Raised chrome/cyan 3D channel letters following curvature.
- Small forehead micro-badge between wings.
- Side-mounted service-status display integrated into shell.
- Retro dot-matrix display strip under visor.
- Transparent glass capsule display with luminous 3D text.

## Good variant set for mascot poses/situations

- Welcome pose through body tilt/wing gesture, avoiding hands/fingers unless needed.
- Agent-orb companions / rental console.
- Fast Hermes messenger with motion trails.
- Thinking/working with holographic cube or node.
- Support/operator with headphone emphasis and empty chat bubble/dots.
- Hero badge / app-icon pose with halo and small nodes.
