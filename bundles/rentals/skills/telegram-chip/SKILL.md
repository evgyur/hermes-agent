---
name: telegram-chip
description: Use ChipCR's existing host-local Telegram runtime directly for Telegram reads, exports, and explicitly authorized writes.
---

# Telegram Chip

Use the existing user-owned `telegram-chip` HTTP service directly. Computer Use,
Telegram Desktop, browser automation, and web scraping must not be used for
Telegram operations when this runtime is available.

## Runtime

- Base URL: `$TELEGRAM_CHIP_BASE_URL` when set, otherwise `http://127.0.0.1:8080`.
- Verify `GET /health` before the first operation.
- Verify the acting account with `GET /me`; it must report `@ChipCR`.
- Discover request schemas from `GET /openapi.json`; do not guess endpoint fields.

## Safety contract

- Never print, copy, or request Telegram session material, API credentials, or
  the service environment file.
- Reads must use exact chat and message identifiers whenever the user provides
  them. Keep exports private and local.
- Writes are permitted only when the user has made the destination and action
  unambiguous. The service's write allowlist is authoritative; never widen it
  to `*` and never bypass a blocked write.
- After a write, record the returned message ID and fetch it back through the
  API before claiming success.
- Use bounded polling for replies and preserve exact message IDs as evidence.

## Basic checks

```bash
base="${TELEGRAM_CHIP_BASE_URL:-http://127.0.0.1:8080}"
curl -fsS "$base/health"
curl -fsS "$base/me"
curl -fsS "$base/openapi.json"
```

For messages, media, and exports, take the exact method, path, and JSON schema
from OpenAPI. Prefer structured JSON responses over rendered chat text.
