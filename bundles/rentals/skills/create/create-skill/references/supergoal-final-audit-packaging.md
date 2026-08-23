# SuperGoal final audit packaging

Use this when a skill-library or SuperGoal run needs to publish a final zip bundle plus a final audit report.

## Avoid self-referential bundle hashes

If `final-audit.md` contains the bundle hash and the zip contains `final-audit.md`, the hash cannot be stable. The same applies to a package manifest that records the hash of the zip containing itself.

Safe packaging sequence:

1. Run all final validations first.
2. Write `final-audit.md` with validation evidence and completion markers.
3. Create the zip bundle excluding:
   - `final-audit.md`;
   - package sidecar manifest.
4. Write `package-manifest.json` outside the zip with path, sha256, bytes, entry count, and excludes.
5. Patch `final-audit.md` with the bundle metadata.
6. Re-check:
   - `STATE.md` complete marker;
   - `AUDIT_COMPLETE` marker;
   - `SUPERGOAL_RUN_COMPLETE` marker;
   - bundle exists;
   - actual sha256 matches manifest.

## Reporting rule

Attach `final-audit.md` separately from the bundle and say explicitly that it is separate to avoid self-referential hash drift.

## Related live skill note

The `chip-supergoal` root skill may be near/over the skill-management size limit. If its root cannot be patched, store the detailed runbook under `chip-supergoal/references/final-audit-packaging.md` and carry this governing pointer here until that root is slimmed.
