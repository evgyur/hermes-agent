#!/usr/bin/env python3
"""Retrieve Zoom cloud artifacts through Server-to-Server OAuth.

Safe output: never prints OAuth tokens, secrets, passcodes, or download URLs.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ZOOM_API = "https://api.zoom.us/v2"
TARGET_TYPES = {
    "transcript": {"audio_transcript"},
    "summary": {"summary"},
    "next_steps": {"summary_next_steps"},
    "audio": {"audio_only"},
    "video": {"shared_screen_with_speaker_view", "shared_screen_with_gallery_view", "active_speaker"},
}
EXTENSIONS = {
    "audio_transcript": ".vtt",
    "summary": ".json",
    "summary_next_steps": ".json",
    "audio_only": ".m4a",
    "shared_screen_with_speaker_view": ".mp4",
    "shared_screen_with_gallery_view": ".mp4",
    "active_speaker": ".mp4",
}
SAFE_EXT = {
    "TRANSCRIPT": ".vtt",
    "SUMMARY": ".json",
    "TIMELINE": ".json",
    "CHAT": ".txt",
    "M4A": ".m4a",
    "MP4": ".mp4",
}


def load_env(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"env file not found: {path}")
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.startswith("ZOOM_"):
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def request_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, data: bytes | None = None) -> dict[str, Any]:
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=45) as response:
        body = response.read()
    return json.loads(body.decode("utf-8")) if body else {}


def oauth_token() -> tuple[str, list[str]]:
    account = os.environ.get("ZOOM_ACCOUNT_ID")
    client = os.environ.get("ZOOM_CLIENT_ID")
    secret = os.environ.get("ZOOM_CLIENT_SECRET")
    if not all((account, client, secret)):
        raise SystemExit("ZOOM_ACCOUNT_ID/ZOOM_CLIENT_ID/ZOOM_CLIENT_SECRET missing")
    basic = base64.b64encode(f"{client}:{secret}".encode()).decode()
    url = "https://zoom.us/oauth/token?" + urllib.parse.urlencode(
        {"grant_type": "account_credentials", "account_id": account}
    )
    payload = request_json(url, method="POST", headers={"Authorization": f"Basic {basic}"})
    token = payload.get("access_token")
    if not token:
        raise SystemExit("Zoom OAuth token unavailable")
    return token, payload.get("scope", "").split()


def api_get(token: str, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    url = ZOOM_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return request_json(url, headers={"Authorization": f"Bearer {token}"})


def list_users(token: str) -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = []
    next_token = ""
    while True:
        params = {"status": "active", "page_size": "300"}
        if next_token:
            params["next_page_token"] = next_token
        payload = api_get(token, "/users", params)
        users.extend(payload.get("users") or [])
        next_token = payload.get("next_page_token") or ""
        if not next_token:
            return users


def list_recordings(token: str, start: str, end: str) -> list[dict[str, Any]]:
    meetings: list[dict[str, Any]] = []
    for user in list_users(token):
        user_id = user.get("id")
        if not user_id:
            continue
        next_token = ""
        while True:
            params = {"from": start, "to": end, "page_size": "300"}
            if next_token:
                params["next_page_token"] = next_token
            payload = api_get(token, f"/users/{urllib.parse.quote(str(user_id), safe='')}/recordings", params)
            meetings.extend(payload.get("meetings") or [])
            next_token = payload.get("next_page_token") or ""
            if not next_token:
                break
    meetings.sort(key=lambda item: item.get("start_time") or "", reverse=True)
    return meetings


def canonical_share(url: str) -> str:
    return url.split("?", 1)[0].rstrip("/")


def select_meeting(
    meetings: list[dict[str, Any]],
    share_url: str | None = None,
    topic: str | None = None,
    *,
    latest: bool | None = None,
) -> dict[str, Any]:
    """Select a recording without silently substituting another meeting.

    `latest is None` is the canonical share-URL/topic API. Supplying `latest`
    activates the legacy exact-meeting-id contract retained for older bots.
    """
    if latest is not None:
        meeting_id = re.sub(r"\D+", "", share_url or "")
        candidates = meetings
        if meeting_id:
            candidates = [
                item
                for item in meetings
                if re.sub(r"\D+", "", str(item.get("id") or "")) == meeting_id
            ]
        if not candidates:
            raise RuntimeError("no matching finalized Zoom cloud recording found")
        candidates = sorted(candidates, key=lambda item: item.get("start_time") or "", reverse=True)
        if not latest and not meeting_id and len(candidates) > 1:
            raise RuntimeError("multiple recordings found; pass an exact meeting id or latest=True")
        return candidates[0]

    selected = meetings
    if share_url:
        wanted = canonical_share(share_url)
        selected = [item for item in meetings if canonical_share(item.get("share_url") or "") == wanted]
        if not selected:
            raise SystemExit("NO_MATCH: supplied share URL is not in the connected Zoom account")
    if topic:
        needle = topic.casefold()
        selected = [item for item in selected if needle in (item.get("topic") or "").casefold()]
        if not selected:
            raise SystemExit("NO_MATCH: topic not found in the bounded recording window")
    if not selected:
        raise SystemExit("NO_MATCH: no completed cloud recordings in the bounded window")
    return selected[0]


def safe_filename(index: int, item: dict[str, Any]) -> str:
    """Build a deterministic artifact filename without provider URLs or secrets."""
    file_type = str(item.get("file_type") or "FILE").upper()
    recording_type = re.sub(
        r"[^a-z0-9]+", "-", str(item.get("recording_type") or "artifact").lower()
    ).strip("-")
    return f"{index:02d}-{recording_type}-{file_type.lower()}{SAFE_EXT.get(file_type, '.bin')}"


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(stat.S_IRWXU)


def download(token: str, url: str, target: Path) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=180) as response:
        data = response.read()
    target.write_bytes(data)
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return {"path": str(target), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def vtt_to_txt(
    source: Path,
    target: Path,
    meeting: dict[str, Any] | None = None,
) -> dict[str, Any] | int:
    text = source.read_text(errors="replace")
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    cues: list[tuple[str, str]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines or lines[0] == "WEBVTT":
            continue
        timing = next((line for line in lines if "-->" in line), None)
        if not timing:
            continue
        idx = lines.index(timing)
        body = " ".join(lines[idx + 1 :]).strip()
        if not body:
            continue
        start = timing.split("-->", 1)[0].strip().split(".", 1)[0]
        cues.append((start, body))

    if meeting is None:
        # Compatibility ABI used by already-shipped Human20Bot tests/callers.
        target.write_text(
            "\n".join(f"[{start}] {body}" for start, body in cues) + "\n",
            encoding="utf-8",
        )
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return len(cues)

    formatted: list[str] = []
    for start, body in cues:
        match = re.match(r"([^:]{1,80}):\s*(.*)", body)
        if match:
            formatted.append(f"[{start}] {match.group(1).strip()}:\n{match.group(2).strip()}")
        else:
            formatted.append(f"[{start}]\n{body}")
    header = (
        "Автоматическая транскрипция Zoom\n"
        f"Тема: {meeting.get('topic') or ''}\n"
        f"Начало UTC: {meeting.get('start_time') or ''}\n"
        f"Длительность: {meeting.get('duration') or ''} минут\n"
        "Источник: Zoom audio_transcript; распознавание может содержать ошибки.\n\n"
    )
    target.write_text(header + "\n\n".join(formatted) + "\n", encoding="utf-8")
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    data = target.read_bytes()
    return {"path": str(target), "cues": len(formatted), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def main() -> None:
    today = date.today()
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=Path.home() / ".hermes" / ".env")
    parser.add_argument("--from", dest="start", default=(today - timedelta(days=30)).isoformat())
    parser.add_argument("--to", dest="end", default=today.isoformat())
    parser.add_argument("--share-url")
    parser.add_argument("--topic")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--download", default="transcript,summary,next_steps", help="comma list: transcript,summary,next_steps,audio,video,none")
    args = parser.parse_args()
    load_env(args.env)
    token, scopes = oauth_token()
    meetings = list_recordings(token, args.start, args.end)
    meeting = select_meeting(meetings, args.share_url, args.topic)
    private_dir(args.out)
    requested = {part.strip() for part in args.download.split(",") if part.strip() and part.strip() != "none"}
    wanted_types = set().union(*(TARGET_TYPES.get(key, set()) for key in requested))
    files = []
    seen_video = False
    for item in meeting.get("recording_files") or []:
        recording_type = item.get("recording_type") or ""
        if recording_type not in wanted_types or item.get("status") != "completed":
            continue
        if recording_type in TARGET_TYPES["video"]:
            if seen_video:
                continue
            seen_video = True
        stem = {"audio_transcript": "transcript", "summary": "zoom-summary", "summary_next_steps": "zoom-next-steps", "audio_only": "audio"}.get(recording_type, "video")
        target = args.out / f"{stem}{EXTENSIONS.get(recording_type, '.bin')}"
        receipt = download(token, item["download_url"], target)
        receipt["recording_type"] = recording_type
        files.append(receipt)
        if recording_type == "audio_transcript":
            files.append(vtt_to_txt(target, args.out / "transcript.txt", meeting))
    result = {
        "status": "ok",
        "meeting": {
            "id": meeting.get("id"),
            "topic": meeting.get("topic"),
            "start_time": meeting.get("start_time"),
            "duration_minutes": meeting.get("duration"),
            "recording_file_types": sorted({item.get("recording_type") for item in meeting.get("recording_files") or [] if item.get("status") == "completed"}),
        },
        "required_scopes": {
            "list_user_recordings": "cloud_recording:read:list_user_recordings:admin" in scopes,
            "list_recording_files": "cloud_recording:read:list_recording_files:admin" in scopes,
            "meeting_transcript": "cloud_recording:read:meeting_transcript:admin" in scopes,
        },
        "files": files,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
            message = payload.get("message") or str(error)
            code = payload.get("code")
        except json.JSONDecodeError:
            message, code = str(error), None
        raise SystemExit(json.dumps({"status": "error", "http_status": error.code, "code": code, "message": message}, ensure_ascii=False))
