# SuperGoal live restart proof and goal recovery pitfalls

Use this reference when a SuperGoal changes Hermes gateway restart recovery, `/goal` continuation recovery, or startup/drain behavior.

## Detached restart proof pattern

Never run `hermes gateway run --replace` from the live gateway turn. For live proof, create a detached script under `~/.hermes/scripts/` and schedule it with `systemd-run --on-active=<delay>` after the visible response is sent.

The script should prove:

- old gateway PID and state before restart;
- new gateway PID and active state after restart;
- touched Python files import/compile from the active checkout;
- synthetic safe active-goal classifier returns `auto_resume` through `GoalManager.next_continuation_prompt()` in the same session id;
- new gateway PID journal has no `Traceback`, `CRITICAL`, startup-recovery failure, or start failure lines.

## Journal scoping pitfall

A raw proof can fail if it greps the whole service journal since restart start time: the old PID may log expected shutdown noise or unrelated platform-send errors during SIGTERM. Scope health checks to the **new MainPID** after restart:

```bash
new_pid=$(systemctl show -p MainPID --value hermes-gateway.service)
journalctl -u hermes-gateway.service _PID="$new_pid" --since "@$start_epoch" --no-pager \
  | grep -Ei 'Traceback|CRITICAL|startup goal recovery.*failed|Failed to start'
```

If an old-PID traceback appears, triage it separately. Do not call live proof failed when:

- `old_pid != new_pid`;
- the service is active under the new PID;
- compile/import checks pass;
- synthetic recovery probe passes;
- new-PID journal is clean.

## Compression lineage split-brain pitfall

Active `/goal` state lives in `SessionDB.state_meta` under `goal:<session_id>`. If context compression creates a child session and copies a goal, the parent must be marked `cleared`. Otherwise startup recovery can see two active goals in one compression lineage.

Regression target:

```bash
python -m pytest tests/hermes_cli/test_goals.py::TestGoalManager::test_active_goal_migrates_to_compression_child -q -o 'addopts='
```

If aggregate tests reveal parent/child drift that a single isolated test misses, add a lazy-healing path: when loading a compression-ended parent, check direct compression children; if a child has the same active/paused/blocked goal, mark the parent `cleared`.

## Final audit reminder

Phase 5 can finish after detached proof and tests, but `SUPERGOAL_RUN_COMPLETE` belongs only after the protocol's final `AUDIT` state re-runs aggregated mandatory commands and prints `AUDIT_COMPLETE`.
