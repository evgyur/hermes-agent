# SuperGoal gateway recovery execution notes

Use this reference when a SuperGoal phase touches Hermes gateway restart recovery, `/goal` continuation, or missed-message drain recovery.

## Phase execution contract

- Execute only the current numbered phase from `.supergoal/STATE.md`; after `SUPERGOAL_PHASE_DONE`, update `STATE.md` to the next phase and stop with `SUPERGOAL_TURN_YIELD`.
- Do not chain phases in one assistant turn even if there is remaining context/tool budget.
- For each phase, print `SUPERGOAL_PHASE_START`, `SUPERGOAL_PHASE_VERIFY`, `RPD_PHASE_REVIEW` when required, `MEMORY_SAVED`, `SUPERGOAL_PHASE_DONE`, and `SUPERGOAL_TURN_YIELD`.
- Treat broad restart tests that expose existing scanner mismatches as evidence for the later recovery phase unless the current phase explicitly owns the scanner.

## Gateway recovery ledger pattern

For missed-message/drain recovery, prefer a profile-local SQLite ledger in `SessionDB`, not JSON side files. It should be additive and fail-open:

- table beside `messages`/`state_meta`, e.g. `gateway_message_ledger`;
- rows carry platform/chat/thread/message/user ids, `session_key`, `session_id`, lifecycle timestamps, status, origin type, reason/metadata, and a short snippet;
- statuses: `received`, `in_progress`, `completed`, `failed`, `drained`, `requeued`; later policy phases can add `alert_only`;
- origin types: `real_user`, `internal_goal`, `startup_recovery`.

Wire lifecycle at existing gateway boundaries:

1. `_handle_message()` entry → `received`.
2. startup-restore queue → `requeued`.
3. session key/slot claim → `in_progress` with `session_key`.
4. `SessionStore.get_or_create_session()` → attach `session_id`.
5. agent result → `completed` only when the same success predicate used for `resume_pending` clearing passes; `failed`/`drained` otherwise.
6. shutdown drain timeout → bulk mark active rows for still-running `session_key`s as `drained`.

## Privacy rails

- Do not duplicate full message bodies outside the normal transcript.
- Store only short redacted snippets; suppress internal `/goal` body text entirely, using constants like `[internal goal continuation]` and `[startup recovery continuation]`.
- Make every ledger write best-effort. A DB write failure must never block gateway dispatch or user delivery.

## Verification pattern

Focused tests should cover:

- direct schema/API transition `received -> in_progress -> completed`;
- real user dispatch through `_handle_message()` reaches completed;
- interrupted/partial result becomes `drained`, not `completed`;
- internal goal continuation differs from real user messages;
- bulk drain leaves already completed rows alone;
- broken DB helpers fail open and do not crash gateway helpers.

Run at minimum:

```bash
python -m py_compile gateway/run.py gateway/session.py hermes_state.py
python -m pytest tests/gateway/test_gateway_message_ledger.py -q -o 'addopts='
python -m pytest tests/gateway/test_telegram_group_gating.py -q -o 'addopts='
git diff --check -- gateway/run.py hermes_state.py tests/gateway/test_gateway_message_ledger.py
```
