# Provider CLI template

This is a generic shape for a provider wrapper. Replace placeholders with your own runtime.

## Environment

```env
VIDEO_PROVIDER_api key placeholder:...
VIDEO_PROVIDER_BASE_URL=https://provider.example
VIDEO_DOWNLOAD_DIR=downloads
VIDEO_POLL_INTERVAL_MS=10000
VIDEO_POLL_TIMEOUT_MS=900000
```

Never paste real API keys into public chats, repositories, or exported skill bundles.

## Commands

Submit a prompt-only job:

```bash
video-provider generate   --prompt "cinematic product reveal"   --aspect-ratio 9:16   --duration 5   --model provider-model-name
```

Submit with image references:

```bash
video-provider generate   --prompt "same person, cinematic portrait reveal"   --image ./reference-front.jpg   --image ./reference-side.jpg   --aspect-ratio 9:16   --duration 5
```

Check status:

```bash
video-provider status --task-id <task_id>
```

Download output:

```bash
video-provider download --task-id <task_id> --output output.mp4
```

Cancel task:

```bash
video-provider cancel --task-id <task_id>
```

## Wrapper requirements

- Return machine-readable JSON for submit/status/download where possible.
- Store task ID, provider, model, duration, aspect ratio, and output path.
- Preserve provider originals.
- Download temporary URLs immediately.
- Surface provider errors plainly instead of converting them into generic failure text.
