"""Durable spool for inbound events deferred before agent dispatch.

Only events whose gateway ledger proves that dispatch never started are replayed.
The private spool closes the restart gap between an in-memory busy queue and
startup ledger reconciliation without replaying ambiguous tool outcomes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_RETENTION_SECONDS = 7 * 24 * 60 * 60
SWEEP_INTERVAL_SECONDS = 60 * 60
_last_sweep_monotonic = 0.0


@dataclass(frozen=True)
class DeferredSpoolEntry:
    path: Path
    event: MessageEvent
    session_key: str
    ledger_id: Optional[int] = None


def _spool_dir() -> Path:
    from hermes_constants import get_hermes_home

    path = get_hermes_home() / "deferred_events"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(path, 0o700)
    return path


def _identity(event: MessageEvent) -> str:
    existing = getattr(event, "_hermes_deferred_spool_id", None)
    if existing:
        return str(existing)
    primary_ledger_id = getattr(event, "_hermes_gateway_ledger_id", None)
    if primary_ledger_id is not None:
        digest = f"ledger-{int(primary_ledger_id)}"
        setattr(event, "_hermes_deferred_spool_id", digest)
        return digest
    source = getattr(event, "source", None)
    platform = getattr(getattr(source, "platform", None), "value", "")
    parts = (
        platform,
        str(getattr(source, "chat_id", "") or ""),
        str(getattr(source, "thread_id", "") or ""),
        str(getattr(event, "message_id", "") or ""),
    )
    raw = "\x1f".join(parts)
    if not parts[-1]:
        raw += f"\x1f{uuid.uuid4().hex}"
    value = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    setattr(event, "_hermes_deferred_spool_id", value)
    return value


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return None


def _serialize_event(event: MessageEvent) -> dict[str, Any]:
    source = getattr(event, "source", None)
    if source is None:
        raise ValueError("deferred event has no source")
    timestamp = getattr(event, "timestamp", None)
    return {
        "text": getattr(event, "text", "") or "",
        "message_type": getattr(getattr(event, "message_type", None), "value", "text"),
        "user_id": getattr(event, "user_id", None),
        "user_name": getattr(event, "user_name", None),
        "source": source.to_dict(),
        "message_id": getattr(event, "message_id", None),
        "platform_update_id": getattr(event, "platform_update_id", None),
        "media_urls": list(getattr(event, "media_urls", None) or []),
        "media_types": list(getattr(event, "media_types", None) or []),
        "reply_to_message_id": getattr(event, "reply_to_message_id", None),
        "reply_to_text": getattr(event, "reply_to_text", None),
        "reply_to_author_id": getattr(event, "reply_to_author_id", None),
        "reply_to_author_name": getattr(event, "reply_to_author_name", None),
        "reply_to_is_own_message": bool(getattr(event, "reply_to_is_own_message", False)),
        "prompt_response": _json_safe(getattr(event, "prompt_response", None)),
        "auto_skill": _json_safe(getattr(event, "auto_skill", None)),
        "channel_prompt": getattr(event, "channel_prompt", None),
        "channel_context": getattr(event, "channel_context", None),
        "internal": bool(getattr(event, "internal", False)),
        "metadata": _json_safe(getattr(event, "metadata", None)) or {},
        "startup_resume": bool(getattr(event, "startup_resume", False)),
        "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else None,
    }


def _deserialize_event(data: dict[str, Any]) -> MessageEvent:
    source_data = data.get("source")
    if not isinstance(source_data, dict):
        raise ValueError("deferred event source is missing")
    timestamp = data.get("timestamp")
    parsed_timestamp = datetime.fromisoformat(timestamp) if timestamp else datetime.now()
    message_type_raw = str(data.get("message_type") or "text")
    try:
        message_type = MessageType(message_type_raw)
    except ValueError:
        message_type = MessageType.TEXT
    return MessageEvent(
        text=str(data.get("text") or ""),
        message_type=message_type,
        user_id=data.get("user_id"),
        user_name=data.get("user_name"),
        source=SessionSource.from_dict(source_data),
        message_id=data.get("message_id"),
        platform_update_id=data.get("platform_update_id"),
        media_urls=list(data.get("media_urls") or []),
        media_types=list(data.get("media_types") or []),
        reply_to_message_id=data.get("reply_to_message_id"),
        reply_to_text=data.get("reply_to_text"),
        reply_to_author_id=data.get("reply_to_author_id"),
        reply_to_author_name=data.get("reply_to_author_name"),
        reply_to_is_own_message=bool(data.get("reply_to_is_own_message", False)),
        prompt_response=data.get("prompt_response"),
        auto_skill=data.get("auto_skill"),
        channel_prompt=data.get("channel_prompt"),
        channel_context=data.get("channel_context"),
        internal=bool(data.get("internal", False)),
        metadata=dict(data.get("metadata") or {}),
        startup_resume=bool(data.get("startup_resume", False)),
        timestamp=parsed_timestamp,
    )


def persist_deferred_event(event: MessageEvent, *, session_key: str) -> Optional[Path]:
    """Atomically persist one not-yet-dispatched event with private permissions."""
    from utils import atomic_json_write

    _maybe_sweep_expired()
    ledger_ids = list(
        dict.fromkeys(
            int(value)
            for value in (
                list(getattr(event, "_hermes_gateway_ledger_ids", None) or [])
                + [getattr(event, "_hermes_gateway_ledger_id", None)]
            )
            if value is not None
        )
    )
    source = getattr(event, "source", None)
    if (
        bool(getattr(event, "internal", False))
        or bool(getattr(source, "role_authorized", False))
        or bool(getattr(source, "delivered_via_upstream_relay", False))
        or bool(getattr(source, "is_bot", False))
        or not ledger_ids
    ):
        return None
    spool_id = _identity(event)
    path = _spool_dir() / f"{spool_id}.json"
    atomic_json_write(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "session_key": str(session_key or ""),
            "ledger_ids": ledger_ids,
            "sequence": min(ledger_ids),
            "event": _serialize_event(event),
        },
        mode=0o600,
    )
    setattr(event, "_hermes_deferred_spool_path", str(path))
    return path


def remove_deferred_event(event: MessageEvent) -> bool:
    """Remove every physical spool represented by a merged event."""
    raw_path = getattr(event, "_hermes_deferred_spool_path", None)
    ledger_ids = list(
        dict.fromkeys(
            int(value)
            for value in (
                list(getattr(event, "_hermes_gateway_ledger_ids", None) or [])
                + [getattr(event, "_hermes_gateway_ledger_id", None)]
            )
            if value is not None
        )
    )
    if not raw_path and not ledger_ids and not getattr(event, "_hermes_deferred_spool_id", None):
        return False
    paths = [Path(raw_path)] if raw_path else []
    paths.extend(_spool_dir() / f"ledger-{value}.json" for value in ledger_ids)
    if not paths:
        paths = [_spool_dir() / f"{_identity(event)}.json"]
    removed = False
    for path in dict.fromkeys(paths):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("Could not remove deferred-event spool file %s", path, exc_info=True)
    return removed


def _maybe_sweep_expired(*, force: bool = False) -> int:
    """Bound private-content retention during long gateway uptimes."""
    global _last_sweep_monotonic
    now_mono = time.monotonic()
    if not force and now_mono - _last_sweep_monotonic < SWEEP_INTERVAL_SECONDS:
        return 0
    _last_sweep_monotonic = now_mono
    removed = 0
    now = time.time()
    for path in _spool_dir().glob("*.json"):
        try:
            if now - path.stat().st_mtime > MAX_RETENTION_SECONDS:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            logger.warning("Could not inspect expired deferred-event spool %s", path, exc_info=True)
    return removed


def discard_deferred_events_for_session(
    db: Any,
    session_key: str,
    *,
    reason: str,
    max_ledger_id: Optional[int] = None,
) -> int:
    """Terminalize and remove queued events cancelled by a session reset."""
    if not session_key:
        return 0
    removed = 0
    for path in sorted(_spool_dir().glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if str(payload.get("session_key") or "") != str(session_key):
                continue
            ledger_ids = [int(value) for value in payload.get("ledger_ids") or []]
            if max_ledger_id is None or any(
                value >= int(max_ledger_id) for value in ledger_ids
            ):
                continue
            results = [
                db.update_gateway_message_ledger(
                    ledger_id,
                    status="drained",
                    reason=reason,
                )
                for ledger_id in ledger_ids
            ]
            if ledger_ids and all(results):
                path.unlink(missing_ok=True)
                removed += 1
        except Exception:
            logger.warning("Could not discard deferred-event spool file %s", path, exc_info=True)
    return removed


def load_replayable_deferred_events(db: Any) -> list[DeferredSpoolEntry]:
    """Load only events proven never to have started dispatch.

    Ambiguous, terminal, malformed, or ledger-missing records are never replayed.
    Terminal/claimed records are removed because the ledger now owns recovery;
    malformed and ledger-missing records remain for operator inspection.
    """
    replayable: list[DeferredSpoolEntry] = []
    _maybe_sweep_expired(force=True)
    for path in sorted(_spool_dir().glob("*.json")):
        try:
            if time.time() - path.stat().st_mtime > MAX_RETENTION_SECONDS:
                logger.warning("Deleting expired deferred-event spool file %s", path)
                path.unlink(missing_ok=True)
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported deferred-event schema")
            event = _deserialize_event(payload.get("event") or {})
            if event.internal:
                for ledger_id in [
                    int(value) for value in payload.get("ledger_ids") or []
                ]:
                    db.update_gateway_message_ledger(
                        ledger_id,
                        status="drained",
                        reason="internal-spool-rejected",
                    )
                path.unlink(missing_ok=True)
                continue
            source = event.source
            ledger_ids = [int(value) for value in payload.get("ledger_ids") or []]
            ledgers = [db.get_gateway_message_ledger(value) for value in ledger_ids]
            if not ledger_ids:
                primary = db.find_gateway_message_ledger(
                    platform=getattr(source.platform, "value", source.platform),
                    chat_id=source.chat_id,
                    thread_id=source.thread_id,
                    message_id=event.message_id,
                )
                ledgers = [primary] if primary else []
            if not ledgers or any(row is None for row in ledgers):
                logger.warning("Deferred-event spool has no ledger row; preserving %s", path)
                continue
            statuses = [str(row.get("status") or "").lower() for row in ledgers]
            if any(row.get("dispatch_started_at") is not None for row in ledgers) or any(
                status in {"in_progress", "completed", "drained", "failed"}
                for status in statuses
            ):
                path.unlink(missing_ok=True)
                continue
            if any(status not in {"received", "requeued"} for status in statuses):
                logger.warning(
                    "Deferred-event spool has unknown ledger statuses %r; preserving %s",
                    statuses,
                    path,
                )
                continue
            setattr(event, "_hermes_deferred_spool_id", path.stem)
            setattr(event, "_hermes_deferred_spool_path", str(path))
            restored_ids = [int(row["id"]) for row in ledgers if row.get("id") is not None]
            ledger_id = restored_ids[0] if restored_ids else None
            if restored_ids:
                setattr(event, "_hermes_gateway_ledger_id", restored_ids[0])
                setattr(event, "_hermes_gateway_ledger_ids", restored_ids)
            replayable.append(
                DeferredSpoolEntry(
                    path=path,
                    event=event,
                    session_key=str(
                        payload.get("session_key") or ledgers[0].get("session_key") or ""
                    ),
                    ledger_id=ledger_id,
                )
            )
        except Exception:
            logger.warning("Could not load deferred-event spool file %s", path, exc_info=True)

    def _order(entry: DeferredSpoolEntry) -> tuple[int, str]:
        return int(entry.ledger_id or 0), str(entry.path)

    return sorted(replayable, key=_order)
