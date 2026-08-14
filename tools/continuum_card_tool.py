"""Trusted same-message task-card capability for standalone Continuum.

The model supplies work and a complete checklist, never Telegram routing or a
provider message id. Routing comes only from the current gateway session cache.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
import weakref
from pathlib import Path
from typing import Any

from hermes_continuum.card_bridge import Bridge
from hermes_continuum.kernel.client import call as daemon_call
from tools.registry import registry

log = logging.getLogger(__name__)
_SOCKET = Path("/run/continuumd/control.sock")
_DB = Path.home() / ".hermes" / "state" / "continuum-card-bridge.sqlite3"
_RUNNER: weakref.ReferenceType[Any] | None = None
_LOOP: asyncio.AbstractEventLoop | None = None
_WATCHER: threading.Thread | None = None
_WATCHER_LOCK = threading.Lock()


def _check_requirements() -> bool:
    return _SOCKET.exists() and _SOCKET.is_socket()


def _origin_json(source: Any, session_key: str) -> str:
    platform = str(getattr(getattr(source, "platform", None), "value", ""))
    if not platform or not getattr(source, "chat_id", None):
        raise RuntimeError("trusted gateway origin unavailable")
    return json.dumps(
        {
            "platform": platform,
            "chat_id": str(source.chat_id),
            "chat_type": str(getattr(source, "chat_type", "dm") or "dm"),
            "thread_id": str(getattr(source, "thread_id", "") or ""),
            "user_id": str(getattr(source, "user_id", "") or ""),
            "profile": str(getattr(source, "profile", "") or ""),
            "business_connection_id": str(
                getattr(source, "business_connection_id", "") or ""
            ),
            "session_key": session_key,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _source_from_origin(origin: str) -> Any:
    from gateway.config import Platform
    from gateway.session import SessionSource

    data = json.loads(origin)
    if not isinstance(data, dict) or set(data) != {
        "platform",
        "chat_id",
        "chat_type",
        "thread_id",
        "user_id",
        "profile",
        "business_connection_id",
        "session_key",
    }:
        raise RuntimeError("invalid durable bridge origin")
    return SessionSource(
        platform=Platform(data["platform"]),
        chat_id=data["chat_id"],
        chat_type=data["chat_type"],
        thread_id=data["thread_id"] or None,
        user_id=data["user_id"] or None,
        profile=data["profile"] or None,
        business_connection_id=data["business_connection_id"] or None,
    )


def _runner() -> Any:
    runner = _RUNNER() if _RUNNER is not None else None
    if runner is None or _LOOP is None or not _LOOP.is_running():
        raise RuntimeError("gateway card bridge unavailable")
    return runner


class _GatewayAdapter:
    def send(self, origin: str, text: str) -> str:
        runner = _runner()
        loop = _LOOP
        assert loop is not None
        source = _source_from_origin(origin)
        adapter = runner._adapter_for_source(source)
        if adapter is None:
            raise RuntimeError("origin adapter unavailable")
        metadata = runner._thread_metadata_for_source(source)
        future = asyncio.run_coroutine_threadsafe(
            adapter.send(str(source.chat_id), text, metadata=metadata), loop
        )
        result = future.result(timeout=30)
        if result is None or getattr(result, "success", True) is False:
            raise RuntimeError("provider did not confirm card creation")
        message_id = str(getattr(result, "message_id", "") or "")
        if not message_id:
            raise RuntimeError("provider returned no card message id")
        return message_id

    def edit(self, origin: str, message_id: str, text: str) -> None:
        runner = _runner()
        loop = _LOOP
        assert loop is not None
        source = _source_from_origin(origin)
        adapter = runner._adapter_for_source(source)
        if adapter is None:
            raise RuntimeError("origin adapter unavailable")
        future = asyncio.run_coroutine_threadsafe(
            adapter.edit_message(str(source.chat_id), message_id, text), loop
        )
        result = future.result(timeout=30)
        if result is None or getattr(result, "success", True) is False:
            raise RuntimeError("provider did not confirm card edit")


def _call(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return daemon_call(_SOCKET, method, params)


def _watch() -> None:
    bridge = Bridge(_DB, _call, _GatewayAdapter())
    while True:
        try:
            if _RUNNER is None or _RUNNER() is None:
                return
            count = bridge.tick(limit=32)
        except Exception:
            log.exception("Continuum card watcher tick failed")
            count = 0
        time.sleep(1.0 if count else 3.0)


def _ensure_watcher() -> None:
    global _WATCHER
    with _WATCHER_LOCK:
        if _WATCHER is not None and _WATCHER.is_alive():
            return
        _WATCHER = threading.Thread(
            target=_watch,
            name="continuum-card-watcher",
            daemon=True,
        )
        _WATCHER.start()


def bind_gateway_runner(runner: Any) -> None:
    """Bind the live runner after its gateway loop exists; resume durable rows."""
    global _RUNNER, _LOOP
    loop = getattr(runner, "_gateway_loop", None)
    if loop is None or not loop.is_running():
        raise RuntimeError("gateway loop unavailable")
    _RUNNER = weakref.ref(runner)
    _LOOP = loop
    _ensure_watcher()


def _handle(args: dict[str, Any], **_: Any) -> str:
    from gateway.session_context import get_session_env
    from gateway.platforms.base import BasePlatformAdapter

    if set(args) - {"goal", "context", "idempotency_key", "plan"}:
        raise ValueError("unsupported argument")
    goal = args.get("goal")
    context = args.get("context", "")
    idem = args.get("idempotency_key")
    labels = args.get("plan")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("goal is required")
    if not isinstance(context, str):
        raise ValueError("context must be text")
    if not isinstance(idem, str) or len(idem) < 12:
        raise ValueError("stable idempotency_key is required")
    if not isinstance(labels, list) or not labels or len(labels) > 64:
        raise ValueError("complete plan is required")
    if any(not isinstance(label, str) or not label.strip() for label in labels):
        raise ValueError("plan labels must be non-empty strings")

    runner = _runner()
    session_key = get_session_env("HERMES_SESSION_KEY")
    sources = getattr(runner, "_session_sources", None) or {}
    source = sources.get(session_key)
    if source is None:
        raise RuntimeError("trusted current-session origin unavailable")
    adapter = runner._adapter_for_source(source)
    if adapter is None or type(adapter).edit_message is BasePlatformAdapter.edit_message:
        raise RuntimeError("current provider cannot edit a task card")

    origin = _origin_json(source, session_key)
    plan = [{"id": str(index), "label": label.strip()} for index, label in enumerate(labels, 1)]
    bridge = Bridge(_DB, _call, _GatewayAdapter())
    result = bridge.launch(origin, goal.strip(), context, idem, plan)
    _ensure_watcher()
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


registry.register(
    name="continuum_card_launch",
    toolset="hermes-cli",
    schema={
        "name": "continuum_card_launch",
        "description": "Create one trusted same-message Continuum task card and start work only after provider acknowledgement.",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "context": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "plan": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 64,
                },
            },
            "required": ["goal", "idempotency_key", "plan"],
            "additionalProperties": False,
        },
    },
    handler=_handle,
    check_fn=_check_requirements,
)
