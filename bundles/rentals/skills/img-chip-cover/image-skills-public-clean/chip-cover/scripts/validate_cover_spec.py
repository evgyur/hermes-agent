#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
REQUIRED=['brand','mode','platform','content']
def main():
    ap=argparse.ArgumentParser(description='Validate a minimal cover JSON spec')
    ap.add_argument('spec'); args=ap.parse_args(); data=json.loads(Path(args.spec).read_text())
    missing=[k for k in REQUIRED if k not in data]
    checks=[]
    if 'overlay' in data and 'headline' in data['overlay'] and len(data['overlay']['headline'])>80:
        checks.append('headline may be too long for phone-first cover')
    ok=not missing
    print(json.dumps({'ok':ok,'missing':missing,'warnings':checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 2
if __name__=='__main__': raise SystemExit(main())
