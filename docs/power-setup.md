# Hermes Power Setup MVP

Status: public-safe MVP foundation.

Hermes Power Setup is an opinionated multimodal setup layer for Hermes Agent. It is designed to be easy to deploy without importing any private operator overlay.

## What the default pack includes

- Voice studio: STT, TTS, `/say`, `/voice on`, `/voice tts`.
- Vision/doc intake: image understanding, screenshot analysis, OCR/PDF dependency checks.
- Image studio: `/img`-style image generation and deterministic asset-composition guardrails.
- Video generation: PiAPI-backed video generation for Seedance/Veo/Kling/Wan model choice, pricing, and `generate -> remove watermark -> download` workflows. The public pack ships provider config and the bundled skill; `PIAPI_API_KEY` stays local.
- Model profile switching foundation: compatibility quick commands `/gptprof`, `/gptt`, and `/mmfast` for public profile discovery and fast/specialized model routes.
- Compatibility command pack: `/say`, `/img`, and `/video` wrappers that call the public TTS, image generation, and PiAPI video-generation surfaces when their providers are configured.
- Telegram user bridge foundation: single Telethon-client companion service pattern.
- Browser relay, media intake, cron/watchdogs, memory/skill hygiene, MCP/webhook starter modules.
- Ops doctor: local readiness checks and secret-scan guardrails.

## Explicitly not included by default

- `tg`
- `postcraft`
- Product-specific CTA rules, ban-word lists, channels, or business logic
- Operator user ids, Telegram chat ids/topics, or account defaults
- `.env`, `.session`, `auth.json`, OAuth access/refresh tokens
- private memory, operating-system, or project-runtime state

The product boundary is: publish mechanisms and convenience, not a private operator system.

## Commands

```bash
hermes power inventory
hermes power install --dry-run
hermes power install
hermes power doctor
hermes power secret-scan
hermes setup power
```

`hermes power install` writes a conservative config preset only. It does not publish, push, copy private overlays, or move secrets.

## MVP acceptance

A fresh public install should be able to:

1. apply the default preset;
2. see `tg` and `postcraft` excluded from default modules;
3. run voice readiness checks for STT/TTS;
4. run auxiliary vision readiness checks;
5. verify image-generation readiness through the `image_gen` surface;
6. verify PiAPI video-generation readiness through the bundled `piapi-video-toolkit` skill and local `PIAPI_API_KEY` env hook;
7. run a public-artifact secret scan.

`hermes power inventory --json` exposes the smoke surfaces as `stt`, `tts`, `auxiliary_vision`, `image_generation`, and `video_generation`. Templates list these surfaces without private keys; real provider credentials stay in `.env` or the user's local config.

## Private overlay rule

Private overlay can extend the public setup locally, but it must never be required for `hermes power doctor` to run or for a fresh public user to understand the install path.
