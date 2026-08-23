# Staff Review Rubric

Use this to grade work produced with `chip-webb`.

## Levels

| Level | Behavior |
| --- | --- |
| Junior | Builds visible pages; relies on happy-path manual checks; misses source-of-truth and rollback. |
| Middle | Wires features and basic tests; understands env and deploy commands but may accept narrow evidence. |
| Senior | Identifies live paths, auth/data/media risks, deploy provenance, and scoped verification. |
| Staff | Defines contracts, decision records, failure modes, rollback, handoff, and risk-based evidence gates. |
| Architect+ | Adds formal state machines, permission matrix, threat model, negative fixtures, rollout policy, and independent review gates. |

## Staff-Level Checklist

- [ ] Decision record explains why the chosen architecture is appropriate.
- [ ] Source-of-truth artifact identifies live app, deploy path, env source, and release fingerprint.
- [ ] Auth/account state machine covers guest, unlinked, inactive, active, stale, and already-authenticated states.
- [ ] Entitlements are scoped and server-enforced.
- [ ] Supabase data authority distinguishes live, local, legacy, backup, and migration states.
- [ ] Protected media is private-by-default with token, path, cache, and error contracts.
- [ ] Deploy uses exact commit, clean tree, release metadata, and smoke evidence.
- [ ] Rollback has a data-safety gate.
- [ ] Failure modes are classified and P0/P1 risks are verified or explicitly accepted.
- [ ] Handoff includes checked and unchecked paths.
- [ ] Public artifacts contain no secrets or project-specific private residue.

## Architect+ Escalation

Escalate from staff to Architect+ when:

- payments, regulated data, minors, or compliance are in scope;
- multiple services or databases can write the same state;
- rollback can lose or corrupt user data;
- admin/operator actions can cause financial/access impact;
- media leakage has material business impact;
- multiple teams/operators will own the system.

Architect+ adds:

- formal state machines with transitions;
- permission matrix for human, service, admin, and agent roles;
- interface contracts with payloads, errors, idempotency, and rollback seams;
- data classification and retention matrix;
- threat model;
- dependency failure matrix;
- negative fixture catalog;
- rollout and promotion policy;
- independent/adversarial review.

## Review Output Template

```text
level:
why:
must_fix_before_production:
should_fix:
evidence:
unverified_risks:
recommended_next_slice:
```
