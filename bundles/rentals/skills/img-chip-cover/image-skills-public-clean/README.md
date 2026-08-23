# Public-clean image skills bundle

This bundle contains two sanitized agent skills:

- `img` — image generation/editing/composition engine.
- `chip-cover` — branded cover art-director/router that calls `img` for rendering.

Sanitization notes:

- No private brand logos, mascot assets, Telegram IDs, infrastructure paths, secrets, tokens, or customer-specific runbooks are included.
- Brand-specific references were replaced with placeholder contracts that tell you where to place your own references.
- Scripts are generic and do not depend on private infrastructure.

Install by copying each skill folder into your agent's skills directory, for example:

```bash
cp -R img chip-cover ~/.hermes/skills/
```

Then load `chip-cover` for branded covers and `img` for ordinary image work.
