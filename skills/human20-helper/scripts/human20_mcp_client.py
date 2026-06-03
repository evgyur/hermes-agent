from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request


DEFAULT_MCP_URL = "https://human20.app/mcp"


class Human20McpError(RuntimeError):
    pass


def _load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class Human20McpClient:
    def __init__(self, base_url: str | None = None, bearer_token: str | None = None, timeout: int = 30) -> None:
        _load_local_env()
        self.base_url = base_url or os.environ.get("HUMAN20_MCP_URL", DEFAULT_MCP_URL)
        self.bearer_token = _normalize_bearer_token(bearer_token or os.environ.get("HUMAN20_BEARER_TOKEN") or "")
        self.timeout = timeout
        self.session_id: str | None = None
        if not self.bearer_token:
            raise Human20McpError("HUMAN20_BEARER_TOKEN is required")

    def _headers(self, include_session: bool = True) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if include_session and self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        return headers

    def _post_raw(self, payload: dict[str, Any], include_session: bool = True) -> tuple[int, dict[str, str], str]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.base_url,
            data=body,
            method="POST",
            headers=self._headers(include_session=include_session),
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                data = response.read().decode("utf-8")
                return response.status, dict(response.headers), data
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            return exc.code, dict(exc.headers), details

    def _parse_json(self, text: str) -> dict[str, Any]:
        if not text.strip():
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise Human20McpError(f"Invalid JSON from Human20 MCP: {exc}") from exc

    def initialize(self) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "human20-helper", "version": "0.1.0"},
            },
        }
        status, headers, text = self._post_raw(payload, include_session=False)
        if status != 200:
            raise Human20McpError(f"initialize failed: {status} {text}")
        decoded = self._parse_json(text)
        self.session_id = headers.get("mcp-session-id") or headers.get("MCP-Session-Id")
        if not self.session_id:
            raise Human20McpError("initialize succeeded but MCP-Session-Id missing")

        notify = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        status2, _, text2 = self._post_raw(notify, include_session=True)
        if status2 not in (200, 202, 204):
            raise Human20McpError(f"notifications/initialized failed: {status2} {text2}")
        return decoded

    def ensure_session(self) -> None:
        if not self.session_id:
            self.initialize()

    def call(self, method: str, params: dict[str, Any] | None = None, retry_on_session: bool = True) -> dict[str, Any]:
        if method != "initialize":
            self.ensure_session()
        payload = {
            "jsonrpc": "2.0",
            "id": "human20-helper",
            "method": method,
            "params": params or {},
        }
        status, _, text = self._post_raw(payload, include_session=(method != "initialize"))
        decoded = self._parse_json(text)

        if status == 200 and decoded:
            error_text = json.dumps(decoded, ensure_ascii=False)
            if "Session not found" in error_text and retry_on_session and method != "initialize":
                self.session_id = None
                return self.call(method, params, retry_on_session=False)
            if "error" in decoded:
                raise Human20McpError(json.dumps(decoded["error"], ensure_ascii=False))
            return decoded

        if retry_on_session and method != "initialize" and "Session not found" in text:
            self.session_id = None
            return self.call(method, params, retry_on_session=False)

        raise Human20McpError(f"Human20 MCP HTTP {status}: {text}")

    def list_tools(self) -> dict[str, Any]:
        return self.call("tools/list")

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.call("tools/call", {"name": name, "arguments": arguments or {}})

    def extract_structured(self, tool_result: dict[str, Any]) -> Any:
        result = tool_result.get("result", {})
        if "structuredContent" in result:
            return result["structuredContent"]
        content = result.get("content") or []
        if content and isinstance(content, list):
            text = content[0].get("text")
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
        return result

    def structured_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return self.extract_structured(self.call_tool(name, arguments))


def _normalize_bearer_token(token: str) -> str:
    normalized = token.strip()
    if normalized.lower().startswith("bearer "):
        normalized = normalized[7:].strip()
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Call Human20 MCP")
    parser.add_argument("method", help="JSON-RPC method or tools/call")
    parser.add_argument("--tool")
    parser.add_argument("--args", default="{}")
    args = parser.parse_args()

    client = Human20McpClient()
    if args.method == "tools/call":
        if not args.tool:
            parser.error("--tool is required for tools/call")
        result = client.call_tool(args.tool, json.loads(args.args))
    else:
        result = client.call(args.method, json.loads(args.args))

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
