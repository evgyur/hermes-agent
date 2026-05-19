---
name: piapi-video-toolkit
description: Use when choosing PiAPI video models, comparing Seedance/Veo/Kling/Wan, estimating 5s/10s/15s generation cost, planning generate -> remove watermark -> download workflows, or preparing a concise client-ready video API recommendation.
---

# PiAPI Video Generation

Use this skill when the task is about PiAPI video generation: model choice, pricing, duration tradeoffs, or production workflow design.

## What this skill is

This is the bundled Power Setup video-generation skill. PiAPI is the default public video-generation provider surface.

It does:

- compare PiAPI-accessible video models;
- estimate generation cost from published per-second pricing;
- explain duration, quality, audio, and watermark-removal tradeoffs;
- shape production recommendations.

It does not:

- create PiAPI accounts;
- provide or embed API keys;
- hide credentials in public templates.

Actual API calls require `PIAPI_API_KEY` plus a PiAPI-connected tool, script, CLI, or plugin in the host environment. The public skill supplies the video-generation decision layer and provider contract without shipping secrets.

## Default workflow

For finished outputs, use:

```text
generate -> remove watermark -> download
```

Temporary output URLs should be downloaded quickly. Keep provider credentials in `.env` or local config, never in a public skill or setup template.

## Model selection rules

- Fast testing and prompt iteration: `seedance-2-fast-preview`
- Better final Seedance renders: `seedance-2-preview`
- Best value at high quality: `wan-2.6 1080p`
- Premium realism and strongest output quality: `veo-3.1`
- Commercial/ad-style output and stronger control: `kling-3.0 omni`

Read `references/models.md` when comparing models.

## Pricing rules

- For `5s`, `10s`, or `15s`, compute direct cost from the published price per second.
- If watermark-removal pricing is not publicly disclosed, say so explicitly.
- Never present an invented final total if part of the pipeline is undisclosed.
- If a public watermark-removal price exists for a model, show it separately.

Read `references/pricing.md` when answering pricing questions.

## Output shape

For a simple recommendation:

1. short comparison;
2. model pick;
3. cost note;
4. workflow;
5. final recommendation.

For a budget question:

- show price per second;
- show 5s and 10s totals when relevant;
- call out whether watermark removal is included, excluded, or undisclosed.

## Important constraint

This skill is universal. Do not anchor it to a niche like tourism, golf, real estate, ecommerce, or social content unless the user explicitly asks for that context.
