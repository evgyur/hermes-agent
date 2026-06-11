# Telegram `.md` launch pipeline hardening

Use this reference when building or repairing SuperGoal launch through Telegram files/buttons.

## Contract

- Human-facing launch pack defaults to exactly three native files: `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`.
- Only `LAUNCH_GOAL.md` may contain `SUPERGOAL_GOAL_BODY:`.
- `THINKING.md`, `ROADMAP.md`, `STATE.md`, `PROTOCOL.md`, `phases/*`, and reports are review/state artifacts, not launch targets.
- the user replies `/goal` only to `LAUNCH_GOAL.md` unless an explicit button launch path is implemented and verified.
- Posting a file or printing `SUPERGOAL_GOAL_BODY:` is not enough to start execution. Autodispatch requires an explicit internal sentinel such as `SUPERGOAL_AUTODISPATCH: true`.

## Gateway invariants

- Bare `/goal` reply must keep `event.text == "/goal"`.
- Replied document body must be stored in `event.reply_to_text`, not appended to `event.text`; otherwise `/goal <entire file>` bypasses reply extraction and stores the whole launch file.
- Goal extraction must return only the body after `SUPERGOAL_GOAL_BODY:` and before the earliest tail marker.
- Tail markers must handle CRLF and no-blank-line forms, not only `\n\nMARKER:`.
- Tail markers include at least: `DONE_CONDITION:`, `OPERATOR_ACTION:`, `NOTES:`, `Artifacts`, `Файлы`, `##`, and known report-tail phrases.
- If args already contain hydrated launch markdown, canonicalize them before `GoalManager.set()`.

## Verification before continuing a phase

Before claiming live goal-loop proof or continuing a phase:

1. Verify the gateway process has restarted after code changes and health is OK.
2. Inspect the visible topic/session `GoalManager` state, not just local artifacts.
3. Confirm stored goal body:
   - starts with the intended SuperGoal action
   - does not contain `[Content of replied-to ...]`
   - does not contain `DONE_CONDITION`, `OPERATOR_ACTION`, `NOTES`, or full-file wrappers
   - status is `active` when proving active launch; `cleared` is not success
4. If the stored goal is wrong, clear it immediately and do not continue phases manually.

## Regression tests to keep

- Bare `/goal` reply to `LAUNCH_GOAL.md` extracts only `SUPERGOAL_GOAL_BODY`.
- Hydrated args shape `/goal\n\n[Content of replied-to LAUNCH_GOAL.md]:...` also canonicalizes to body-only.
- CRLF launch file and no-blank-line `DONE_CONDITION:` are stripped.
- Telegram document hydration preserves bare `/goal` in `event.text` and moves document content to `event.reply_to_text`.
- Restart/proof scripts must not treat `bool(load_goal())` or `status=cleared` as valid active proof.

## User-facing discipline

If the user calls out instability in the launch rail, stop promising. Run Shaw-style review over the whole pipeline: Telegram hydration, extraction, GoalManager state, compression migration, restart proof, artifacts/skill docs, and tests. Fix guardrails first, then relaunch.
