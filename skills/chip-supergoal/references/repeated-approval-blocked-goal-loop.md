# Repeated approval-blocked `/goal` continuations

Session-derived pitfall from a Pear/Privy/Hyperliquid SuperGoal.

## Symptom

A SuperGoal phase reaches `BLOCKED_BY_APPROVAL`, but the visible `/goal` loop keeps posting the same approval card because the goal condition mentions `SUPERGOAL_RUN_COMPLETE` and the judge keeps returning CONTINUE.

This is not more phase work. It is a GoalManager/control-plane bug or stale goal state.

## Correct behavior

When a SuperGoal response/state clearly says `BLOCKED_BY_APPROVAL`:

- treat it as terminal blocked state for the `/goal` loop;
- do not repeat the same approval card on every continuation;
- do not burn the turn budget trying to reach `SUPERGOAL_RUN_COMPLETE`;
- mark the visible/compression-tip goal state as `blocked` or pause it with a blocker reason;
- keep the SuperGoal `.supergoal/STATE.md` at blocked/current phase until explicit approval or credentials arrive.

## Compression migration gotcha

Context compression can migrate `goal:<old_session_id>` into a new tip session. If you patch only the old row, the new tip may still be `active` and auto-continue.

Before claiming the loop is fixed:

1. Find the current visible/compression-tip session id.
2. Inspect `state_meta` for `goal:<tip_session_id>`.
3. If the SuperGoal is blocked, set the tip goal state to:
   - `status: blocked`
   - `last_verdict: done`
   - `last_reason: supergoal stopped with BLOCKED_BY_APPROVAL` or the concrete blocker
   - `paused_reason`: short human-readable blocker
4. Leave parent/compressed sessions cleared or migrated; do not let stale parent goals continue.

## User-facing response

If the user/operator asks “why did you stop?” or “what is needed?”:

- explain the concrete blocker once;
- if the blocker is approval, show the minimum exact approval phrase only if needed;
- if the blocker is credentials, say where to provision them and explicitly say not to paste secrets into Telegram;
- do not defend the protocol at length.

## Restart proof

If the fix changes Gateway/GoalManager code, schedule a detached gateway restart after the current response is delivered. Verify fresh PID and current goal tip state after restart before claiming the fix is live.
