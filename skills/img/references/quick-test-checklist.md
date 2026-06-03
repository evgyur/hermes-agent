# /img Quick Test Checklist

- [ ] `hermes config` shows `image_gen.provider: openai-codex`.
- [ ] `hermes config` shows `image_gen.model: gpt-image-2-medium`.
- [ ] `hermes tools list --platform telegram` shows `image_gen` enabled.
- [ ] `/img cyberpunk city at night` loads this skill and calls `image_generate` with prompt + aspect ratio only.
- [ ] Tool result returns `success: true`, `provider: openai-codex`, and a local cached image path.
- [ ] No fal.ai / `FAL_KEY` / paid OpenAI API-key fallback is used.
