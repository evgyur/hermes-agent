# SuperGoal review artifact delivery — explicit engineering chat correction

Use when preparing or correcting a `/chip-supergoal` review pack in Telegram.

## Lesson
Do not over-apply the post-production chat guard. The guard blocks SuperGoal artifacts only in the exact post-production Telegram chat reserved for `tg`/the delivery account previews. If the current chat is an engineering/SuperGoal working thread, operator DM, Dev topic, or the operator explicitly says “attach in this chat”, attach the artifacts there.

A false positive is worse than a cautious local-only handoff: the operator expects files in the visible thread.

## Required delivery shape
1. Send the three human-facing files as native attachments:
   - `THINKING.md`
   - `ROADMAP.md`
   - `LAUNCH_GOAL.md`
2. `LAUNCH_GOAL.md` is the only file the user/operator should reply `/goal` to.
3. If the user/operator asks for “all artifacts”, also send a zip bundle of `.supergoal/`, excluding secrets/caches/runtime junk.
4. Do not replace the three native files with only a zip unless the operator asked specifically for a single archive.
5. Keep `STATE.md`, `PROTOCOL.md`, `phases/`, reports, `context.md`, `repo-map.md`, and `tools.md` internal by default, but include them in the full bundle when the operator explicitly asks for all artifacts.

## Correction pattern
If you mistakenly withheld files:
- acknowledge the workflow miss in one line;
- attach the three files immediately;
- if relevant, attach the full zip bundle too;
- do not re-explain the whole plan.

## Exact pitfall
Bad:
> “This might be chiptg, keeping artifacts local.”

Good:
> “This is an engineering/SuperGoal thread; attaching the review pack here.”

Then include:
`MEDIA:/.../.supergoal/THINKING.md`
`MEDIA:/.../.supergoal/ROADMAP.md`
`MEDIA:/.../.supergoal/LAUNCH_GOAL.md`
