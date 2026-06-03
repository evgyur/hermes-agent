# Hermes GPT-image-2 routing for /img

`/img` must call Hermes `image_generate` with prompt + aspect ratio only.

Auth behavior:

- `image_gen.provider: openai-codex` routes through OpenAI Codex / ChatGPT OAuth.
- `image_gen.model: gpt-image-2-medium` selects the GPT-image-2 medium tier.
- Do not use fal.ai, `FAL_KEY`, Nano Banana, or paid OpenAI API-key fallback for Chip's Sigurd // Img chat.

Expected configured section:

```yaml
image_gen:
  provider: openai-codex
  model: gpt-image-2-medium
  openai-codex:
    model: gpt-image-2-medium
```

Useful tool call shape:

```json
{"prompt":"...","aspect_ratio":"landscape"}
```

## Thinking / reasoning caveat

Do not claim that `/img` or GPT-image-2 has a separate user-facing `thinking mode` unless the live tool/schema proves it. In the current Hermes OpenAI Codex image route:

- Hermes `image_generate` exposes only `prompt` and `aspect_ratio` to the agent.
- The Codex image plugin calls Responses with an `image_generation` tool using `model: gpt-image-2`, `size`, `quality`, `output_format`, `background`, and `partial_images`.
- The OpenAI SDK `responses.tool_param.ImageGeneration` type includes image-specific knobs such as `quality`, `size`, `background`, `input_fidelity`, etc.; it does **not** include `thinking`, `reasoning`, or `reasoning_effort` fields for the image-generation tool itself.
- Responses API has top-level `reasoning` for the host/reasoning model, but that is distinct from a GPT-image-2 image-generation thinking mode. At most, Hermes/the assistant can reason before writing a better prompt; it is not toggling hidden image-model thinking.
- `gpt-image-2-low|medium|high` in Hermes maps to image `quality`, not to thinking effort.

If asked, answer with the distinction: GPT-image-2 may have internal hidden processing, but the current Hermes/OpenAI image-generation surface exposes no separate thinking toggle for the image model.

Hermes currently does not expose reference-image edit inputs in the `image_generate` tool schema. For exact image edits, report this limitation instead of pretending the source image was edited.
