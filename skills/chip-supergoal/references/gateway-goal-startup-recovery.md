# Gateway `/goal` startup recovery + the operator message reconciliation

Use when planning or executing a SuperGoal that fixes Hermes gateway restart/drain behavior, `/goal` continuation, or missed Telegram messages.

## Core lesson

Do not solve active `/goal` recovery as a standalone `gateway:startup` hook. Hooks are useful as watchdog/reporting layers, but `/goal` recovery is core gateway state-machine work.

Correct split:

- Core gateway/GoalManager recovery owns auto-resume, session identity, FIFO/startup restore, and safety policy.
- Gateway startup hook/checklist may scan/report, but should not bypass the official session/GoalManager path.
- `telegram-chip` is the privileged operator chat-history source; Bot API history limitations are not a blocker for operator-owned chats.

## Required design shape

1. Inspect existing recovery first:
   - `hermes_cli/goals.py` for `GoalManager` and `goal:<session_id>` state.
   - `gateway/run.py` for `_handle_goal_command`, `_post_turn_goal_continuation`, `_schedule_resume_pending_sessions`, startup restore, drain handling.
   - `gateway/session.py` / SessionStore for `resume_pending`, origin, session key, and suspension flags.
2. Add or verify a processing ledger before relying on chat history:
   - received / in_progress / completed / failed / drained / requeued / alert_only.
   - platform, chat_id, thread_id, message_id, session_key, session_id, timestamps.
   - short redacted snippets only; never persist full chat history just for recovery.
3. Reconcile operator messages via `telegram-chip` only as optional read-only truth:
   - recent operator messages with no ledger row => missed_by_gateway.
   - ledger `received` but no `in_progress` => safe requeue candidate.
   - ledger `in_progress`/`drained` with possible tool side effects => alert-only.
   - ledger `completed` => ignore.
4. Resume active goals only through official machinery:
   - same `session_key` and `session_id`.
   - `GoalManager.next_continuation_prompt()`.
   - adapter FIFO/startup restore queue.
   - no raw synthetic `/goal ...` command replay.
5. Fail closed on side effects:
   - safe/fresh the operator DM goals can auto-resume.
   - group/public/risky in-progress turns alert only.
   - paused/done/cleared/stale goals never auto-resume.

## Tests to demand

- Active fresh `/goal` interrupted by restart resumes in the same session.
- Paused/done/cleared/stale goals do not resume.
- Session already running is not resumed twice.
- Risky in-progress/tool-side-effect case produces alert-only, not replay.
- Stubbed `telegram-chip` reconciliation classifies missed/completed/alert-only messages without real credentials.
- Startup recovery failure does not crash gateway.

## Live proof bar

After changing live gateway/GoalManager code, use detached restart proof, not `hermes gateway run --replace` inside the live turn. Success requires old PID != new PID, service active, imports from `<hermes-agent-install>`, and a visible/safe recovery proof or explicit blocker.