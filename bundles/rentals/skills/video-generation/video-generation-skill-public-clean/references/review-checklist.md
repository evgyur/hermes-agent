# Review checklist

Use before delivering a generated video.

## Mechanical QA

- [ ] `ffprobe` confirms duration, resolution, codec, and bitrate.
- [ ] Contact sheet generated and inspected.
- [ ] Aspect ratio matches the request, or mismatch is reported.
- [ ] File is the provider original unless a derivative is clearly labeled.
- [ ] Final deliverable is a playable `.mp4`.

## Visual QA

- [ ] Motion matches the prompt enough to be useful.
- [ ] Reference-image likeness is checked, not assumed.
- [ ] Subject, logo, product, or mascot shape is stable enough if relevant.
- [ ] Text/logos are readable; if not, use a crisp overlay instead of relying on generation.
- [ ] No obvious severe artifacts in faces, hands, brand elements, or scene transitions.

## Honesty gates

- [ ] Do not call low-resolution output high quality.
- [ ] Do not hide provider aspect-ratio failures.
- [ ] Do not claim unsupported models were used.
- [ ] Do not treat local post-processing as provider generation.
- [ ] Do not invent costs, rights, or watermark policy.
