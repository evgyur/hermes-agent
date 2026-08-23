---
name: telegram-chip
description: Use the employee-scoped @chipmanager Telegram runtime directly on HEL1.
---

# Employee Telegram — chipmanager

This employee-only capability uses the separate `@chipmanager` Telegram
account. It must never access Chip's personal Telegram account or the personal
telegram-chip runtime.

## Exact runtime

- Base URL: `$TELEGRAM_CHIP_BASE_URL` when set, otherwise `http://127.0.0.1:18083`.
- `GET /me` must return username `chipmanager`. Stop if any other identity is
  returned.
- Never probe, connect to, or mention the personal runtime on port 8080.
- Load this skill once per turn, then proceed directly through terminal and the
  HTTP API; do not repeat `skill_view`.

## Safety

- Never expose session material, credentials, phone numbers, or service env.
- Read exact chats/messages when identifiers are supplied.
- Writes require an unambiguous user-authorized target and must pass the
  runtime's own narrow allowlist. Never widen it to `*` or bypass a denial.
- Fetch every sent message back by its returned message ID before claiming
  success.

```bash
base="${TELEGRAM_CHIP_BASE_URL:-http://127.0.0.1:18083}"
curl -fsS "$base/health"
curl -fsS "$base/me"
curl -fsS "$base/openapi.json"
```

Use the exact OpenAPI method and schema for all further operations.
