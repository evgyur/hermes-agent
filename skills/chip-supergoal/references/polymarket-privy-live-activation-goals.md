# Polymarket/Privy live activation goal correction

## Trigger

Use when Chip is planning a Polymarket/Privy SuperGoal and refers to previously listed “non-goals” such as live trading, wallet/deposit-wallet creation, signing, CLOB key derivation, pUSD approvals/wrap/unwrap/funding/withdrawal, order submit/cancel/claim/redeem, authenticated requests, geofence, or release/push.

## Lesson

Do not automatically convert “non-goals” into “forbidden/out-goals”. In this session Chip clarified: “I mean make them our goals” / “We need that”. The intended correction was to make the actual live money capabilities first-class implementation goals, while translating unsafe literal forms into controlled internal equivalents.

## Correct framing

- Live trading capability: goal, under policy and exact approval.
- Wallet/deposit-wallet creation/binding: goal, exact-approved and read back.
- Server-side signing and CLOB key lifecycle: goal, custody-controlled and redacted.
- pUSD/USDC approvals, wrap/unwrap, funding, withdrawal/offramp: goal, bounded/exact-approved/read back.
- Order submit/cancel and claim/redeem: goal, exact-approved/read back.
- Authenticated Polymarket/CLOB requests: goal through typed internal adapters.
- Secrets: goal is server-side overlay/KMS/Privy, never chat/log/file exposure.
- Geofence: goal is compliant official gate, not bypass.
- Release/push: goal is private/controlled release unless Chip separately approves public push.

## Pitfall

If the user/operator says “make those out goals too” in typo-prone chat context, inspect the surrounding text before acting. If the meaning is ambiguous, prefer a compact clarification or a reversible wording change. Do not rewrite the whole plan into safety-only forbidden criteria when the strategic intent is to build the missing live capability rail.

## User-facing recovery

If misread, acknowledge directly: “I read out as out-goals; you meant our goals.” Then rewrite `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`, protocol, and phase specs around live activation capability goals, preserving exact approval/readback and secret/geofence safety boundaries.
