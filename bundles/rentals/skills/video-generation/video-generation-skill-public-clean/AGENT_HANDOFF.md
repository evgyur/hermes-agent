# Agent handoff

This is a public-clean skill bundle for video-generation work.

## Install

1. Copy the folder `video-generation-skill-public-clean/` into your agent skills directory.
2. Load `video-generation-router` when a user asks to generate, verify, or evaluate AI video output.
3. Connect the workflow to your own provider runtime. This bundle does not include credentials or private infrastructure paths.

## Read first

- `SKILL.md` — main behavior contract.
- `references/review-checklist.md` — required QA before saying a video is ready.
- `references/provider-evaluation.md` — provider/model decision framework.
- `references/provider-cli-template.md` — placeholder wrapper pattern.

## Verification

Before delivering any generated video, run:

```bash
ffprobe -v error -show_entries format=duration,bit_rate   -show_entries stream=codec_name,width,height,r_frame_rate   -of json output.mp4
```

Then create and inspect a contact sheet:

```bash
ffmpeg -y -i output.mp4 -vf "fps=1,scale=320:-1,tile=5x3" contact_sheet.jpg
```

## Privacy status

Public-clean. No secrets, private paths, chat IDs, or customer-specific operational notes are included.
