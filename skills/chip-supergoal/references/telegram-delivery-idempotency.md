# Telegram delivery idempotency for Chip SuperGoals

## Trigger

Use when a SuperGoal/ТЗ package sends review/final artifacts to Telegram, especially after a timeout, manual fallback, retry, or the user/operator asks “why did you send duplicates?”.

## Lesson

Chip wants mandatory file delivery, but not duplicate delivery. A blocking delivery gate must be **idempotent**: retries should prove prior delivery instead of resending the same files.

## Required pattern

1. Send exactly the intended artifact set:
   - review pack: `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`;
   - final pack: archive or explicitly requested final artifacts.
2. Write a receipt after successful send with:
   - `ok=true`, `sent=true`;
   - target/chat/thread;
   - absolute paths;
   - byte sizes;
   - SHA-256 for every file or archive;
   - timestamp;
   - `manual_fallback=true` if a direct tool/send fallback was used.
3. Before retrying, compare current artifact paths + sizes + SHA-256 + target against the receipt.
4. If they match, exit cleanly with `skipped_duplicate_send=true`; do **not** resend.
5. Resend only when Chip explicitly asks, using a force flag such as `SUPERGOAL_FORCE_RESEND=1`.

## Pitfall

Do not combine manual `send_message` delivery with a later script retry. If the script times out but manual fallback succeeds, immediately write the same success receipt and stop. Otherwise the retry will send a second copy of each file.

## Minimal receipt shape

```json
{
  "ok": true,
  "sent": true,
  "target": "telegram:...",
  "files": [
    {"path": "/abs/THINKING.md", "bytes": 1234, "sha256": "..."}
  ],
  "manual_fallback": false,
  "timestamp": "2026-06-19T01:12:12+03:00"
}
```

## Verification

Run the delivery script twice against unchanged files. The second run must return success with `skipped_duplicate_send=true` and must not create new Telegram messages.