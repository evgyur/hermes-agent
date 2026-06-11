# Telegram `/goal` live verification

Use this when the user says he did not see a Supergoal/`/goal` start in a Telegram topic, or when testing the `Start now` clarify-button pipeline.

## The failure pattern

A gateway log line like `Supergoal callback: started official /goal from button press` is not enough. In supergroup topics, the callback path and normal inbound-message path can build different session keys:

- visible topic session: `agent:main:telegram:group:<chat_id>:<thread_id>`
- invisible sibling session: `agent:main:telegram:forum:<chat_id>:<thread_id>`

If GoalManager state is written under the `forum` key, the user will not see the standing-goal continuation in the actual topic session even though the log says the callback started.

## Required proof before claiming goal started

1. Verify gateway is running the patched code and restarted after the change. If the live gateway process started before the code mtime, do **not** run a live Telegram E2E and pretend it used the patch.
2. Check the exact session mapping in `~/.hermes/sessions/sessions.json` for the target chat/thread.
3. Resolve the visible session id from `agent:main:telegram:group:<chat_id>:<thread_id>`.
4. Probe GoalManager with `hermes_cli.goals.load_goal(<visible_session_id>)`.
5. Also probe the sibling `forum` session if it exists; it should be empty or paused after migration.
6. Inspect `~/.hermes/state.db` `state_meta` keys `goal:<session_id>` if the helper output is ambiguous.
7. For the reply workflow, verify a bare `/goal` reply extracts only the `SUPERGOAL_GOAL_BODY:` payload even when the marker is inside a larger assistant report. The stored goal must exclude human handoff text like `Не стартовал за тебя` and the queued kickoff must not start with `/goal`.
8. Guard against accidental standing goals created from an assistant report. If the visible topic `GoalState.goal` starts with report prose like ``или `.supergoal/...` artifacts`` instead of an operator-authored objective or canonical `Execute SuperGoal ...`, clear it immediately before doing any restart/live-proof work. Do not let Hermes continue toward its own status report as a goal.
9. For Supergoal completion, terminal markers count only as standalone non-fenced lines. Mentions of `SUPERGOAL_RUN_COMPLETE` inside fallback `/goal` commands, code blocks, or explanatory prose must not satisfy the judge.
10. `FAILURE_HANDOFF` / `AUDIT_HANDOFF` should stop the loop as blocked/stopped, not as `✓ Goal achieved`.
11. Only claim success when the visible `group` session has `status: active` for the intended goal, or `status: blocked` after an intentional handoff test.

## Restarting the live gateway from inside gateway

If live code changed and the gateway must restart, do not run `hermes gateway run --replace` from the live turn. Prefer a detached systemd service that sleeps briefly, then restarts and sends proof back after the current reply is delivered.

A transient `systemd-run --on-active=... --collect ...` timer can disappear without useful user-level evidence on some hosts. Safer pattern: run a transient service whose command itself sleeps, then executes the restart/probe script. Verify the gateway actually got a fresh PID; if `MainPID` is unchanged, the proof did not happen.

Required proof after restart:
- old PID and new PID differ;
- `systemctl show hermes-gateway` says `ActiveState=active`;
- local health endpoint responds if configured;
- `load_goal(visible_session_or_tip)` still shows the intended goal state;
- the proof message is sent only after those checks pass.

## If goal is active in the wrong sibling session

Do not tell the user to press the button again or paste another giant `/goal` body. Repair the state:

1. Load the goal from the wrong `forum` session id.
2. Save the same `GoalState` under the visible `group` session id.
3. Mark the wrong `forum` goal as `paused` with a migration reason so two loops do not compete.
4. Clear stale `resume_pending` on the visible session mapping if a previous restart left it pending.
5. Re-probe both session ids and report the exact statuses.

## Telegram-chip caveat

`telegram-chip` is useful for verifying the userCR identity, reading exact messages, and sending explicit test messages, but a userbot-send response is not sufficient E2E proof that the Hermes gateway accepted and processed the message. After sending through `telegram-chip`, verify a matching inbound gateway log and/or GoalManager state in the visible session. If no inbound log appears, report that honestly and repair/verify through the gateway state instead of pretending E2E passed.

## Minimal verification snippet

```python
import json, pathlib, sys
sys.path.insert(0, '<hermes-agent-install>')
from hermes_cli.goals import load_goal

sessions = json.loads(pathlib.Path('/home/<user>/.hermes/sessions/sessions.json').read_text())
visible_key = 'agent:main:telegram:group:<chat_id>:<thread_id>'
forum_key = 'agent:main:telegram:forum:<chat_id>:<thread_id>'
for key in [visible_key, forum_key]:
    rec = sessions.get(key)
    sid = rec and rec.get('session_id')
    state = load_goal(sid) if sid else None
    print(key, sid, None if state is None else {'status': state.status, 'turns_used': state.turns_used})
```
