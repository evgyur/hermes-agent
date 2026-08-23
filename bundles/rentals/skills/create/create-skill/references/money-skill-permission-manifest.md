# Money-skill permission manifest checklist

Use this when creating, refactoring, or reviewing Hermes skills that touch trading, wallets, exchanges, payments, credentials, approvals, or agentic execution.

## When required

A money-skill permission manifest is mandatory when a skill can mention or route toward:
- wallets, custody, private keys, signatures, EIP-712, Privy, exchange accounts;
- API keys, JWTs, auth sessions, refresh tokens;
- orders, cancels, closes, leverage, margin, funding, withdrawals, transfers;
- builder approvals, agent/API-wallet approvals, revocations, or fee caps;
- autonomous/bounded trading agents;
- payment rails or irreversible money movement.

## Required sections

Every manifest should include:

1. Skill identity
   - skill name
   - live `skill_dir`
   - default mode
   - owner/operator boundary

2. Allowed tools and files
   - safe read/search/probe tools
   - allowed local helper scripts
   - allowed skill files/references
   - artifact/evidence paths

3. Allowed network/API surface
   - exact domains and endpoints
   - request types that are read-only
   - authenticated readbacks, if any
   - docs/source lookups

4. Forbidden network/API surface
   - signing/login with a real wallet
   - order/trade/cancel/close/leverage/margin/fund/withdraw/transfer
   - approval/revocation/cap changes
   - API-key/session creation, rotation, or revocation
   - broad raw request/action wrappers

5. Approval semantics
   - exact current-chat approval only
   - no blanket “go/continue/done” semantics
   - dynamic IDs accepted only after authenticated readback and manifest match
   - demotion/fail-closed behavior

6. Secrets and credentials
   - required secrets, if any
   - secrets never to request/print/store
   - redaction and rotation behavior on leak

7. Output rules
   - mode label: read-only/prep/execution/bounded autopilot
   - sources/readbacks used
   - statement of side effects not performed

8. Regression probes
   - deterministic scanner command
   - negative patterns: terminology, raw surfaces, exchange-as-readonly, blanket approvals, secret-shaped tokens

## Default stance

- Read-only by default.
- Prep can draft manifests, risk envelopes, and approval cards.
- Execution requires exact approval and post-action readback.
- Bounded autopilot requires risk envelope, idempotency, audit ledger, kill switch, and exact approval.
- The skill itself should never create a hidden raw API/action surface.

## Minimal manifest skeleton

```md
# <Skill> permission manifest

## Skill identity
- Skill:
- Live path:
- Mode:
- Default posture:

## Allowed tools and files
- ...

## Allowed network/API surface
- ...

## Forbidden network/API surface
- ...

## Required gates
- ...

## Secrets and credentials
- ...

## Output rules
- ...
```

## Review checklist

- [ ] Manifest is linked from root `SKILL.md`.
- [ ] Live `skill_dir` is correct.
- [ ] Allowed endpoints are exact, not broad “API access”.
- [ ] Forbidden side effects are explicit.
- [ ] Approval semantics reject blanket approvals.
- [ ] Secret rules forbid raw keys/JWTs/signed payloads.
- [ ] Regression probe covers at least five negative patterns.
- [ ] Final answer reports created/validated/loaded, not deployed/executed.
