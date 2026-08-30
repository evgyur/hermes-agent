"""Governed stateless host bridges using only public plugin boundaries."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 15) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("service returned a non-object JSON response")
    return value


def _mem0g_base() -> str:
    return (os.environ.get("MEM0G_BASE_URL") or os.environ.get("MEM0G_ENDPOINT") or "http://127.0.0.1:8081").rstrip("/")


def _mem0g_request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    api_key = os.environ.get("MEM0G_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MEM0G_API_KEY is not configured")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _mem0g_base() + path,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("mem0g returned a non-object response")
    return value


def handle_mem0g(args: dict[str, Any], **_: Any) -> str:
    action = str(args.get("action") or "search")
    try:
        if action == "health":
            result = _mem0g_request("GET", "/health")
        elif action in {"search", "recall"}:
            query = str(args.get("query") or "").strip()
            if not query:
                raise ValueError("query is required")
            result = _mem0g_request(
                "POST",
                "/v1/memories/search",
                {
                    "query": query,
                    "max_results": max(1, min(int(args.get("max_results") or 5), 20)),
                    "acl": ["yellow"],
                    "curation": ["raw", "candidate", "active"],
                },
            )
        elif action == "write":
            text = str(args.get("text") or "").strip()
            if not text:
                raise ValueError("text is required")
            result = _mem0g_request("POST", "/v1/memories", {"content": text, "acl": "yellow", "curation": "raw"})
        else:
            raise ValueError(f"unknown action: {action}")
        return _json({"ok": True, "mem0g_used": True, "fallback_used": False, "result": result})
    except Exception as exc:
        return _json({"ok": False, "degraded": True, "fail_closed": True, "mem0g_used": False, "fallback_used": False, "error": str(exc)})


def _continuum_socket() -> Path:
    return Path(os.environ.get("CONTINUUM_CONTROL_SOCKET", "/run/continuumd/control.sock"))


def handle_continuum(args: dict[str, Any], **_: Any) -> str:
    """Call the host-owned daemon without storing gateway or delivery state."""
    method = str(args.get("method") or "health").strip()
    params = args.get("params") or {}
    if not isinstance(params, dict):
        return _json({"ok": False, "error": "params must be an object"})
    path = _continuum_socket()
    if os.name == "nt" or not path.exists():
        return _json({"ok": False, "degraded": True, "fail_closed": True, "error": f"Continuum control socket unavailable: {path}"})
    request = json.dumps({"jsonrpc": "2.0", "id": "powerpack-gen2", "method": method, "params": params}).encode("utf-8") + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(str(path))
            client.sendall(request)
            raw = client.makefile("rb").readline(1_048_577)
        if len(raw) > 1_048_576:
            raise RuntimeError("Continuum response exceeded 1 MiB")
        response = json.loads(raw.decode("utf-8"))
        return _json({"ok": "error" not in response, "response": response})
    except Exception as exc:
        return _json({"ok": False, "degraded": True, "fail_closed": True, "error": str(exc)})


_CHIPMANAGER_BASE = "http://127.0.0.1:18083"


def _chipmanager_base() -> str:
    return os.environ.get("TELEGRAM_CHIP_BASE_URL", _CHIPMANAGER_BASE).rstrip("/")


def _unwrap(payload: dict[str, Any]) -> Any:
    value = payload.get("data", payload)
    if isinstance(value, str) and value.startswith(("{", "[")):
        return json.loads(value)
    return value


def handle_chipmanager(args: dict[str, Any], **_: Any) -> str:
    base = _chipmanager_base()
    if base != _CHIPMANAGER_BASE:
        return _json({"ok": False, "refused": True, "error": "employee Telegram runtime must be exact loopback port 18083"})
    action = str(args.get("action") or "health")
    try:
        identity = _unwrap(_http_json("GET", base + "/me"))
        if not isinstance(identity, dict) or identity.get("username") != "chipmanager":
            raise RuntimeError("employee Telegram identity is not chipmanager")
        if action == "health":
            health = _unwrap(_http_json("GET", base + "/health"))
            if not isinstance(health, dict) or health.get("status") != "ok" or not health.get("telegram_connected"):
                raise RuntimeError("chipmanager runtime is unhealthy")
            return _json({"ok": True, "username": "chipmanager", "telegram_connected": True})
        if action != "send":
            raise ValueError(f"unknown action: {action}")
        chat_id = str(args.get("chat_id") or "").strip()
        message = str(args.get("message") or "")
        authority = str(args.get("authority") or "")
        if not chat_id or not message or authority != "explicit-user-request":
            raise ValueError("send requires exact chat_id, message, and authority=explicit-user-request")
        sent = _http_json("POST", base + "/messages/send", {"chat_id": chat_id, "message": message}, timeout=20)
        if not sent.get("success"):
            raise RuntimeError("chipmanager runtime denied or failed the send")
        import re

        match = re.search(r"Message ID:\s*(\d+)", str(sent.get("data", "")))
        if not match:
            raise RuntimeError("chipmanager returned no message ID")
        message_id = int(match.group(1))
        fetched = _http_json("GET", f"{base}/chats/{urllib.parse.quote(chat_id, safe='')}/messages/{message_id}")
        readback = _unwrap(fetched)
        if not fetched.get("success") or not isinstance(readback, dict) or readback.get("text") != message:
            raise RuntimeError("chipmanager exact message readback failed")
        return _json({"ok": True, "message_id": message_id, "readback": "exact"})
    except (urllib.error.URLError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        return _json({"ok": False, "fail_closed": True, "error": str(exc)})


MEM0G_SCHEMA = {
    "name": "mem0g",
    "description": "Governed shared-memory health, recall, search, and writes. Fails closed without local-memory fallback.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["health", "search", "recall", "write"]},
            "query": {"type": "string"},
            "text": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

CONTINUUM_SCHEMA = {
    "name": "continuum_host",
    "description": "Call the host-owned Continuum control socket without owning gateway delivery or lifecycle state.",
    "parameters": {
        "type": "object",
        "properties": {"method": {"type": "string"}, "params": {"type": "object"}},
        "required": ["method"],
        "additionalProperties": False,
    },
}

CHIPMANAGER_SCHEMA = {
    "name": "chipmanager_telegram",
    "description": "Employee-only chipmanager health or exact-target send with mandatory message-ID readback.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["health", "send"]},
            "chat_id": {"type": "string"},
            "message": {"type": "string"},
            "authority": {"type": "string", "enum": ["explicit-user-request"]},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}
