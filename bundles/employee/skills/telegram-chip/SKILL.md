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

Use this one-shot identity probe. It deliberately prints no phone number or
other account metadata:

```bash
python3 - <<'PY'
import json, os, urllib.request

base = os.environ.get("TELEGRAM_CHIP_BASE_URL", "http://127.0.0.1:18083").rstrip("/")
if base != "http://127.0.0.1:18083":
    raise SystemExit("REFUSED_UNEXPECTED_TELEGRAM_RUNTIME")

def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return json.load(response)

health = get("/health")
outer = get("/me")
identity = json.loads(outer["data"]) if isinstance(outer.get("data"), str) else outer["data"]
if health.get("status") != "ok" or not health.get("telegram_connected"):
    raise SystemExit("CHIPMANAGER_HEALTH_FAILED")
if identity.get("username") != "chipmanager":
    raise SystemExit("CHIPMANAGER_IDENTITY_FAILED")
print("CHIPMANAGER_HEALTH_OK")
print("CHIPMANAGER_IDENTITY_OK username=chipmanager")
PY
```

After both OK lines, answer the user immediately; do not run another terminal call.

Use the exact OpenAPI method and schema for all further operations.
