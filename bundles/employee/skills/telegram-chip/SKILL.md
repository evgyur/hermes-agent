---
name: telegram-chip
description: "Run: python3 ~/.hermes/skills/telegram-chip/scripts/probe_identity.py."
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
- After reading this file, proceed directly through terminal and the HTTP API.
  Read this file only once in a turn.

## Safety

- Never expose session material, credentials, phone numbers, or service env.
- Read exact chats/messages when identifiers are supplied.
- Writes require an unambiguous user-authorized target and must pass the
  runtime's own narrow allowlist. Never widen it to `*` or bypass a denial.
- Fetch every sent message back by its returned message ID before claiming
  success.

For an identity or health check, run exactly this one terminal command. The
bundled probe deliberately prints no phone number or other account metadata:

```bash
python3 ~/.hermes/skills/telegram-chip/scripts/probe_identity.py
```

After both `CHIPMANAGER_HEALTH_OK` and
`CHIPMANAGER_IDENTITY_OK username=chipmanager`, answer the user immediately;
do not run another terminal call.

Use the exact OpenAPI method and schema for all further operations.

For an authorized exact-target write plus mandatory readback, use the bundled
helper instead of constructing shell HTTP payloads by hand:

```bash
python3 ~/.hermes/skills/telegram-chip/scripts/send_and_read.py --chat-id TARGET --message TEXT
```
