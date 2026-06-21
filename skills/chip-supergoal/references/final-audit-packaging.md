# Final audit packaging pattern

Use this when a SuperGoal final phase must attach a complete artifact bundle and also produce a final audit report.

## The self-referential bundle trap

If the final audit report includes the bundle path/hash/size, and the bundle includes the final audit report, the bundle hash becomes self-referential and unstable.

Safe pattern:

1. Run final validation first:
   - preflight command;
   - regression probes;
   - `skill_view` or equivalent readbacks;
   - changed-file secret scan;
   - state marker checks.
2. Write `final-audit.md` with all validation evidence.
3. Create the zip bundle **excluding**:
   - `final-audit.md`;
   - the bundle sidecar manifest itself.
4. Write a sidecar manifest, for example `.supergoal/evidence/phase-N/package-manifest.json`, containing:
   - bundle path;
   - sha256;
   - byte size;
   - entry count;
   - created timestamp;
   - explicit excludes.
5. Patch `final-audit.md` with the final bundle metadata.
6. Re-run a final consistency check:
   - `STATE.md` is complete;
   - audit report contains `AUDIT_COMPLETE` and `SUPERGOAL_RUN_COMPLETE`;
   - bundle exists;
   - actual sha256 matches the sidecar manifest.

## Reporting rule

Attach both:

- `final-audit.md` as its own file;
- the final zip bundle.

In the final user reply, state that the final audit is attached separately to avoid a self-referential bundle hash.

## Minimal consistency check

```python
from pathlib import Path
import hashlib, json
state = Path('.supergoal/STATE.md').read_text()
audit = Path('.supergoal/reports/final-audit.md').read_text()
manifest = json.loads(Path('.supergoal/evidence/phase-5/package-manifest.json').read_text())
zip_path = Path(manifest['bundle'])
sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
assert 'Status: COMPLETE' in state
assert 'AUDIT_COMPLETE' in audit
assert 'SUPERGOAL_RUN_COMPLETE' in audit
assert zip_path.exists()
assert sha == manifest['sha256']
```
