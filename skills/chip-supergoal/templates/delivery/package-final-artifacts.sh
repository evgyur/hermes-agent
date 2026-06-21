#!/usr/bin/env bash
# Template: package final SuperGoal artifacts without including receipt sidecars as source artifacts.
set -euo pipefail
ROOT="${SUPERGOAL_ROOT:-$(pwd)/.supergoal}"
OUT="$ROOT/out"
mkdir -p "$OUT"
ARCHIVE="$OUT/final-artifacts.zip"
MANIFEST="$OUT/final-artifacts-manifest.json"
python3 - <<'PY' "$ROOT" "$ARCHIVE" "$MANIFEST"
from pathlib import Path
import hashlib,json,sys,zipfile,datetime
root=Path(sys.argv[1]); archive=Path(sys.argv[2]); manifest=Path(sys.argv[3])
exclude={archive.name, manifest.name, 'final-artifacts-delivery-receipt.json', 'review-md-files-delivery-receipt.json'}
files=[]
for p in sorted(root.rglob('*')):
    if not p.is_file(): continue
    rel=p.relative_to(root).as_posix()
    if rel.startswith('out/') and Path(rel).name in exclude: continue
    files.append((p,rel))
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for p,rel in files: z.write(p,rel)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
json.dump({'ok':True,'archive':str(archive),'hash':digest,'files':[rel for _,rel in files],'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}, open(manifest,'w'), ensure_ascii=False, indent=2)
print(str(archive)); print(digest)
PY
