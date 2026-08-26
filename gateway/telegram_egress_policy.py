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
    if not text:
        return ""
    if text.lstrip("+-").isdigit():
        try:
            return str(int(text, 10))
        except ValueError:
            return text
    return "@" + text.lstrip("@").casefold()


def denied_recipients() -> frozenset[str]:
    """Return the current operator registry plus the emergency env fence.

    A malformed existing registry fails closed: allowing egress after an
    operator-created safety file becomes unreadable would silently remove the
    very control it is meant to provide.  Required registries also fail closed
    when missing.  The file is read on every decision so an operator edit takes
    effect without a process restart or a best-effort watcher.
    """

    denied: set[str] = set()
    denied.update({
        _normalise_recipient(value)
        for value in os.getenv("HERMES_TELEGRAM_EGRESS_DENY_IDS", "").split(",")
        if _normalise_recipient(value)
    })
    path = _deny_registry_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if os.getenv("HERMES_TELEGRAM_EGRESS_DENY_REQUIRED", "").strip() == "1":
            raise TelegramEgressDenied(
                "telegram_egress_deny_registry_missing"
            )
        return frozenset(denied)
    except (OSError, ValueError, TypeError) as exc:
        raise TelegramEgressDenied("telegram_egress_deny_registry_unreadable") from exc
    required_keys = {"version", "blocked_user_ids", "blocked_usernames"}
    if (
        not isinstance(payload, dict)
        or set(payload) != required_keys
        or type(payload.get("version")) is not int
        or payload.get("version") != 1
    ):
        raise TelegramEgressDenied("telegram_egress_deny_registry_invalid")
    blocked_ids = payload["blocked_user_ids"]
    blocked_usernames = payload["blocked_usernames"]
    if not isinstance(blocked_ids, list) or not isinstance(blocked_usernames, list):
        raise TelegramEgressDenied("telegram_egress_deny_registry_invalid")
    if any(
        (type(value) is not int and not isinstance(value, str))
        or (
            isinstance(value, str)
            and (not value.strip() or value != value.strip())
        )
        for value in blocked_ids
    ):
        raise TelegramEgressDenied("telegram_egress_deny_registry_invalid")
    if any(
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        for value in blocked_usernames
    ):
        raise TelegramEgressDenied("telegram_egress_deny_registry_invalid")
    denied.update(_normalise_recipient(value) for value in blocked_ids)
    denied.update(
        _normalise_recipient("@" + value.lstrip("@"))
        for value in blocked_usernames
    )
    return frozenset(denied)


def _clear_denied_recipients_cache() -> None:
    """Compatibility hook for callers that previously cleared an LRU cache."""


# Function attributes keep the public test/operator surface compatible while
# deliberately avoiding a process-lifetime policy cache.
denied_recipients.cache_clear = _clear_denied_recipients_cache  # type: ignore[attr-defined]


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
        raw_envelope = route.get("route_envelope")
        try:
            envelope = canonical_route_envelope(raw_envelope)
        except Exception as exc:
            raise TelegramEgressDenied("unsafe_telegram_business_route") from exc
        if (
            envelope["chat_id"] != str(chat_id)
            or envelope["business_connection_id"] != business_connection_id
            or not envelope["external_safe_mode"]
        ):
            raise TelegramEgressDenied("unsafe_telegram_business_route")
        return
    if plain_dm_allowlist is not None:
        allowed = {
            _normalise_recipient(value)
            for value in plain_dm_allowlist
            if _normalise_recipient(value)
        }
        if _normalise_recipient(chat_id) not in allowed:
            raise TelegramEgressDenied("unsafe_plain_telegram_dm")
    return


def canonical_route_envelope(route: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the durable Telegram route envelope."""

    required = {
        "version",
        "platform",
        "runtime_profile",
        "transport_profile",
        "chat_id",
        "thread_id",
        "user_id",
        "business_connection_id",
        "external_safe_mode",
    }
    if (
        not isinstance(route, Mapping)
        or set(route) != required
        or type(route.get("version")) is not int
        or route.get("version") != 1
        or type(route.get("external_safe_mode")) is not bool
    ):
        raise ValueError("invalid_route_envelope")
    platform = route.get("platform")
    chat_id = route.get("chat_id")
    runtime_profile = route.get("runtime_profile")
    transport_profile = route.get("transport_profile")
    if (
        platform != "telegram"
        or not isinstance(chat_id, str)
        or not chat_id
        or chat_id != chat_id.strip()
        or not isinstance(runtime_profile, str)
        or not runtime_profile
        or runtime_profile != runtime_profile.strip()
        or not isinstance(transport_profile, str)
        or not transport_profile
        or transport_profile != transport_profile.strip()
    ):
        raise ValueError("invalid_route_envelope")
    optional_text = ("thread_id", "user_id", "business_connection_id")
    if any(
        value is not None
        and (
            not isinstance(value, str)
            or not value
            or value != value.strip()
        )
        for key in optional_text
        if (value := route.get(key)) is not None
    ):
        raise ValueError("invalid_route_envelope")
    result = {
        "version": 1,
        "platform": platform,
        "runtime_profile": runtime_profile,
        "transport_profile": transport_profile,
        "chat_id": chat_id,
        "thread_id": route.get("thread_id"),
        "user_id": route.get("user_id"),
        "business_connection_id": route.get("business_connection_id"),
        "external_safe_mode": route["external_safe_mode"],
    }
    if result["external_safe_mode"] and not result["business_connection_id"]:
        raise ValueError("ambiguous_route_envelope")
    return result


def guard_telegram_request(inner_request: Any) -> Any:
    """Wrap one PTB request transport with the single recipient choke point.

    Every Bot API method that can send/edit/react to a chat carries ``chat_id``
    in :class:`RequestData`; one wrapper therefore covers text, media, rich,
    progress, callback edits, retries, and direct raw API calls without copying
    policy checks into each feature path.
    """

    class _RecipientGuardRequest:
        @property
        def inner_request(self):
            return inner_request

        @property
        def read_timeout(self):
            return inner_request.read_timeout

        async def initialize(self) -> None:
            await inner_request.initialize()

        async def shutdown(self) -> None:
            await inner_request.shutdown()

        @staticmethod
        def _check(request_data: Any) -> None:
            for parameter in getattr(request_data, "parameters", None) or ():
                if getattr(parameter, "name", None) == "chat_id":
                    assert_recipient_allowed(getattr(parameter, "value", None))

        async def post(self, url, request_data=None, **timeouts):
            self._check(request_data)
            return await inner_request.post(
                url=url, request_data=request_data, **timeouts
            )

        async def retrieve(self, url, **timeouts):
            return await inner_request.retrieve(url=url, **timeouts)

        async def do_request(
            self, url, method="POST", request_data=None, **timeouts
        ):
            self._check(request_data)
            return await inner_request.do_request(
                url=url,
                method=method,
                request_data=request_data,
                **timeouts,
            )

    return _RecipientGuardRequest()
