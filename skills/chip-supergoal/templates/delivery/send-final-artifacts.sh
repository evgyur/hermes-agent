#!/usr/bin/env bash
# Template: send packaged final artifacts and write a blocking receipt.
set -euo pipefail
ROOT="${SUPERGOAL_ROOT:-$(pwd)/.supergoal}"
OUT="$ROOT/out"
TARGET="${SUPERGOAL_DELIVERY_TARGET:?set SUPERGOAL_DELIVERY_TARGET}"
ARCHIVE="${1:-$OUT/final-artifacts.zip}"
[[ -s "$ARCHIVE" ]] || { echo "missing archive: $ARCHIVE" >&2; exit 2; }
HASH="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
RECEIPT="$OUT/final-artifacts-delivery-receipt.json"
FORCE="${SUPERGOAL_FORCE_RESEND:-0}"
if [[ -f "$RECEIPT" && "$FORCE" != "1" ]] && python3 - <<'PY' "$RECEIPT" "$TARGET" "$HASH"
import json,sys
r=json.load(open(sys.argv[1])); raise SystemExit(0 if r.get('ok') and r.get('sent') and r.get('target')==sys.argv[2] and r.get('hash')==sys.argv[3] else 1)
PY
then
  echo "final artifacts already sent for target+hash"
  exit 0
fi
TRANSPORT_SEND_FILE() { file="$1"; echo "SEND_FILE target=$TARGET file=$file"; }
MESSAGE_ID="$(TRANSPORT_SEND_FILE "$ARCHIVE")"
python3 - <<'PY' "$RECEIPT" "$TARGET" "$ARCHIVE" "$HASH" "$MESSAGE_ID"
import json,sys,datetime
path,target,archive,hash_,message_id=sys.argv[1:]
json.dump({'ok':True,'sent':True,'kind':'final-artifacts','target':target,'archive':archive,'hash':hash_,'message_id':message_id,'sent_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}, open(path,'w'), ensure_ascii=False, indent=2)
PY
