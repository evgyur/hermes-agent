# /img Manual Review Checklist

- [ ] Frontmatter has `name: img` and command metadata `/img`.
- [ ] Description explicitly says GPT-image-2 and Codex OAuth route.
- [ ] Skill text matches Hermes tool schema: no unsupported `model`, `size`, `image`, or `images` args in the actual call.
- [ ] Config locks backend to `openai-codex`; no fal.ai fallback.
- [ ] Auth/quota failures are reported directly and do not trigger paid fallback.
