#!/usr/bin/env python3
"""Create and verify Zoom or Google Meet rooms without exposing host secrets."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib import error, parse, request

JsonCall = Callable[[str, str, dict[str, str] | None, dict[str, Any] | None], dict[str, Any]]


class MeetingCreateError(RuntimeError):
    pass


@dataclass(frozen=True)
class MeetingSpec:
    title: str
    agenda: str
    start_time: str
    duration_minutes: int
    timezone_name: str
    attendees: tuple[str, ...] = ()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise MeetingCreateError(f"missing required credential: {name}")
    return value


def _http_json(method: str, url: str, headers: dict[str, str] | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise MeetingCreateError(f"provider HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise MeetingCreateError(f"provider request failed: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MeetingCreateError("provider returned non-JSON data") from exc
    if not isinstance(value, dict):
        raise MeetingCreateError("provider returned unexpected JSON shape")
    return value


def _zoom_token(call: JsonCall) -> tuple[str, set[str]]:
    account_id = _required_env("ZOOM_ACCOUNT_ID")
    client_id = _required_env("ZOOM_CLIENT_ID")
    client_secret = _required_env("ZOOM_CLIENT_SECRET")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    url = "https://zoom.us/oauth/token?" + parse.urlencode({"grant_type": "account_credentials", "account_id": account_id})
    data = call("POST", url, {"Authorization": f"Basic {basic}"}, None)
    token = str(data.get("access_token") or "")
    if not token:
        raise MeetingCreateError("Zoom OAuth response did not contain access_token")
    scopes = set(str(data.get("scope") or "").split())
    return token, scopes


def create_zoom(
    spec: MeetingSpec,
    call: JsonCall = _http_json,
    allow_receipt_verification: bool = False,
) -> dict[str, Any]:
    token, scopes = _zoom_token(call)
    accepted_read_scopes = {"meeting:read:meeting", "meeting:read:meeting:admin"}
    has_read_scope = bool(scopes.intersection(accepted_read_scopes))
    if not has_read_scope and not allow_receipt_verification:
        raise MeetingCreateError("Zoom app is missing meeting:read:meeting:admin; refusing to create an unverifiable room")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "topic": spec.title,
        "type": 2,
        "start_time": spec.start_time,
        "duration": spec.duration_minutes,
        "timezone": spec.timezone_name,
        "agenda": spec.agenda,
        "settings": {"join_before_host": False, "waiting_room": True, "mute_upon_entry": True, "auto_recording": "none"},
    }
    created = call("POST", "https://api.zoom.us/v2/users/me/meetings", headers, payload)
    meeting_id = str(created.get("id") or "")
    if not meeting_id:
        raise MeetingCreateError("Zoom create response did not contain meeting id")
    verified = (
        call("GET", f"https://api.zoom.us/v2/meetings/{parse.quote(meeting_id)}", headers, None)
        if has_read_scope
        else created
    )
    join_url = str(verified.get("join_url") or "")
    if not join_url.startswith("https://"):
        raise MeetingCreateError("Zoom receipt/readback did not contain a valid join_url")
    return {
        "provider": "zoom",
        "meeting_id": meeting_id,
        "join_url": join_url,
        "title": str(verified.get("topic") or spec.title),
        "start_time": str(verified.get("start_time") or spec.start_time),
        "verified": has_read_scope,
        "verification_level": "provider_readback" if has_read_scope else "create_receipt_requires_join_probe",
    }


def _google_access_token() -> str:
    direct = os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN", "").strip()
    if direct:
        return direct

    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    configured = os.getenv("GOOGLE_TOKEN_FILE", "").strip()
    meeting_token = home / "google_meeting_token.json"
    token_path = Path(configured) if configured else (meeting_token if meeting_token.is_file() else home / "google_token.json")
    saved: dict[str, Any] = {}
    if token_path.is_file():
        try:
            value = json.loads(token_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                saved = value
        except (OSError, json.JSONDecodeError) as exc:
            raise MeetingCreateError("Google token file is invalid") from exc

    client_id = str(saved.get("client_id") or os.getenv("GOOGLE_CALENDAR_CLIENT_ID", "")).strip()
    client_secret = str(saved.get("client_secret") or os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET", "")).strip()
    refresh_token = str(saved.get("refresh_token") or os.getenv("GOOGLE_CALENDAR_REFRESH_TOKEN", "")).strip()
    if not client_id:
        raise MeetingCreateError("missing required credential: GOOGLE_CALENDAR_CLIENT_ID")
    if not client_secret:
        raise MeetingCreateError("missing required credential: GOOGLE_CALENDAR_CLIENT_SECRET")
    if not refresh_token:
        raise MeetingCreateError("missing required credential: GOOGLE_CALENDAR_REFRESH_TOKEN")
    encoded = parse.urlencode({"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token, "grant_type": "refresh_token"}).encode()
    req = request.Request(str(saved.get("token_uri") or "https://oauth2.googleapis.com/token"), data=encoded, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read())
    except (error.HTTPError, error.URLError, json.JSONDecodeError) as exc:
        raise MeetingCreateError(f"Google OAuth refresh failed: {type(exc).__name__}") from exc
    token = str(data.get("access_token") or "")
    if not token:
        raise MeetingCreateError("Google OAuth response did not contain access_token")
    return token


def create_google_meet(spec: MeetingSpec, call: JsonCall = _http_json) -> dict[str, Any]:
    token = _google_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    start = datetime.fromisoformat(spec.start_time.replace("Z", "+00:00"))
    end = start + timedelta(minutes=spec.duration_minutes)
    request_id = f"hermes-{int(start.timestamp())}-{os.getpid()}"
    payload = {
        "summary": spec.title,
        "description": spec.agenda,
        "start": {"dateTime": start.isoformat(), "timeZone": spec.timezone_name},
        "end": {"dateTime": end.isoformat(), "timeZone": spec.timezone_name},
        "conferenceData": {"createRequest": {"requestId": request_id, "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
    }
    if spec.attendees:
        payload["attendees"] = [{"email": email} for email in spec.attendees]
    base = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    created = call("POST", base + "?conferenceDataVersion=1&sendUpdates=none", headers, payload)
    event_id = str(created.get("id") or "")
    if not event_id:
        raise MeetingCreateError("Google Calendar create response did not contain event id")
    verified = call("GET", f"{base}/{parse.quote(event_id)}?conferenceDataVersion=1", headers, None)
    organizer = str((verified.get("organizer") or {}).get("email") or "").strip().lower()
    expected_account = os.getenv("GOOGLE_MEETING_ACCOUNT", "").strip().lower()
    if not expected_account:
        home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
        account_file = home / "google_meeting_token.json"
        if account_file.is_file():
            try:
                expected_account = str(json.loads(account_file.read_text(encoding="utf-8")).get("account") or "").strip().lower()
            except (OSError, json.JSONDecodeError):
                expected_account = ""
    if expected_account and organizer != expected_account:
        call("DELETE", f"{base}/{parse.quote(event_id)}", headers, None)
        raise MeetingCreateError(
            f"Google event organizer mismatch: expected {expected_account}, got {organizer or 'unknown'}"
        )
    join_url = str(verified.get("hangoutLink") or "")
    if not join_url:
        for point in (verified.get("conferenceData") or {}).get("entryPoints", []):
            if point.get("entryPointType") == "video":
                join_url = str(point.get("uri") or "")
                break
    if not join_url.startswith("https://meet.google.com/"):
        raise MeetingCreateError("Google readback did not contain a valid Meet join URL")
    return {"provider": "google", "event_id": event_id, "join_url": join_url, "title": str(verified.get("summary") or spec.title), "start_time": str((verified.get("start") or {}).get("dateTime") or spec.start_time), "account": organizer, "verified": True}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("zoom", "google"), default="zoom")
    parser.add_argument("--title", required=True)
    parser.add_argument("--agenda", required=True)
    parser.add_argument("--start", help="ISO 8601; defaults to now + 2 minutes")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--timezone", default="Europe/Moscow")
    parser.add_argument("--attendee", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-receipt-verification", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.duration < 5 or args.duration > 1440:
        raise MeetingCreateError("duration must be between 5 and 1440 minutes")
    start = args.start or (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(timespec="seconds")
    spec = MeetingSpec(args.title.strip(), args.agenda.strip(), start, args.duration, args.timezone, tuple(args.attendee))
    if not spec.title or not spec.agenda:
        raise MeetingCreateError("title and agenda must not be blank")
    if args.dry_run:
        result = {"provider": args.provider, "start_time": spec.start_time, "verified": False, "dry_run": True}
    else:
        result = (
            create_zoom(spec, allow_receipt_verification=args.allow_receipt_verification)
            if args.provider == "zoom"
            else create_google_meet(spec)
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MeetingCreateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
