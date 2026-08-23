---
name: video-generation-router
description: "Public-clean skill for routing video-generation requests, running provider jobs, verifying outputs, and evaluating video models/providers."
---

# video-generation-router

A public-clean skill for agents that create AI-generated videos from text prompts, images, or reference media.

Use this skill when the user asks to:
- generate/render a video from text, an image, or references;
- choose between video providers or models;
- run a provider job, poll status, download output, or cancel a task;
- verify a generated MP4 before delivery;
- evaluate whether a new provider is worth integrating.

## Core principle

If the user asks for a real video, submit a real provider job. Do not substitute a local slideshow, GIF, HTML animation, or simulated artifact as the main deliverable unless the user explicitly asked for a mockup.

## Provider routing

| User intent | Default route |
|---|---|
| Fast/cheap smoke test | Use the cheapest wired provider/model that can return a short preview. |
| First serious render | Use the best currently wired provider/model for quality. |
| Image-to-video / identity | Use a provider with explicit image/reference support; verify likeness after download. |
| Exact text/logo | Generate the scene first, then add crisp text/logo overlays in post-processing. |
| Unsupported provider request | Check docs/current integration first; do not claim it is wired. |
| New provider evaluation | Classify as `use now`, `watchlist`, or `ignore` after checking API, cost, rights, and a smoke test. |

## Execution workflow

1. Capture the user's creative intent: subject, shot, style, aspect ratio, duration, references, and delivery format.
2. Pick a provider/model that is actually available in the current environment.
3. Submit the provider task with explicit parameters.
4. Poll until success/failure/timeout; keep the provider task ID.
5. Download the original output immediately; provider URLs may expire.
6. Run mechanical QA with `ffprobe` and create a contact sheet.
7. Inspect visual quality: prompt fit, artifacts, reference likeness, text/logo readability, aspect ratio.
8. Deliver the MP4 and include compact proof: provider/model, duration, resolution, bitrate, task ID if shareable, and QA verdict.

## QA commands

```bash
ffprobe -v error -show_entries format=duration,bit_rate   -show_entries stream=codec_name,width,height,r_frame_rate   -of json output.mp4
```

Create a contact sheet:

```bash
ffmpeg -y -i output.mp4 -vf "fps=1,scale=320:-1,tile=5x3" contact_sheet.jpg
```

## Output contract

For generated videos:
- final `.mp4` as the deliverable;
- provider/model actually used;
- prompt summary, not necessarily the full private prompt;
- resolution, duration, bitrate/codec when available;
- QA verdict and any limitations.

For provider/model evaluation:
- verdict first: `use now`, `watchlist`, or `ignore`;
- why it matters;
- integration boundary: API available, CLI wired, self-host only, or research-only;
- concrete next step if worth tracking.

## Honesty gates

- Do not call low-resolution output “high quality”.
- Do not hide aspect-ratio or duration mismatches.
- Do not claim unsupported models or providers were used.
- Do not invent pricing; check current provider docs.
- Do not expose secrets, API keys, local paths, chat IDs, customer names, or private operational notes in handoffs.

## References

- `references/provider-evaluation.md`
- `references/review-checklist.md`
- `references/provider-cli-template.md`
