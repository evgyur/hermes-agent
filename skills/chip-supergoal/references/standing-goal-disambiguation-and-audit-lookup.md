# Standing-goal disambiguation and audit lookup

Use this reference when Chip replies to a `/goal`/SuperGoal handoff, asks whether an older goal is complete, or multiple similarly named SuperGoals exist.

## Problem pattern

Chip may have several related goal roots in flight, for example:

- a safe-lane skill/guardrail SuperGoal (`polymarket-privy-supergoal`);
- a later live/runtime execution rail SuperGoal (`polymarket-privy-live-rail-supergoal`);
- an accidentally created wrong SuperGoal in quarantine.

The user’s reply target and attached `LAUNCH_GOAL.md` are the source of truth. Do not continue the most recent or most exciting goal if the user is asking about a different replied goal.

## Required workflow

1. Parse the exact root path from the replied `LAUNCH_GOAL.md`.
2. Read that root’s `.supergoal/STATE.md` before acting.
3. If the user asks “is this done?” or “where is the audit?”, look for:
   - `Status: COMPLETE` in `.supergoal/STATE.md`;
   - `.supergoal/reports/final-audit.md`;
   - `AUDIT_COMPLETE`;
   - `SUPERGOAL_RUN_COMPLETE`.
4. Report the exact audit path, package path/hash if present, and honest scope.
5. If `STATE.md` is complete and audit markers exist, do not rerun phases or create another goal.
6. If the goal is incomplete, continue the next numbered phase from its own `STATE.md`, not from chat memory.
7. If a different related goal exists, name the distinction explicitly.

## Scope wording pitfall

Never collapse these statuses:

- `safe-lane skill/guardrail complete` means markdown/reference/probe/audit work is done; it does **not** mean live trading works.
- `runtime rail in progress` means code is being built; it does **not** mean money movement is enabled.
- `guarded-live-rail-disabled` means the execution path exists but is intentionally blocked until exact approval, wallet/deposit-wallet binding, secrets, geofence, audit, and readback pass.
- `live-enabled-with-readback` is the only status that can be described as live trading enabled.

## Minimal answer shape

```text
да/нет.

➊ status
┈ STATE: ...
┈ audit marker: ...

➋ audit
┈ path: ...
┈ package/hash: ...

➌ boundary
┈ what is complete
┈ what is not live-ready
```

## Anti-patterns

- Starting a new SuperGoal when the user asked about the old one.
- Treating a repeated standing-goal wrapper as a new human request after `SUPERGOAL_RUN_COMPLETE`.
- Saying a skill scaffold is “100% ready to trade.”
- Ignoring a replied document path and using the latest workspace path instead.
