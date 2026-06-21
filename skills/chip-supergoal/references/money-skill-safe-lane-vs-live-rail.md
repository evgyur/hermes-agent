# Money-skill SuperGoals: safe-lane completion vs live execution rail

Use this when a SuperGoal touches wallets, trading, payments, approvals, credentials, API keys, or irreversible money movement.

## Core lesson

A safe-lane skill/guardrail SuperGoal can be **complete** while the system is still **not live-ready to move money**.

Do not collapse these into one status.

- `safe-lane complete` means skill markdown, permission manifest, references, static probes, source-lock, and audit package are done.
- `live-ready` means the runtime/control-plane has wallet binding, typed action registry, geofence gate, approval binding, idempotency, audit ledger, execution adapter, readback, and negative probes.
- `live enabled` additionally requires exact current-chat approval, real wallet/secrets/policies, fresh geofence from the execution runtime, and verified post-action readback.

## Required response pattern

When the user/operator asks “is this 100% working / can we trade / can it move money?” after a safe-lane SuperGoal:

1. Verify `STATE.md`, final audit, and markers for the named goal.
2. Say clearly whether that named goal is complete.
3. Separately say whether live money execution is enabled.
4. If not live-ready, name the missing runtime rail pieces.
5. If the user/operator says “делай”, do **not** mutate the completed safe-lane goal. Create or continue a separate execution-rail goal targeting the runtime repo.

## Correct wording

```text
Safe-lane goal: COMPLETE.
Live trading: NOT ENABLED.
Reason: this delivered skill/guardrails/probes, not wallet-bound execution.
Next goal, if approved: implement fail-closed live execution rail with typed actions, approval/hash/idempotency, audit, and readback.
```

## Pitfalls

- Do not answer “yes, complete” in a way that implies real trading is enabled.
- Do not keep re-running a completed safe-lane goal when the user is asking for live readiness.
- Do not create an unrelated SuperGoal because a prior conversation mentioned a different bug.
- Do not treat generic “делай/continue” as approval for wallet creation, signing, funding, order submit/cancel, claim/redeem, or API-key derivation.
- Do not print or store secrets in the SuperGoal report.

## Evidence requirements for money/trading final audit

The final audit must include one of these labels:

- `safe-lane-complete-no-live-mutation`
- `guarded-live-rail-disabled`
- `blocked-handoff`
- `live-enabled-with-readback`

For any label except `live-enabled-with-readback`, state explicitly that money movement is not enabled.
