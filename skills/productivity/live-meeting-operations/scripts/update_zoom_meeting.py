#!/usr/bin/env python3
"""Update one Zoom meeting through protected credentials without exposing host secrets."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import re
from typing import Any
from urllib import parse

from create_meeting import MeetingCreateError, _http_json, _zoom_token


def _iso(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("start must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("start must include timezone offset")
    return value


def main() -> int:
    ap = argparse.ArgumentParser(description="Update a bounded Zoom meeting")
    ap.add_argument("--meeting-id", required=True)
    ap.add_argument("--title")
    ap.add_argument("--agenda")
    ap.add_argument("--start", type=_iso)
    ap.add_argument("--duration", type=int)
    ap.add_argument("--timezone")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not re.fullmatch(r"[0-9]{9,12}", args.meeting_id):
        raise SystemExit("meeting-id must contain 9-12 digits")
    if args.title is not None and (not args.title.strip() or len(args.title) > 200):
        raise SystemExit("title must contain 1-200 characters")
    if args.agenda is not None and len(args.agenda) > 2000:
        raise SystemExit("agenda exceeds 2000 characters")
    if args.duration is not None and not 1 <= args.duration <= 1440:
        raise SystemExit("duration must be 1-1440 minutes")
    if args.timezone is not None and not re.fullmatch(r"[A-Za-z_]+(?:/[A-Za-z0-9_+.-]+)+", args.timezone):
        raise SystemExit("timezone must be an IANA name")

    mapping = {
        "topic": args.title.strip() if args.title is not None else None,
        "agenda": args.agenda,
        "start_time": args.start,
        "duration": args.duration,
        "timezone": args.timezone,
    }
    payload: dict[str, Any] = {k: v for k, v in mapping.items() if v is not None}
    if not payload:
        raise SystemExit("at least one update field is required")
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "provider": "zoom", "meeting_id": args.meeting_id, "fields": sorted(payload)}, ensure_ascii=False))
        return 0

    token, _scopes = _zoom_token(_http_json)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    _http_json("PATCH", f"https://api.zoom.us/v2/meetings/{parse.quote(args.meeting_id)}", headers, payload)
    print(json.dumps({"ok": True, "provider": "zoom", "meeting_id": args.meeting_id, "updated": sorted(payload)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MeetingCreateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=__import__("sys").stderr)
        raise SystemExit(1)
