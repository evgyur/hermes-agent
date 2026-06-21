# Telegram launch and delivery

Use when the SuperGoal package or final artifacts must be sent through Telegram, or when launch happens by replying `/goal` to a Markdown file/message.

## Launch surfaces

Canonical order:

1. `LAUNCH_GOAL.md` native document with `SUPERGOAL_GOAL_BODY:`.
2. Clean Telegram launch card with button/reply affordance.
3. Plain-text `/goal "..."` fallback outside code fences if rich launch fails.

Do not use `ROADMAP.md`, `THINKING.md`, or `PROTOCOL.md` as hidden launch surfaces.

## Review delivery gate

When the user/operator asks for SuperGoal/ТЗ files, send exactly three native `.md` files by default:

- `THINKING.md`
- `ROADMAP.md`
- `LAUNCH_GOAL.md`

The planner must script delivery when available and write `.supergoal/out/review-md-files-delivery-receipt.json` with `ok=true` and `sent=true`. If sending fails, print `SUPERGOAL_REVIEW_FILES_BLOCKED` with the reason and do not pretend the files were delivered.

## Final artifact gate

If final files are requested, generated `/goal` must package and send final artifacts, verify receipt, then print `SUPERGOAL_FILES_SENT` before `SUPERGOAL_RUN_COMPLETE`.

## Idempotency

Use target + file/archive hash for idempotency. Do not resend automatically unless the user explicitly asks or `SUPERGOAL_FORCE_RESEND=1` is set for a deliberate retry.
