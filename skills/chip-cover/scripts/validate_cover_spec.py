#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path

REQUIRED = ["brand", "mode", "platform", "content", "constraints"]

def main():
    ap = argparse.ArgumentParser(description="Validate a minimal chip-cover JSON spec")
    ap.add_argument("spec")
    args = ap.parse_args()
    data = json.loads(Path(args.spec).read_text())
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        print(json.dumps({"ok": False, "missing": missing}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "brand": data.get("brand"), "mode": data.get("mode")}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
