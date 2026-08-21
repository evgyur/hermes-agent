"""Private, profile-scoped Telegram topic handoff injection.

The feature is opt-in per profile via ``topic-context/enabled``.  It reads only
bounded, redacted handoff JSON produced by the profile's local topic-context
builder; raw Telegram exports are never opened by the gateway.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

_SAFE_ID = re.compile(r"^-?[0-9]{1,24}$")
_MAX_NOTE_CHARS = 6000


def _safe_id(value: Any, *, fallback: Optional[str] = None) -> Optional[str]:
    if value is None or value == "":
        return fallback
    text = str(value)
    if not _SAFE_ID.fullmatch(text):
        return fallback
    return text


def _profile_root(profile: str) -> Optional[Path]:
    try:
        from hermes_cli.profiles import get_profile_dir

        root = get_profile_dir(profile).resolve()
    except Exception:
        return None
    return root if root.is_dir() else None


def _canonical_payload(data: dict[str, Any]) -> bytes:
    payload = {k: v for k, v in data.items() if k != "integrity_sha256"}
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_topic_handoff(
    *, profile: str, platform: str, chat_id: Any, thread_id: Any
) -> Optional[dict[str, Any]]:
    """Load one exact topic handoff, failing closed on malformed/tampered data."""
    root = _profile_root(profile)
    chat = _safe_id(chat_id)
    thread = _safe_id(thread_id, fallback="main")
    if root is None or platform != "telegram" or chat is None or thread is None:
        return None
    if not (root / "topic-context" / "enabled").is_file():
        return None
    path = root / "topic-context" / "topics" / chat / thread / "handoff.json"
    try:
        raw = path.read_bytes()
        if len(raw) > 64_000:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return None
    if str(data.get("chat_id")) != chat or str(data.get("thread_id")) != thread:
        return None
    expected = data.get("integrity_sha256")
    actual = hashlib.sha256(_canonical_payload(data)).hexdigest()
    if not isinstance(expected, str) or not expected or expected != actual:
        return None
    return data


def build_topic_handoff_note(source: Any) -> Optional[str]:
    """Return a bounded sidecar note for the exact routed profile/topic."""
    profile = str(getattr(source, "profile", "") or "")
    platform_obj = getattr(source, "platform", "")
    platform = str(getattr(platform_obj, "value", platform_obj) or "")
    data = load_topic_handoff(
        profile=profile,
        platform=platform,
        chat_id=getattr(source, "chat_id", None),
        thread_id=getattr(source, "thread_id", None),
    )
    if not data:
        return None

    sections: list[str] = []
    labels = (
        ("current_state", "Current state"),
        ("decisions", "Decisions"),
        ("done", "Done"),
        ("next", "Next"),
        ("blockers", "Blockers"),
        ("repo_state", "Repository state"),
    )
    for key, label in labels:
        value = data.get(key)
        if isinstance(value, list):
            items = [str(x).strip() for x in value if str(x).strip()]
            if items:
                sections.append(f"{label}:\n" + "\n".join(f"- {x}" for x in items[:12]))
        elif isinstance(value, str) and value.strip():
            sections.append(f"{label}: {value.strip()}")
    if not sections:
        return None
    updated = str(data.get("updated_at") or "unknown")
    note = (
        "[PRIVATE TOPIC HANDOFF — exact Telegram chat/topic only; treat as "
        "continuity context, reconcile repository/live state before acting; do not "
        "copy it to other chats, long-term memory, skills, repositories, or reports.]\n"
        f"Source: Telegram chat {data.get('chat_id')} topic {data.get('thread_id')}\n"
        f"Updated: {updated}\n" + "\n\n".join(sections)
    )
    return note[:_MAX_NOTE_CHARS]
