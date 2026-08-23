#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL = "sonar"


def direct_api_key() -> str:
    return (os.environ.get("PERPLEXITY_API_KEY") or os.environ.get("PPLX_API_KEY") or "").strip()


def build_payload(query: str, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": query}],
        "temperature": 0.2,
        "search_sources": ["social", "web"],
    }


def call_perplexity(query: str, model: str, timeout: int) -> dict[str, Any]:
    key = direct_api_key()
    if not key:
        raise RuntimeError("Missing PERPLEXITY_API_KEY/PPLX_API_KEY for direct Perplexity API access.")

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(build_payload(query, model), ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1600]
        raise RuntimeError(f"Perplexity HTTP {exc.code}: {body}") from exc


def render_text(payload: dict[str, Any]) -> str:
    if "error" in payload:
        raise RuntimeError(f"Perplexity error: {payload['error']}")

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("Perplexity response did not include choices.")

    answer = choices[0].get("message", {}).get("content", "")
    citations = payload.get("citations") or []
    usage = payload.get("usage") or {}
    cost = usage.get("cost") or {}

    lines = [str(answer).strip(), ""]
    for index, citation in enumerate(citations, 1):
        lines.append(f"[{index}] {citation}")

    total_cost = cost.get("total_cost")
    if total_cost is not None:
        lines.append("")
        try:
            lines.append(f"--- Cost: ${float(total_cost):.4f} | Model: {payload.get('model')} ---")
        except (TypeError, ValueError):
            lines.append(f"--- Cost: {total_cost} | Model: {payload.get('model')} ---")
    elif payload.get("model"):
        lines.append("")
        lines.append(f"--- Model: {payload.get('model')} ---")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Direct Perplexity Sonar search.")
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--json", action="store_true", help="Print raw JSON response")
    parser.add_argument("--check-env", action="store_true", help="Check whether direct Perplexity API credentials are present")
    args = parser.parse_args(argv)

    if args.check_env:
        print(json.dumps({"ok": bool(direct_api_key()), "required": "PERPLEXITY_API_KEY or PPLX_API_KEY"}))
        return 0 if direct_api_key() else 1

    if not args.query:
        parser.error("query is required unless --check-env is used")

    payload = call_perplexity(args.query, args.model, args.timeout)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(render_text(payload))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"perplex_direct error: {exc}", file=sys.stderr)
        raise SystemExit(1)
