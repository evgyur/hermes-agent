# Telegram SuperGoal launch-card UX

## Trigger

Use this when planning, implementing, or verifying SuperGoal dispatch UX in Telegram, especially when the user wants a smoother flow than copying a long `/goal` line.

## Preferred user-facing shape

Do not make the long plan text the primary action surface. Split review artifacts from launch controls:

1. Send native review artifacts first, exactly three by default:
   - `THINKING.md`
   - `ROADMAP.md`
   - `LAUNCH_GOAL.md`
   Internal artifacts (`STATE.md`, `PROTOCOL.md`, `phases/`, reports, `context.md`, `repo-map.md`, `tools.md`) stay on disk unless the user explicitly asks for debug internals.
2. Send one short launch-card message after the files:
   - says the files above are the source of truth
   - contains a visible `SUPERGOAL_GOAL_BODY:` section with the canonical goal body
   - is not fenced as code
3. Attach up to four action buttons:
   - `▶ Start Goal`
   - `✎ Assumption`
   - `⚙ Phase tweak`
   - `↔ Restructure`

## Dispatch hierarchy

Primary:
- `▶ Start Goal` button starts official `GoalManager` directly. It must not enqueue a synthetic `/goal ...` slash-command text because post-run safety can discard queued slash commands.

Fallback:
- the user replies to the launch-card with bare `/goal`; gateway extracts only `SUPERGOAL_GOAL_BODY:` and starts the visible session goal.

Bonus fallback:
- the user replies `/goal` to `LAUNCH_GOAL.md` only. Gateway downloads/reads the replied document, extracts only `SUPERGOAL_GOAL_BODY:` and strips `DONE_CONDITION:`, `OPERATOR_ACTION:`, `NOTES:` and wrapper text, then starts GoalManager. Replying to `ROADMAP.md`, `THINKING.md`, `STATE.md`, `PROTOCOL.md`, or phase files is not the normal launch path.

## Why files are review artifacts, not execution truth

Telegram files are excellent for review, but the executing source of truth must remain the project-local `.supergoal/` directory. Relying on Telegram file contents as the main execution source makes dispatch fragile: file cache, captions, truncation, missing project root, and ambiguity over which file is authoritative.

## Implementation checklist

### `gateway/platforms/telegram.py`

- When incoming text/command message is a reply to a document, hydrate the event before command handling/batching.
- For replied `.md/.txt/.html` or other text documents, download the replied document up to a safe text limit and populate `event.reply_to_text`.
- Preserve existing replied-media behavior: cache the document, set `event.media_urls` / `event.media_types`, and update `event.message_type` for document/video/audio/image where appropriate. Do not fix `/goal` by breaking `/summ` reply-to-document.
- Keep command text intact. Example: `/goal` should remain `event.text == "/goal"`; the document body belongs in `event.reply_to_text`.
- Use size guards twice: Telegram-declared `file_size` and actual downloaded byte length. A useful split is platform max for caching and ~100 KB for text injection.

### `gateway/run.py`

- Reuse existing reply extraction: prefer `SUPERGOAL_GOAL_BODY:`, else synthesize from `.supergoal/...` artifact paths.
- Marker extraction must work even when `SUPERGOAL_GOAL_BODY:` appears inside a larger report/document, not only at byte 0.
- Strip launch/report tails after the canonical body: examples include “Не стартовал…”, “Artifacts:”, “Файлы:”, “Кнопки”, and markdown section headers.
- Keep `xhigh` reasoning for SuperGoal dispatch.
- Printing `SUPERGOAL_GOAL_BODY:` alone must not autostart GoalManager. Autodispatch requires an explicit internal sentinel such as `SUPERGOAL_AUTODISPATCH: true`.

### Goal state/session proof

- Preserve visible topic session key shape: `agent:main:telegram:group:<chat_id>:<thread_id>`.
- Verify success by reading visible GoalManager state (`status == active`), not by trusting callback logs, userbot-send responses, or synthetic `/goal` text.

## Regression tests to add/run

Minimum tests when touching this path:

- Bare `/goal` reply to launch-card strips `SUPERGOAL_GOAL_BODY:` and does not include report tails.
- Bare `/goal` reply extracts marker from the middle of a larger report.
- Bare `/goal` reply to hydrated markdown document starts from `reply_to_text`.
- Telegram replied document hydration keeps command text unchanged while populating `reply_to_text`.
- Existing reply-to-document media flows still cache files and preserve `/summ` behavior.
- Targeted suites:
  - `tests/gateway/test_goal_reply_command.py`
  - `tests/gateway/test_telegram_documents.py`
  - `tests/gateway/test_telegram_clarify_buttons.py`
  - `tests/gateway/test_telegram_group_gating.py`
  - goal status/verdict/max-turns tests as needed

## User-facing rule

The UX should be obvious: files are for reading, launch-card is for starting, button is the shortest path, `/goal` reply is the durable fallback.
