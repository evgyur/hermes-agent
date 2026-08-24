"""Local index of text we've sent via ``sendRichMessage`` (Bot API 10.1).

Telegram does NOT echo a rich message's content back in ``reply_to_message``
when a user replies to it (verified: ``.text``/``.caption`` empty,
``.api_kwargs`` None). So replies to the launchd briefings / any rich send
arrive with no quotable text and the agent is blind to what was referenced.

Fix: remember ``message_id -> text`` at send time, look it up by
``reply_to_id`` on inbound. This module is the single source of truth for that
index.

Best-effort and dependency-free: every operation swallows errors and degrades
to a no-op / ``None`` so it can never break a send or an inbound message.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

_MAX_ENTRIES = 1000
_MAX_TEXT_CHARS = 2000


def _store_path() -> str:
    # Resolve via get_hermes_home() so the active profile override is honored.
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    return os.path.join(str(home), "state", "rich_sent_index.json")


def _key(chat_id, message_id, business_connection_id=None) -> str:
    if business_connection_id:
        return f"business:{business_connection_id}:{chat_id}:{message_id}"
    return f"{chat_id}:{message_id}"


def _scoped_key(
    *,
    platform,
    transport_profile,
    business_connection_id,
    chat_id,
    message_id,
) -> Optional[str]:
    parts = (
        platform,
        transport_profile,
        business_connection_id,
        chat_id,
        message_id,
    )
    normalized = tuple(str(value).strip() for value in parts)
    if not all(normalized):
        return None
    return "scope:v1:" + json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load() -> dict:
    try:
        with open(_store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, AttributeError):
        return {}


def _write(data: dict) -> None:
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if len(data) > _MAX_ENTRIES:
        for key, _ in sorted(
            data.items(), key=lambda item: item[1].get("ts", 0)
        )[: len(data) - _MAX_ENTRIES]:
            data.pop(key, None)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def record(
    chat_id,
    message_id,
    text: Optional[str],
    business_connection_id=None,
    *,
    platform=None,
    transport_profile=None,
) -> None:
    """Persist ``text`` for ``(chat_id, message_id)``. No-op on any failure."""
    if not text or message_id is None or chat_id is None:
        return
    try:
        data = _load()
        entry = {
            "t": text[:_MAX_TEXT_CHARS],
            "ts": int(time.time()),
        }
        data[_key(chat_id, message_id, business_connection_id)] = {
            **entry,
        }
        scoped_key = _scoped_key(
            platform=platform,
            transport_profile=transport_profile,
            business_connection_id=business_connection_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        if scoped_key is not None:
            data[scoped_key] = {**entry}
        _write(data)
    except Exception:
        return


def lookup(chat_id, message_id, business_connection_id=None) -> Optional[str]:
    """Return stored text for ``(chat_id, message_id)`` or ``None``."""
    if message_id is None or chat_id is None:
        return None
    try:
        data = _load()
        entry = data.get(_key(chat_id, message_id, business_connection_id))
        if isinstance(entry, dict):
            return entry.get("t") or None
    except Exception:
        return None
    return None


def lookup_scoped(
    *,
    platform,
    transport_profile,
    business_connection_id,
    chat_id,
    message_id,
) -> Optional[str]:
    """Return text only for one exact transport route; never widen scope."""
    key = _scoped_key(
        platform=platform,
        transport_profile=transport_profile,
        business_connection_id=business_connection_id,
        chat_id=chat_id,
        message_id=message_id,
    )
    if key is None:
        return None
    try:
        entry = _load().get(key)
        if isinstance(entry, dict):
            return entry.get("t") or None
    except Exception:
        return None
    return None
