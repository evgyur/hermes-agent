# Polymarket live-activation SuperGoal correction

Session lesson: when the user/operator says a list of former `Non-goals` should be “our goals”, do not reinterpret it as “out-goals” / forbidden-success criteria. He means the live capabilities are desired outcomes.

## Correct handling

Convert the plan from preview-only/readiness into a live-activation rail, while translating unsafe literal forms into controlled equivalents:

- `No default live trading` → goal: live trading capability under explicit policy, exact approval, and readback; not uncontrolled autopilot.
- `No wallet/deposit-wallet creation` → goal: exact-approved Privy wallet creation/binding and Polymarket deposit-wallet provisioning/binding.
- `No signing / CLOB key derivation` → goal: server-side/custody-controlled signing and CLOB key lifecycle; no raw payloads in chat/logs/files.
- `No pUSD approvals/wrap/unwrap/funding/withdrawal` → goal: bounded collateral lifecycle with spender/amount/expiry/readback.
- `No order submit/cancel/claim/redeem` → goal: typed live order lifecycle with idempotency, audit, and post-action readback.
- `No raw authenticated requests` → goal: typed internal authenticated adapters, not broad agent escape hatches.
- `No secrets in chat/logs/files` → goal: secret overlay/KMS/Privy with redacted presence evidence.
- `No geofence bypass` → goal: compliant official geofence gate; blocked/unknown fails closed.
- `No public push` → goal: private/controlled release unless separately exact-approved.

## Planning pitfall

Do not label these as `out-goals` unless Chip explicitly means forbidden outcomes. In this pattern, the roadmap should use sections like:

- `Our goals`
- `Capability goals`
- `Safety translation`
- `Hard boundaries`

The final readiness labels should distinguish implementation from actual live mutation, e.g. `live-activation-blocked`, `wallet-clob-collateral-provisioned`, `guarded-live-order-ready`, `live-microtrade-complete-with-readback`, `blocked-handoff`.

## File delivery pitfall

For SuperGoal/ТЗ packages, the three human review files must be native Telegram attachments at planning time: `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`. Send them before asking for start/dispatch and make delivery receipt-gated, not dependent on memory.