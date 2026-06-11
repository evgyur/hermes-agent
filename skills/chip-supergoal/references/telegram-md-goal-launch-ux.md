# Telegram `.md` SuperGoal launch UX

Use when creating or troubleshooting a SuperGoal launch in Telegram where the user reviews `.md` artifacts and starts execution with reply `/goal`.

## Human-facing artifact rule

Default to exactly three files:

1. `THINKING.md` — reasoning and trade-offs.
2. `ROADMAP.md` — phase plan and acceptance shape.
3. `LAUNCH_GOAL.md` — the only file the user replies to with `/goal`.

Do not dump `PROTOCOL.md`, `STATE.md`, individual `phase-N.md` files, reports, repo maps, or archives into chat unless the user asks. Those are internal/source-of-truth artifacts on disk, not the review UI.

## `LAUNCH_GOAL.md` shape

Keep the file short and explicit:

```md
# <Run name> — SuperGoal launch

SUPERGOAL_GOAL_BODY:
<raw GoalManager body only; one concise execution condition that points at .supergoal/... files>

DONE_CONDITION:
<optional human explanation>

OPERATOR_ACTION:
Reply to this file in Telegram with exactly: /goal

NOTES:
- This file is a launch artifact only.
- It does not autostart by being posted.
- GoalManager starts only from explicit reply /goal or the Start Goal button path.
```

## Critical extraction pitfall

When Telegram hydrates a replied document, `event.reply_to_text` may look like:

```text
[Content of replied-to LAUNCH_GOAL.md]:
# ...

SUPERGOAL_GOAL_BODY:
Execute SuperGoal ...

DONE_CONDITION:
...

OPERATOR_ACTION:
...

NOTES:
...
```

`/goal` must store only the raw body after `SUPERGOAL_GOAL_BODY:` and stop before these section tails:

- `DONE_CONDITION:`
- `OPERATOR_ACTION:`
- `NOTES:`

If the stored goal contains `[Content of replied-to ...]`, `DONE_CONDITION`, `OPERATOR_ACTION`, or `NOTES`, immediately clear it before it auto-continues. That is a bogus launch, not a valid SuperGoal run.

## Verification checklist

Before telling the user to reply `/goal` again:

- Gateway is restarted after the extraction patch.
- Visible Telegram `group:<chat_id>:<thread_id>` session has no active bogus goal.
- Focused test covers `LAUNCH_GOAL.md` with `SUPERGOAL_GOAL_BODY` followed by `DONE_CONDITION` / `OPERATOR_ACTION` / `NOTES`.
- Smoke extraction on the real launch file returns a body that starts with `Execute SuperGoal ...` and does not contain `DONE_CONDITION` or `NOTES`.

## Bad UX to avoid

- Sending a dozen `.md` files into Telegram.
- Making the user copy a long `/goal "..."` line.
- Treating the whole replied file as the goal.
- Saying the launch worked just because Telegram printed a GoalManager status notice; inspect the stored goal body in the visible group session.
