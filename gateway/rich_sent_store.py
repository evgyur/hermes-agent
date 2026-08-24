"""Local index of text we've sent via ``sendRichMessage`` (Bot API 10.1).

Telegram does NOT echo a rich message's content back in ``reply_to_message``
when a user replies to it (verified: ``.text``/``.caption`` empty,
``.api_kwargs`` None). So replies to the launchd briefings / any rich send
arrive with no quotable text and the agent is blind to what was referenced.

Fix: remember ``message_id -> text`` at send time, look it up by
``reply_to_id`` on inbound. This module is the single source of truth for that
index.

Dependency-free and crash-safe: records use an OS-locked read/merge/atomic
replace with file and directory fsync. Ordinary-chat callers may keep treating
the index as best-effort; Telegram Business callers use the boolean scoped
receipt and fail non-retryably when ownership cannot be committed.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Optional

_MAX_ENTRIES = 1000
_MAX_TEXT_CHARS = 2000
_PROCESS_LOCK = threading.RLock()


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


@contextmanager
def _exclusive_store_lock():
    """Serialize read/merge/replace across threads and gateway processes."""
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = f"{path}.lock"
    with _PROCESS_LOCK:
        with open(lock_path, "a+b") as lock_file:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_unlocked() -> dict:
    try:
        with open(_store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, AttributeError):
        return {}


def _write_unlocked(data: dict) -> None:
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if len(data) > _MAX_ENTRIES:
        for key, _ in sorted(
            data.items(), key=lambda item: item[1].get("ts", 0)
        )[: len(data) - _MAX_ENTRIES]:
            data.pop(key, None)
    fd, tmp = tempfile.mkstemp(
        prefix=f"{os.path.basename(path)}.tmp.{os.getpid()}.",
        dir=os.path.dirname(path),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        # Persist the directory entry on platforms that support directory
        # fsync.  Windows' atomic replace is already the durable primitive.
        if os.name != "nt":
            dir_fd = os.open(os.path.dirname(path), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _record_key(key: Optional[str], text: Optional[str]) -> bool:
    if key is None or not text:
        return False
    try:
        with _exclusive_store_lock():
            data = _load_unlocked()
            data[key] = {
                "t": text[:_MAX_TEXT_CHARS],
                "ts": int(time.time()),
            }
            _write_unlocked(data)
        return True
    except Exception:
        return False


def _lookup_key(key: Optional[str]) -> Optional[str]:
    if key is None:
        return None
    try:
        with _exclusive_store_lock():
            entry = _load_unlocked().get(key)
        if isinstance(entry, dict):
            return entry.get("t") or None
    except Exception:
        return None
    return None


def record(
    chat_id,
    message_id,
    text: Optional[str],
    business_connection_id=None,
    *,
    platform=None,
    transport_profile=None,
) -> bool:
    """Persist a legacy ordinary-chat entry and report commit success."""
    if not text or message_id is None or chat_id is None:
        return False
    # Deliberately legacy-only.  Business ownership receipts use the separate
    # exact-route API below and never dual-write an unscoped key.
    return _record_key(_key(chat_id, message_id, business_connection_id), text)


def record_scoped(
    *,
    platform,
    transport_profile,
    business_connection_id,
    chat_id,
    message_id,
    text: Optional[str],
) -> bool:
    """Durably record one exact transport route, without a legacy shadow."""
    return _record_key(
        _scoped_key(
            platform=platform,
            transport_profile=transport_profile,
            business_connection_id=business_connection_id,
            chat_id=chat_id,
            message_id=message_id,
        ),
        text,
    )


def lookup(chat_id, message_id, business_connection_id=None) -> Optional[str]:
    """Return stored text for ``(chat_id, message_id)`` or ``None``."""
    if message_id is None or chat_id is None:
        return None
    return _lookup_key(_key(chat_id, message_id, business_connection_id))


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
    return _lookup_key(key)
