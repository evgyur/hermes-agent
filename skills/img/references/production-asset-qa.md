# Production asset QA

Before delivering production images, check the actual image, not just the prompt.

## Always check

- final file exists;
- dimensions and format match request;
- exact text is present and readable;
- no pseudo-text, watermark, fake logo, or broken Cyrillic;
- no right/bottom clipping;
- no stale names from earlier drafts;
- official logos/QR codes are sourceable and undistorted;
- visual matches the current brief, not a previous similarly named artifact.

## Covers

For branded covers, defer visual-spec and brand QA to `chip-cover`; `img` verifies rendering/composition mechanics.
