"""Fail-closed Telegram recipient and route policy.

This module is deliberately transport-neutral so both the live Telegram
adapter and standalone/cron/tool senders enforce the same deny registry.
Keeping the check at each egress boundary prevents a stale session, replay,
callback, watcher, or model instruction from overriding an operator block.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from hermes_constants import get_hermes_home


class TelegramEgressDenied(RuntimeError):
    """Raised before a Telegram API call when a recipient/route is unsafe."""


def _deny_registry_path() -> Path:
    override = os.getenv("HERMES_TELEGRAM_EGRESS_DENY_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return get_hermes_home() / "state" / "telegram_egress_deny.json"


def _normalise_recipient(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("@"):  # usernames are compared case-insensitively
        return "@" + text[1:].casefold()
    return text


def denied_recipients() -> set[str]:
    """Return the union of the durable registry and emergency env fence.

    A malformed existing registry fails closed: allowing egress after an
    operator-created safety file becomes unreadable would silently remove the
    very control it is meant to provide.  A missing file is permitted for
    normal installations; the production pre-start guard pins its presence.
    """

    denied: set[str] = {
        _normalise_recipient(value)
        for value in os.getenv("HERMES_TELEGRAM_EGRESS_DENY_IDS", "").split(",")
        if _normalise_recipient(value)
    }
    path = _deny_registry_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return denied
    except (OSError, ValueError, TypeError) as exc:
        raise TelegramEgressDenied("telegram_egress_deny_registry_unreadable") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise TelegramEgressDenied("telegram_egress_deny_registry_invalid")
    for key in ("blocked_user_ids", "blocked_usernames"):
        values = payload.get(key, [])
        if not isinstance(values, list):
            raise TelegramEgressDenied("telegram_egress_deny_registry_invalid")
        denied.update(
            _normalise_recipient(value)
            for value in values
            if _normalise_recipient(value)
        )
    return denied


def assert_recipient_allowed(chat_id: Any, *, username: Optional[str] = None) -> None:
    denied = denied_recipients()
    candidates = {_normalise_recipient(chat_id)}
    if username:
        candidates.add(_normalise_recipient("@" + username.lstrip("@")))
    if any(candidate and candidate in denied for candidate in candidates):
        raise TelegramEgressDenied("telegram_recipient_denied")


def is_private_peer_id(chat_id: Any) -> bool:
    """Telegram private chats use positive integer ids; groups are negative."""

    try:
        return int(str(chat_id)) > 0
    except (TypeError, ValueError):
        # @username and other unresolved destinations are private-capable and
        # must not bypass a DM allowlist merely because they are non-numeric.
        return True


def assert_route_allowed(
    chat_id: Any,
    *,
    metadata: Optional[Mapping[str, Any]] = None,
    plain_dm_allowlist: Optional[set[str]] = None,
) -> None:
    """Enforce the absolute deny and prevent Business-to-bot-DM fallback."""

    assert_recipient_allowed(chat_id)
    if not is_private_peer_id(chat_id):
        return
    route = metadata or {}
    business_connection_id = str(route.get("business_connection_id") or "").strip()
    if business_connection_id:
        # Connection-scoped egress is valid only when the trust lane survives.
        if not (
            route.get("external_safe_mode")
            or route.get("telegram_business_external_contact")
            or route.get("telegram_business_send_as_account")
        ):
            raise TelegramEgressDenied("unsafe_telegram_business_route")
        return
    allowed = {str(value) for value in (plain_dm_allowlist or set())}
    if str(chat_id) not in allowed:
        raise TelegramEgressDenied("unsafe_telegram_dm_route")


def canonical_route_envelope(route: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the durable Telegram route envelope."""

    if not isinstance(route, Mapping) or route.get("version") != 1:
        raise ValueError("invalid_route_envelope")
    platform = str(route.get("platform") or "")
    chat_id = str(route.get("chat_id") or "")
    if platform != "telegram" or not chat_id:
        raise ValueError("invalid_route_envelope")
    result = {
        "version": 1,
        "platform": platform,
        "runtime_profile": str(
            route.get("runtime_profile") or route.get("profile") or "default"
        ),
        "transport_profile": str(route.get("transport_profile") or "default"),
        "chat_id": chat_id,
        "thread_id": (
            str(route["thread_id"]) if route.get("thread_id") is not None else None
        ),
        "user_id": str(route["user_id"]) if route.get("user_id") else None,
        "business_connection_id": (
            str(route["business_connection_id"])
            if route.get("business_connection_id")
            else None
        ),
        "external_safe_mode": bool(route.get("external_safe_mode", False)),
    }
    if result["external_safe_mode"] and not result["business_connection_id"]:
        raise ValueError("ambiguous_route_envelope")
    return result
