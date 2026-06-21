# Telegram SuperGoal artifact delivery correction

Session lesson: Chip corrected a false-positive chat-boundary guard during a Pear SuperGoal planning run.

## Durable rule

Do not infer that a Telegram thread is the post-production Telegram preview chat just because the message is a reply in a group-style transcript or mentions Telegram. If the user/operator says this is the current SuperGoal/Dev/engineering chat, or explicitly says to attach artifacts here, treat that as the target clarification and deliver artifacts in the current chat.

## Correct delivery sequence

1. Generate and validate SuperGoal artifacts on disk.
2. In allowed engineering/SuperGoal chats, attach the three human-facing files natively:
   - `THINKING.md`
   - `ROADMAP.md`
   - `LAUNCH_GOAL.md`
3. If the user/operator asks for all artifacts, also attach a zip bundle containing the full `.supergoal/` tree, excluding secrets/cache/runtime junk.
4. Do not substitute a zip for the three human-facing files. The skill expects files-first review plus launch-card UX.
5. `LAUNCH_GOAL.md` remains the only document the user/operator should reply `/goal` to.

## Guardrail

The the post-production Telegram preview chat block is narrow: it applies only when the chat is actually the post-production Telegram preview surface. It must not be used as a generic excuse to withhold SuperGoal artifacts from the current engineering chat.

## Anti-pattern caught

Wrong:
- Generate artifacts locally.
- Refuse to attach them because of a guessed the post-production preview tool guard.
- Later send only a zip.

Right:
- Attach `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md` directly.
- Attach full zip additionally when requested.
- Acknowledge misclassification briefly and continue the SuperGoal flow.
