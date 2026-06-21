# Telegram review-file delivery gate for SuperGoal packages

When the user/operator asks for SuperGoal/ТЗ files, the planning-stage package must be delivered as exactly three native Telegram attachments before asking him to start or dispatch:

1. `THINKING.md`
2. `ROADMAP.md`
3. `LAUNCH_GOAL.md`

Do not rely on “I’ll send later”, archive-only delivery, or pasted text. If the delivery is missing, Chip will reasonably say “не вижу файлов”.

## Required implementation pattern

For generated SuperGoals, add a small scripted gate such as:

- `.supergoal/scripts/send-review-md-files.sh`
- `.supergoal/out/review-md-files-delivery-receipt.json`

The receipt must record:

```json
{
  "ok": true,
  "sent": true,
  "target": "telegram:...",
  "files": [
    {"path": ".../THINKING.md", "sha256": "..."},
    {"path": ".../ROADMAP.md", "sha256": "..."},
    {"path": ".../LAUNCH_GOAL.md", "sha256": "..."}
  ]
}
```

If the script fails, do not ask for start/dispatch. Print `SUPERGOAL_REVIEW_FILES_BLOCKED` with target and local paths.

## Final artifacts

For final SuperGoal completion, use a separate final artifact gate and marker:

- `.supergoal/scripts/package-final-artifacts.*`
- `.supergoal/scripts/send-final-artifacts.*`
- `.supergoal/out/final-artifacts-delivery-receipt.json`
- `SUPERGOAL_FILES_SENT` before `AUDIT_COMPLETE` / `SUPERGOAL_RUN_COMPLETE`.

The review-file gate and final-artifact gate are different moments. Review files go with the ТЗ; final artifacts go at completion.