---
name: telegram-chip
description: Use isolated chipmanager Telegram tools for employee tasks.
---

# Employee Telegram — chipmanager

Use the `chipmanager_telegram` tool for the separate `@chipmanager` account.
Never use a personal Telegram account or endpoint.

## Workflow

1. Call `chipmanager_telegram` with `action=health` once.
2. Continue only when the returned username is exactly `chipmanager`.
3. For an explicit user-authorized write, pass the exact `chat_id`, exact
   `message`, and `authority=explicit-user-request`.
4. Treat success as proven only when the tool returns a message ID and exact
   readback.

The host runtime owns the narrow allowlist and may deny any target. Never widen
that allowlist, bypass a denial, print credentials, or change the fixed
loopback endpoint `http://127.0.0.1:18083`.
