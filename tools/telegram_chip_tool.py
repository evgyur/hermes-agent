"""Read-only Telegram Chip API tools for Human20Team.

These tools expose a Telethon user-session companion service to Hermes so the bot
can inspect chats where @chipmanager is a member. They intentionally do not send,
edit, delete, invite, promote, or mutate Telegram state.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from tools.registry import registry

DEFAULT_BASE_URL = "http://127.0.0.1:18083"
MAX_RESULT_CHARS = 24000


def _base_url() -> str:
    return os.getenv("TELEGRAM_CHIP_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _check() -> bool:
    # Keep schema available even while the user session is being authorized; the
    # handler will return a clear API-down/unauthorized error until service works.
    return True


def _redact(text: str) -> str:
    # Redact phone fields even when nested JSON is stored as an escaped string.
    text = re.sub(r'(phone\\?\"?\s*:\s*\\?\"?)[+0-9 ]+', r'\1<redacted>', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<!\d)\+?7\d{10}(?!\d)', '<redacted-phone>', text)
    return text


def _truncate(obj: Any, max_chars: int = MAX_RESULT_CHARS) -> str:
    text = _redact(json.dumps(obj, ensure_ascii=False, indent=2))
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _request(method: str, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None) -> str:
    url = f"{_base_url()}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    data = None
    headers = {"Accept": "application/json"}
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:4000]
        return json.dumps({"success": False, "error": f"telegram-chip HTTP {e.code}", "body": body}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": f"telegram-chip unavailable: {type(e).__name__}: {e}"}, ensure_ascii=False)
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {"raw": raw}
    if isinstance(parsed, dict) and parsed.get("success") is True and parsed.get("error") is None:
        parsed.pop("error", None)
    return _truncate(parsed)


def telegram_chip_me() -> str:
    """Return the authorized Telegram user for the @chipmanager session."""
    return _request("GET", "/me")


def telegram_chip_chats(limit: int = 50, chat_type: str | None = None, archived: bool = False, unread_only: bool = False) -> str:
    """List chats visible to @chipmanager."""
    limit = max(1, min(int(limit or 50), 500))
    return _request("GET", "/chats", params={"limit": limit, "chat_type": chat_type, "archived": archived, "unread_only": unread_only})


def telegram_chip_messages(chat_id: str, page: int = 1, page_size: int = 30) -> str:
    """Read recent/paginated messages from a Telegram chat visible to @chipmanager."""
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 30), 100))
    safe_chat = urllib.parse.quote(str(chat_id), safe="")
    return _request("GET", f"/chats/{safe_chat}/messages", params={"page": page, "page_size": page_size})


def telegram_chip_search(chat_id: str, query: str, limit: int = 20, from_user: str | None = None) -> str:
    """Search messages inside a Telegram chat visible to @chipmanager."""
    limit = max(1, min(int(limit or 20), 100))
    return _request("POST", "/messages/search", json_body={"chat_id": chat_id, "query": query, "limit": limit, "from_user": from_user})


def telegram_chip_resolve(username: str) -> str:
    """Resolve a Telegram @username/chat handle visible to @chipmanager."""
    username = str(username).lstrip("@")
    safe = urllib.parse.quote(username, safe="")
    return _request("GET", f"/resolve/{safe}")


registry.register(
    name="telegram_chip_me",
    toolset="telegram_chip",
    schema={
        "name": "telegram_chip_me",
        "description": "Read-only: show which Telegram account is authorized in the Human20Team telegram-chip session.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    handler=lambda args, **kw: telegram_chip_me(),
    check_fn=_check,
    emoji="📨",
)

registry.register(
    name="telegram_chip_chats",
    toolset="telegram_chip",
    schema={
        "name": "telegram_chip_chats",
        "description": "Read-only: list Telegram chats visible to @chipmanager via Human20Team telegram-chip.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
                "chat_type": {"type": ["string", "null"], "enum": ["user", "group", "channel", None]},
                "archived": {"type": "boolean", "default": False},
                "unread_only": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    handler=lambda args, **kw: telegram_chip_chats(args.get("limit", 50), args.get("chat_type"), args.get("archived", False), args.get("unread_only", False)),
    check_fn=_check,
    emoji="📨",
)

registry.register(
    name="telegram_chip_messages",
    toolset="telegram_chip",
    schema={
        "name": "telegram_chip_messages",
        "description": "Read-only: get recent/paginated messages from a Telegram chat visible to @chipmanager.",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "Telegram numeric chat ID or username/handle."},
                "page": {"type": "integer", "default": 1, "minimum": 1},
                "page_size": {"type": "integer", "default": 30, "minimum": 1, "maximum": 100},
            },
            "required": ["chat_id"],
            "additionalProperties": False,
        },
    },
    handler=lambda args, **kw: telegram_chip_messages(args["chat_id"], args.get("page", 1), args.get("page_size", 30)),
    check_fn=_check,
    emoji="📨",
)

registry.register(
    name="telegram_chip_search",
    toolset="telegram_chip",
    schema={
        "name": "telegram_chip_search",
        "description": "Read-only: search messages in a Telegram chat visible to @chipmanager.",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                "from_user": {"type": ["string", "null"]},
            },
            "required": ["chat_id", "query"],
            "additionalProperties": False,
        },
    },
    handler=lambda args, **kw: telegram_chip_search(args["chat_id"], args["query"], args.get("limit", 20), args.get("from_user")),
    check_fn=_check,
    emoji="📨",
)

registry.register(
    name="telegram_chip_resolve",
    toolset="telegram_chip",
    schema={
        "name": "telegram_chip_resolve",
        "description": "Read-only: resolve a Telegram @username/chat handle visible to @chipmanager.",
        "parameters": {
            "type": "object",
            "properties": {"username": {"type": "string"}},
            "required": ["username"],
            "additionalProperties": False,
        },
    },
    handler=lambda args, **kw: telegram_chip_resolve(args["username"]),
    check_fn=_check,
    emoji="📨",
)
