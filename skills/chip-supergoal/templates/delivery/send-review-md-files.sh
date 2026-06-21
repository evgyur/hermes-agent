#!/usr/bin/env bash
# Template: send the required SuperGoal planning review pack as native files.
# Copy to .supergoal/scripts/send-review-md-files.sh and adapt TRANSPORT_SEND_FILE.
set -euo pipefail
ROOT="${SUPERGOAL_ROOT:-$(pwd)/.supergoal}"
OUT="$ROOT/out"
mkdir -p "$OUT"
TARGET="${SUPERGOAL_DELIVERY_TARGET:?set SUPERGOAL_DELIVERY_TARGET}"
FORCE="${SUPERGOAL_FORCE_RESEND:-0}"
FILES=("$ROOT/THINKING.md" "$ROOT/ROADMAP.md" "$ROOT/LAUNCH_GOAL.md")
for f in "${FILES[@]}"; do [[ -s "$f" ]] || { echo "missing review file: $f" >&2; exit 2; }; done
MANIFEST="$OUT/review-md-files-delivery-receipt.json"
HASHES=$(python3 - <<'PY' "${FILES[@]}"
import hashlib,json,sys,os
print(json.dumps({os.path.basename(p): hashlib.sha256(open(p,'rb').read()).hexdigest() for p in sys.argv[1:]}, sort_keys=True))
PY
)
if [[ -f "$MANIFEST" && "$FORCE" != "1" ]] && python3 - <<'PY' "$MANIFEST" "$TARGET" "$HASHES"
import json,sys
r=json.load(open(sys.argv[1]));
raise SystemExit(0 if r.get('ok') and r.get('sent') and r.get('target')==sys.argv[2] and r.get('hashes')==json.loads(sys.argv[3]) else 1)
PY
then
  echo "review files already sent for target+hash"
  exit 0
fi
# Replace this function in generated package with the actual platform sender.
TRANSPORT_SEND_FILE() { file="$1"; echo "SEND_FILE target=$TARGET file=$file"; }
MESSAGE_IDS=()
for f in "${FILES[@]}"; do
  msg_id="$(TRANSPORT_SEND_FILE "$f")"
  MESSAGE_IDS+=("$msg_id")
done
python3 - <<'PY' "$MANIFEST" "$TARGET" "$HASHES" "${MESSAGE_IDS[@]}"
import json,sys,datetime
path,target,hashes,*message_ids=sys.argv[1:]
json.dump({
  'ok': True,
  'sent': True,
  'kind': 'review-md-files',
  'target': target,
  'files': ['THINKING.md','ROADMAP.md','LAUNCH_GOAL.md'],
  'hashes': json.loads(hashes),
  'message_ids': message_ids,
  'sent_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
}, open(path,'w'), ensure_ascii=False, indent=2)
PY
