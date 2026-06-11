# Gateway restart proof + bogus `/goal` cleanup

Use this after changing Hermes gateway/GoalManager code from inside a live Telegram gateway session.

## Problem pattern

A SuperGoal status/report can accidentally become a standing `/goal` when the gateway sees a continuation prompt like `Continuing toward your standing goal` and treats a quoted assistant report as the goal. The stored goal may start with prose such as:

```text
или `.supergoal/...` artifacts...
```

This is not a valid SuperGoal. Do not continue it. Clear it before doing any more GoalManager verification, otherwise Hermes will try to achieve its own report.

## Cleanup check

Probe the visible Telegram topic session, not a sibling forum/session key:

```python
import json, pathlib, sys
sys.path.insert(0, '<hermes-agent-install>')
from hermes_state import SessionDB
from hermes_cli.goals import GoalManager, load_goal

sessions = json.loads(pathlib.Path('/home/<user>/.hermes/sessions/sessions.json').read_text())
key = 'agent:main:telegram:group:<chat_id>:<thread_id>'
sid = sessions[key]['session_id']
tip = SessionDB().get_compression_tip(sid) or sid
state = load_goal(tip)
print(tip, None if not state else {'status': state.status, 'head': state.goal[:160]})

if state and state.status == 'active' and not state.goal.lstrip().startswith(('SUPERGOAL_GOAL_BODY:', 'Execute SuperGoal ')):
    GoalManager(session_id=tip).clear()
    print('cleared bogus report goal')
```

## Detached restart proof pattern

Never run `hermes gateway run --replace` from the live gateway turn. Use a detached systemd job after the current answer is delivered.

Preferred shape:

```bash
sudo systemd-run \
  --unit="hermes-gateway-restart-proof-$(date +%Y%m%d%H%M%S)" \
  --description="Delayed Hermes gateway restart proof" \
  --collect \
  /bin/bash -lc 'sleep 20; exec <hermes-agent-install>/venv/bin/python /home/<user>/.hermes/scripts/hermes_gateway_restart_probe.py'
```

The proof script should:

- set `HERMES_HOME=/home/<user>/.hermes` for subprocess probes;
- log under `~/.hermes/logs/`, not `/tmp`; root may fail to overwrite a user-owned `/tmp` log on systems with `fs.protected_regular`;
- capture old PID, restart `hermes-gateway`, wait for a fresh active PID;
- verify `migrate_goal_to_session` is importable from the active checkout;
- probe the visible topic GoalManager state and gateway health endpoint;
- send a compact Telegram proof back to the origin topic.

## What counts as success

Success is not “service exited with result success”. Success is:

```text
old pid != new pid
hermes-gateway ActiveState=active
ExecMainStartTimestamp after patch mtime
visible group:<chat>:<thread> session has expected GoalManager state
health endpoint ok
```

If a detached unit reports success but gateway PID did not change, treat it as failed verification and inspect the proof script/logs before claiming restart happened.
