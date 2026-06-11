# SuperGoal Hermes update preservation

## Trigger

Use this when a SuperGoal `/goal` launch bug required patching Hermes gateway/GoalManager code and the user asks whether the fix will survive a Hermes update.

## Durable lesson

A live gateway patch is not durable until both layers are true:

1. The live Hermes checkout commits the code and pushes it to the user's private Hermes fork (`private/main`).
2. The server operations/update runbook documents the private rail so future upstream merges treat missing behavior as a private-patch regression, not harmless upstream drift.

Do not tell the user “it is safe across updates” if the fix exists only as dirty files in `<hermes-agent-install>`.

## Required preservation checklist

For SuperGoal launch-pipeline fixes, preserve all of these in the private fork and update workflow:

- Telegram document hydration keeps bare `event.text == "/goal"`; file content goes to `event.reply_to_text`.
- `_handle_goal_command()` canonicalizes args that still contain a hydrated `SUPERGOAL_GOAL_BODY:` before `GoalManager.set()`.
- `_extract_supergoal_body()` strips launch/report tails: `DONE_CONDITION:`, `OPERATOR_ACTION:`, `NOTES:`, `Artifacts:`, markdown headings, CRLF/no-blank-line boundaries.
- GoalManager migrates active/paused/blocked goals across context-compression parent→child session splits and clears stale parent state.
- Proof scripts distinguish a real active goal from a cleared/stale `load_goal()` object and inspect the stored visible-topic goal body.
- Telegram UX defaults to exactly three files: `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`; only `LAUNCH_GOAL.md` contains `SUPERGOAL_GOAL_BODY:`.

## Commands that proved the preservation pattern

```bash
cd <hermes-agent-install>
python -m pytest tests/gateway/test_goal_reply_command.py tests/gateway/test_telegram_documents.py tests/hermes_cli/test_goals.py tests/gateway/test_goal_status_notice.py tests/gateway/test_goal_max_turns_config.py tests/gateway/test_goal_verdict_send.py tests/gateway/test_telegram_clarify_buttons.py tests/gateway/test_telegram_group_gating.py -q -o 'addopts='
git add gateway/platforms/telegram.py gateway/run.py hermes_cli/goals.py tests/gateway/test_goal_reply_command.py tests/gateway/test_telegram_documents.py tests/gateway/test_telegram_group_gating.py tests/hermes_cli/test_goals.py
git commit -m "fix(gateway): stabilize supergoal launch pipeline"
git push private HEAD:main
```

Server-doctor/update-runbook side:

```bash
# update references/hermes-private-update-workflow.md with the SuperGoal rail checklist
# commit and push the docs repo/runbook
```

## Final proof before retrying `/goal`

After restart and before asking the user to retry, verify:

```text
gateway ActiveState=active
health ok
visible topic GoalManager status cleared/inactive before retry
LAUNCH_GOAL.md extracts clean Resume/Execute SuperGoal body
THINKING.md and ROADMAP.md contain no SUPERGOAL_GOAL_BODY
focused tests pass
```
