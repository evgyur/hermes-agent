# Telegram `/goal` kickoff prompt invariant

Use this when fixing or reviewing SuperGoal launch flows in Hermes Gateway: reply `/goal` to `LAUNCH_GOAL.md`, launch-card button start, or `SUPERGOAL_AUTODISPATCH`.

## Bug class

GoalManager can be set correctly while the first queued agent turn is wrong.

Failure shape:

1. User replies `/goal` to `LAUNCH_GOAL.md` or clicks `▶ Start Goal`.
2. Gateway extracts/stores the clean goal body in GoalManager.
3. Gateway queues the raw `state.goal` as the immediate kickoff message.
4. The agent sees a naked body like `From repo root ... execute the SuperGoal ...`, not the official `[Continuing toward your standing goal]` wrapper.
5. SuperGoal discipline then rejects the kickoff as "body without command" or tells the operator it did not start, even though GoalManager state exists.

## Durable invariant

After any official start path, the queued first turn must be the canonical GoalManager continuation prompt:

```python
kickoff_text = mgr.next_continuation_prompt() or state.goal
# or for a freshly-created manager in autodispatch:
kickoff_text = goal_mgr.next_continuation_prompt() or state.goal
```

Then queue `MessageEvent(text=kickoff_text, ...)`, not `MessageEvent(text=state.goal, ...)`.

This makes the first turn indistinguishable from a normal `/goal` continuation and gives the agent the active-goal context immediately.

## Tests to require

Regression tests should prove all of these:

- `GoalManager.state.goal` is the clean extracted body only.
- Tail sections in `LAUNCH_GOAL.md` such as `DONE_CONDITION:`, `OPERATOR_ACTION:`, and `NOTES:` are stripped.
- The queued kickoff starts with `[Continuing toward your standing goal]\nGoal: `.
- The queued kickoff contains the clean goal body but does **not** start with the raw body.
- SuperGoal starts set `xhigh` reasoning override where that rail exists.
- The same kickoff invariant covers reply `/goal`, launch-card button start, and `SUPERGOAL_AUTODISPATCH`.

Useful focused test set from the Hermes gateway repo:

```bash
python -m pytest \
  tests/gateway/test_goal_reply_command.py \
  tests/gateway/test_telegram_documents.py \
  tests/gateway/test_goal_status_notice.py \
  tests/gateway/test_goal_verdict_send.py \
  tests/gateway/test_telegram_clarify_buttons.py \
  -q -o 'addopts='
```

Also run `py_compile` on touched gateway/tests files.

## Live proof

A green unit test is not enough for operator-facing Telegram fixes. Before claiming live repair:

- identify active gateway service/PID/checkouts;
- restart safely after the user-visible response, not inline from the same gateway turn;
- verify PID changed and service is active;
- verify the patched kickoff invariant is present in the running checkout;
- verify visible-session GoalManager state if a live goal is involved.

Use `references/gateway-restart-live-proof.md` for the delayed restart pattern.

## User-facing behavior

If the user/operator says he replied `/goal` to `LAUNCH_GOAL.md`, believe the flow and debug the gateway. Do not tell him he sent only the body unless logs prove there was no reply command. Reply-based launch is a first-class rail, not a fallback to blame.
