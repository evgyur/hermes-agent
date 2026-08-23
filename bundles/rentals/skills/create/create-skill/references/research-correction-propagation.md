# Research correction propagation

Use this when a user corrects a source assumption during or after a research-to-skill / SuperGoal planning session.

## Trigger

Apply when the user says a prior assumption was wrong, especially for jurisdiction, account scope, geography, compliance, current source version, live path, API version, or identity context.

Examples:
- “I am in Finland, not Russia.”
- “This is a subaccount, not the master wallet.”
- “That route is deprecated; use the new route.”
- “This skill is user-local, not the repo copy.”

## Required propagation

Do not only fix the final chat answer. Patch every durable artifact that future agents may load:

1. Operator synthesis / research summary.
2. New SuperGoal `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`, `RESEARCH.md`, and phase specs.
3. Any support reference created under the target class-level skill.
4. The governing skill root pointer if the correction changes routing or trigger behavior.
5. User memory only if the correction is stable and future sessions would otherwise repeat the mistake.

## Canonical wording pattern

Use explicit source-status language:

```text
User correction: <corrected fact>. Do not infer <wrong fact> from stale context. Phase 0 / source-lock must reverify <current source> before any mutation/action design.
```

For compliance/geofence tasks:
- record the corrected planning jurisdiction or location context;
- do not carry forward stale country-specific conclusions;
- do not convert the correction into permission to bypass restrictions;
- keep source-lock/current-IP/official-policy verification as a hard gate before mutation surfaces.

## Verification checklist

- [ ] Search durable artifacts for the stale assumption string.
- [ ] Patch all active artifacts or mark stale archival copies as not authoritative.
- [ ] Re-run lightweight plan validation if a SuperGoal was generated.
- [ ] Final response acknowledges the correction and names the patched artifacts.
