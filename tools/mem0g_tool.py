#!/usr/bin/env python3
"""Hermes mem0g tool and health/smoke CLI.

This tool gives Hermes a governed path to the shared mem0g API. It intentionally
does not read local Hermes memory as a fallback: if mem0g is configured but
unavailable, recall/search returns an explicit degraded state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_ENV_FILE = "/home/hermes/.hermes/secrets/mem0g-hermes-agent.env"


def _load_env_file(path: str | None = None) -> None:
    env_path = path or os.environ.get("MEM0G_ENV_FILE") or DEFAULT_ENV_FILE
    p = Path(env_path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _config() -> dict[str, str]:
    _load_env_file()
    base_url = os.environ.get("MEM0G_BASE_URL") or os.environ.get("MEM0G_ENDPOINT") or "http://127.0.0.1:8081"
    return {
        "base_url": base_url.rstrip("/"),
        "api_key": os.environ.get("MEM0G_API_KEY", ""),
        "actor_id": os.environ.get("MEM0G_ACTOR_ID", "hermes-agent:ryzen64"),
        "environment": os.environ.get("MEM0G_ENVIRONMENT", "prod"),
        "canonical_host": os.environ.get("MEM0G_CANONICAL_HOST", "ryzen64"),
    }


def _request_json(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _config()
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{cfg['base_url']}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            text = response.read().decode("utf-8")
            body = json.loads(text or "{}")
            body.setdefault("_http_status", response.status)
            return body
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(text or "{}")
        except json.JSONDecodeError:
            body = {"message": text}
        body["_http_status"] = exc.code
        raise RuntimeError(f"mem0g HTTP {exc.code}: {body.get('error') or body.get('message') or exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"mem0g unavailable: {type(exc).__name__}: {exc}") from exc


def _health() -> dict[str, Any]:
    cfg = _config()
    body = _request_json("GET", "/health")
    return {
        "ok": body.get("status") == "ok",
        "mem0g_used": False,
        "actor_id": cfg["actor_id"],
        "base_url": cfg["base_url"],
        "health": body,
    }


def _search(query: str, max_results: int = 5) -> dict[str, Any]:
    if not query:
        return {"ok": False, "error": "query is required"}
    payload = {
        "query": query,
        "max_results": max(1, min(int(max_results or 5), 20)),
        "acl": ["yellow"],
        "curation": ["raw", "candidate", "active"],
    }
    body = _request_json("POST", "/v1/memories/search", payload)
    results = body.get("results") or []
    return {
        "ok": True,
        "degraded": False,
        "mem0g_used": True,
        "fallback_used": False,
        "legacy_used": False,
        "query": query,
        "count": len(results),
        "request_id": body.get("request_id"),
        "audit_id": body.get("audit_id"),
        "results": results,
    }


def _write(text: str) -> dict[str, Any]:
    if not text:
        return {"ok": False, "error": "text is required"}
    cfg = _config()
    now = int(time.time() * 1000)
    source_ref = f"hermes-mem0g-{cfg['canonical_host']}-{now}"
    payload = {
        "content": text,
        "acl": "yellow",
        "curation": "raw",
        "confidence": 0.55,
        "source_type": "hermes_mem0g_tool",
        "source_ref": source_ref,
        "idempotency_key": source_ref,
        "environment": cfg["environment"],
        "metadata": {
            "actor_id": cfg["actor_id"],
            "source_system": "hermes-agent",
            "source_host": cfg["canonical_host"],
            "source_class": "hermes_mem0g_tool",
            "digest_eligible": False,
        },
        "tags": ["hermes", "mem0g", f"host:{cfg['canonical_host']}"],
    }
    body = _request_json("POST", "/v1/memories", payload)
    return {
        "ok": True,
        "mem0g_used": True,
        "id": body.get("id"),
        "created": body.get("created"),
        "request_id": body.get("request_id"),
        "audit_id": body.get("audit_id"),
    }


def _handle_mem0g(args: dict[str, Any], **_: Any) -> str:
    action = str(args.get("action") or "search")
    try:
        if action == "health":
            result = _health()
        elif action in {"search", "recall"}:
            result = _search(str(args.get("query") or ""), int(args.get("max_results") or 5))
        elif action == "write":
            result = _write(str(args.get("text") or ""))
        else:
            result = {"ok": False, "error": f"unknown action: {action}"}
    except Exception as exc:
        result = {
            "ok": False,
            "degraded": True,
            "mem0g_used": False,
            "fallback_used": False,
            "fail_closed": True,
            "error": str(exc),
        }
    return json.dumps(result, ensure_ascii=False)


def _check_mem0g_reqs() -> bool:
    cfg = _config()
    return bool(cfg["base_url"] and cfg["api_key"] and cfg["actor_id"])


MEM0G_SCHEMA = {
    "name": "mem0g",
    "description": (
        "Governed shared-memory access through mem0g. Use recall/search for "
        "memory questions and preserve exact @handles, usernames, IDs, and URLs "
        "in the query. If mem0g is unavailable, report the degraded state instead "
        "of answering from local memory as if complete."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["health", "search", "recall", "write"],
                "description": "Operation to run.",
            },
            "query": {
                "type": "string",
                "description": "Exact recall/search query. Preserve handles, usernames, IDs, and URLs.",
            },
            "text": {
                "type": "string",
                "description": "Memory text to write for governed raw/yellow records.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Maximum search results.",
            },
        },
        "required": ["action"],
    },
}


from tools.registry import registry

registry.register(
    name="mem0g",
    toolset="mem0g",
    schema=MEM0G_SCHEMA,
    handler=_handle_mem0g,
    check_fn=_check_mem0g_reqs,
    requires_env=["MEM0G_API_KEY", "MEM0G_ACTOR_ID"],
    description=MEM0G_SCHEMA["description"],
    emoji="M",
    max_result_size_chars=60_000,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes mem0g health/search/smoke")
    parser.add_argument("--mode", choices=["health", "search", "smoke"], default="health")
    parser.add_argument("--query", default="Hermes mem0g smoke")
    parser.add_argument("--text", default="")
    args = parser.parse_args(argv)

    try:
        if args.mode == "health":
            result = _health()
            if result.get("ok"):
                auth_result = _search("Hermes mem0g health", 1)
                result["auth_search_status"] = "ok" if auth_result.get("ok") else "failed"
                result["request_id"] = auth_result.get("request_id")
                result["audit_id"] = auth_result.get("audit_id")
        elif args.mode == "search":
            result = _search(args.query, 5)
        else:
            text = args.text or f"Hermes mem0g smoke {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"
            write = _write(text)
            search = _search("Hermes mem0g smoke", 5)
            result = {"ok": bool(write.get("ok") and search.get("ok")), "write": write, "search": search}
    except Exception as exc:
        result = {
            "ok": False,
            "degraded": True,
            "mem0g_used": False,
            "fail_closed": True,
            "error": str(exc),
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
