# Telegram `.md` `/goal` launch hardening

## Trigger

Use this when SuperGoal launch is started from Telegram by replying bare `/goal` to an attached markdown file, especially `LAUNCH_GOAL.md`.

## Durable lesson

Do not treat markdown file hydration as normal command text. The user-visible command must remain exactly `/goal`; the document body is context and belongs in `event.reply_to_text` only.

Bad shape that caused a false launch:

```text
event.text = "/goal\n\n[Content of replied-to LAUNCH_GOAL.md]:\n...whole file..."
```

That makes `_handle_goal_command()` see non-empty args and store the whole file as the goal. The official reply-extraction path is bypassed.

Good shape:

```text
event.text = "/goal"
event.reply_to_text = "[Content of replied-to LAUNCH_GOAL.md]:\n...file..."
```

## User-facing contract

Send exactly three human-facing files by default:

1. `THINKING.md` — reasoning/review.
2. `ROADMAP.md` — execution map, review-only.
3. `LAUNCH_GOAL.md` — the only document the user should reply `/goal` to.

Keep `SUPERGOAL_GOAL_BODY:` out of `THINKING.md`, `ROADMAP.md`, `STATE.md`, `PROTOCOL.md`, phase specs, and reports. Those files are internal/review artifacts, not launch surfaces.

## Gateway extraction requirements

`LAUNCH_GOAL.md` may contain sections after the body:

```text
SUPERGOAL_GOAL_BODY:
<canonical body>

DONE_CONDITION:
...

OPERATOR_ACTION:
...

NOTES:
...
```

The stored GoalManager goal must contain only `<canonical body>`. It must not contain:

- `[Content of replied-to ...]`
- `DONE_CONDITION:`
- `OPERATOR_ACTION:`
- `NOTES:`
- file title/header text

Extractor must handle CRLF and no blank line before tail sections; use multiline stop patterns, not exact `\n\nTOKEN:` string search.

## Verification before telling the user to retry

Before saying “reply `/goal` now”, verify:

```text
gateway service active on fresh PID after patch
/health ok
visible Telegram topic GoalManager state is cleared/inactive before retry
_extract_supergoal_body(LAUNCH_GOAL.md) starts with Resume/Execute SuperGoal
extracted body has no wrapper/tails
focused tests for reply command + document hydration pass
```

After the user replies `/goal`, proof is the visible `group:<chat_id>:<thread_id>` GoalManager state with:

```text
status == active
goal starts with Resume/Execute SuperGoal
goal contains no [Content of replied-to], DONE_CONDITION, NOTES, OPERATOR_ACTION
```

Do not accept callback logs, Telegram send responses, or a stale sibling/forum session as proof.

## Regression tests to keep

- Bare `/goal` replied to markdown hydration keeps `event.text == "/goal"`.
- `/goal` args containing hydrated `SUPERGOAL_GOAL_BODY:` are canonicalized before `GoalManager.set()`.
- Extractor strips `DONE_CONDITION:`, `OPERATOR_ACTION:`, and `NOTES:` with CRLF and without blank lines.
- Real `LAUNCH_GOAL.md` fixture extracts a clean body.
