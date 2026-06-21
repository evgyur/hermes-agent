# Live activation / continuation hardening

Use this reference when executing SuperGoal work that can be interrupted by gateway restarts, stale wrappers, or live-mutation boundaries.

## Lessons captured

- A completed SuperGoal root must not be resumed just because a stale wrapper arrives. Inspect `.supergoal/STATE.md` and terminal markers first. If `Status: COMPLETE` or both `AUDIT_COMPLETE` + `SUPERGOAL_RUN_COMPLETE` are present, treat the wrapper as stale unless the user explicitly names a fresh root.
- If a successor root exists and is `READY`/`IN_PROGRESS`, do not answer `COMPLETE — stop` repeatedly for the old root. Emit `STALE_GOAL_WRAPPER`, name old + new roots, and continue only from the canonical active root.
- Gateway startup recovery with uncheckpointed tool tails is not a reason to abandon an active goal. Inspect persisted state/artifacts, then continue from `.supergoal/STATE.md`. Redo only idempotent or verified-safe steps.
- `SUPERGOAL_TURN_YIELD` is not permission to stop. It means persist state and immediately advance unless blocked by missing user input or a live-mutation approval boundary.
- For live activation goals, distinguish live capability implementation from live mutation. Generic `continue`, `go`, phase completion, or stale approval never authorizes wallet/signing/collateral/order/claim/redeem mutation.
- Final completion requires real evidence and delivery receipts before terminal markers: focused tests, full tests, negative probes, secret/raw surface checks, `validate-supergoal.sh`, review-file receipt, final artifact receipt, then `SUPERGOAL_FILES_SENT`, `AUDIT_COMPLETE`, `SUPERGOAL_RUN_COMPLETE`.

## Minimal recovery checklist

1. Read the active root from the prompt, not from stale wrapper history.
2. Read `.supergoal/STATE.md` and phase report inventory.
3. If prior turn had uncheckpointed tool calls, inspect output/evidence files before rerunning commands.
4. Continue at `Current phase`.
5. For any send script, enforce idempotency by target + file/archive sha256 + receipt.
6. Never perform live mutation without exact current-chat approval packet.
7. If live prerequisites are missing, write `AUDIT_HANDOFF` for that live path and keep completing non-live SuperGoal phases.
