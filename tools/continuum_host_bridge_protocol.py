"""Dependency-free AF_UNIX clients used by the Hermes-owned Continuum card bridge."""
from __future__ import annotations

import json
import secrets
import socket
from pathlib import Path
from typing import Any, Final

V1_VERSION: Final = 1
V2_VERSION: Final = 2
V1_MAX_FRAME: Final = 65_536
V2_MAX_FRAME: Final = 131_072
METHOD_CAPABILITY: Final = {
    "create": "continuum.launch",
    "bind_card": "continuum.card",
    "list": "continuum.read",
    "status": "continuum.read",
    "result": "continuum.read",
    "send": "continuum.send",
    "stop": "continuum.stop",
    "tree": "continuum.read",
    "children": "continuum.read",
    "schedules": "continuum.read",
    "heartbeats": "continuum.read",
}


class V2ProtocolError(RuntimeError):
    def __init__(self, code: str, diagnostic: str) -> None:
        super().__init__(f"{code}: {diagnostic}")
        self.code = code
        self.diagnostic = diagnostic


def _exchange(
    socket_path: Path,
    request: dict[str, Any],
    *,
    timeout: float,
    max_frame: int,
) -> dict[str, Any]:
    encoded = (json.dumps(request, separators=(",", ":"), sort_keys=True) + "\n").encode()
    if len(encoded) > max_frame:
        raise ValueError("request exceeds protocol frame")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall(encoded)
        data = bytearray()
        while b"\n" not in data and len(data) <= max_frame:
            chunk = client.recv(min(4096, max_frame + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
    if len(data) > max_frame or b"\n" not in data:
        raise RuntimeError("invalid daemon response")
    frame, trailing = bytes(data).split(b"\n", 1)
    if trailing:
        raise RuntimeError("invalid daemon response framing")
    try:
        value = json.loads(frame)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("invalid daemon response JSON") from None
    if not isinstance(value, dict):
        raise RuntimeError("invalid daemon response")
    return value


def call_v1(
    socket_path: Path, method: str, params: dict[str, Any], *, timeout: float = 2.0
) -> dict[str, Any]:
    request_id = secrets.token_urlsafe(18)
    value = _exchange(
        socket_path,
        {"version": V1_VERSION, "request_id": request_id, "method": method, "params": params},
        timeout=timeout,
        max_frame=V1_MAX_FRAME,
    )
    if (
        value.get("version") != V1_VERSION
        or value.get("request_id") != request_id
        or not isinstance(value.get("ok"), bool)
    ):
        raise RuntimeError("invalid daemon response")
    return value


def call_v2(
    socket_path: Path,
    method: str,
    params: dict[str, Any],
    *,
    client_id: str,
    capabilities: list[str],
    command_id: str | None = None,
    event_cursor: int | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "version": V2_VERSION,
        "client_id": client_id,
        "command_id": command_id or f"command:{secrets.token_urlsafe(18)}",
        "method": method,
        "capabilities": capabilities,
        "params": params,
    }
    if event_cursor is not None:
        request["event_cursor"] = event_cursor
    value = _exchange(
        socket_path, request, timeout=timeout, max_frame=V2_MAX_FRAME
    )
    if (
        value.get("version") != V2_VERSION
        or value.get("command_id") != request["command_id"]
        or not isinstance(value.get("ok"), bool)
        or isinstance(value.get("event_cursor"), bool)
        or not isinstance(value.get("event_cursor"), int)
    ):
        raise RuntimeError("invalid daemon response")
    if value["ok"] is not True:
        error = value.get("error")
        if not isinstance(error, dict):
            raise RuntimeError("invalid daemon error")
        raise V2ProtocolError(
            str(error.get("code", "UNKNOWN")), str(error.get("diagnostic", ""))
        )
    if not isinstance(value.get("result"), dict):
        raise RuntimeError("invalid daemon result")
    return value


__all__ = ["METHOD_CAPABILITY", "V2ProtocolError", "call_v1", "call_v2"]
