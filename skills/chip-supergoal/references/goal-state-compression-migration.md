# GoalManager state across context compression

Use this when a SuperGoal or `/goal` run appears to start, but later continuations say no goal is active, or when Telegram proof shows the visible topic session changed after context compression.

## Durable contract

`/goal` state is stored under `state_meta["goal:<session_id>"]`. Hermes context compression can rotate the active `session_id` while keeping the same visible Telegram topic/session binding. A live SuperGoal must therefore preserve GoalManager state from the compression parent to the current compression child.

Correct behavior:

- Resolve the current tip with `SessionDB.get_compression_tip(old_session_id)` before proving or resuming a goal.
- If an `active`, `paused`, or `blocked` goal exists only on a compression ancestor, migrate it to the tip.
- Mark the old ancestor goal `cleared` with a reason such as `migrated to <new_sid> after context compression`.
- Never migrate `done` or already-`cleared` goals.
- Never migrate across non-compression ancestry such as branch/resume/new-session relationships.
- Treat stale parent goals as dangerous: they can make the agent continue in the wrong session or falsely claim a visible `/goal` is inactive.

## Implementation shape

Preferred core API in `hermes_cli/goals.py`:

```python
_MIGRATABLE_GOAL_STATUSES = {"active", "paused", "blocked"}

def migrate_goal_to_session(old_session_id: str, new_session_id: str) -> bool:
    ...
```

`load_goal(session_id)` should first try exact lookup. If missing, it should walk the compression parent chain, find a migratable ancestor goal, copy it to the requested session, clear the ancestor, and return the migrated state. Keep the ancestry walk bounded, e.g. 32 levels, to avoid loops.

Gateway call-sites should also call `migrate_goal_to_session(old, new)` when:

- `agent_result["session_id"]` differs from the bound session entry.
- A session split is detected after an agent run and the agent exposes a new `session_id`.

Wrap gateway migration calls in `try/except` and log debug failures; the migration is a preservation aid and must not break normal chat handling.

## Verification checklist

1. Unit test: active parent goal + child with `end_reason="compression"` → child loads same goal, parent becomes `cleared`.
2. Unit test: parent/child with non-compression reason → child does not inherit, parent remains active.
3. Live proof: resolve the visible Telegram topic session id, call `get_compression_tip`, then verify `load_goal(tip).status in {"active", "paused", "blocked"}` as expected.
4. If resuming a SuperGoal, pause or clear any wrong-session sibling before continuing.

## Pitfall

Do not prove `/goal` with logs alone. A callback log, userbot send result, or synthetic `/goal` text is not enough. The proof is visible-session GoalManager state on `group:<chat_id>:<thread_id>` after compression lineage resolution.
