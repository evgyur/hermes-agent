#!/usr/bin/env python3
"""Generate an image with GPT-Image-2/Codex using one or more real reference images.

This helper is for identity/likeness/style/layout reference workflows where
Hermes `image_generate` is too weak because it is prompt-only.

Usage:
  python scripts/codex_reference_image_generate.py \
    --ref /path/to/person.jpg \
    --prompt-file /tmp/prompt.md \
    --out /tmp/hermes-image-out.png \
    --size 1536x1024
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, os.environ.get("HERMES_AGENT_DIR", "/opt/hermes-agent"))
from agent.auxiliary_client import (  # noqa: E402
    _codex_cloudflare_headers,
    _read_codex_access_token,
)


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _find_image_result(obj: Any) -> str | None:
    """Recursively find the base64 result from an image_generation_call event."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        if obj.get("type") == "image_generation_call" and isinstance(obj.get("result"), str) and obj.get("result"):
            return obj["result"]
        for value in obj.values():
            found = _find_image_result(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_image_result(item)
            if found:
                return found
    return None


def _stream_image_result(payload: dict[str, Any], token: str) -> str:
    """Call Codex Responses with raw SSE parsing.

    The OpenAI Python client's Responses stream parser can currently crash on
    Codex image streams when the final completed response has output=None, even
    though an image_generation_call result was already emitted. Raw SSE parsing
    avoids losing the generated image.
    """
    headers = {
        **_codex_cloudflare_headers(token),
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    url = "https://chatgpt.com/backend-api/codex/responses"
    payload = {**payload, "stream": True}

    with requests.post(url, headers=headers, json=payload, stream=True, timeout=600) as response:
        if response.status_code >= 400:
            raise SystemExit(f"Codex image request failed: HTTP {response.status_code}: {response.text[:2000]}")

        event_name: str | None = None
        data_lines: list[str] = []
        last_events: list[dict[str, Any]] = []

        def flush_event() -> str | None:
            nonlocal event_name, data_lines, last_events
            if not data_lines:
                event_name = None
                return None
            data = "\n".join(data_lines)
            try:
                obj: Any = json.loads(data)
            except json.JSONDecodeError:
                obj = {"raw": data[:1000]}
            last_events.append({"event": event_name, "data": obj})
            last_events = last_events[-20:]
            result = _find_image_result(obj)
            event_name = None
            data_lines = []
            return result

        for raw_line in response.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="replace")
            line = raw_line.strip("\r")
            if not line:
                result = flush_event()
                if result:
                    return result
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())

        result = flush_event()
        if result:
            return result
        raise SystemExit("No image_generation_call result found in Codex SSE stream. Last events: " + json.dumps(last_events, ensure_ascii=False)[:4000])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", action="append", required=True, help="Reference image path. Repeat for multiple refs.")
    parser.add_argument("--prompt", help="Prompt text. Prefer --prompt-file for long prompts.")
    parser.add_argument("--prompt-file", help="Markdown/text file containing prompt.")
    parser.add_argument("--out", required=True, help="Output PNG path.")
    parser.add_argument("--size", default="1536x1024", choices=["1536x1024", "1024x1024", "1024x1536"])
    parser.add_argument("--quality", default="high", choices=["low", "medium", "high"])
    parser.add_argument("--model", default="gpt-5.5")
    args = parser.parse_args()

    refs = [Path(p).expanduser().resolve() for p in args.ref]
    missing = [str(p) for p in refs if not p.exists()]
    if missing:
        raise SystemExit(f"Missing reference image(s): {', '.join(missing)}")

    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
    elif args.prompt:
        prompt = args.prompt
    else:
        raise SystemExit("Provide --prompt or --prompt-file")

    token = _read_codex_access_token()
    if not token:
        raise SystemExit("No OpenAI-Codex OAuth token found. Refresh Hermes openai-codex auth first.")

    content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
    for ref in refs:
        content.append({"type": "input_image", "image_url": _data_url(ref)})

    payload: dict[str, Any] = {
        "model": args.model,
        "instructions": (
            "Generate the requested image using the image_generation tool. "
            "If references are provided for identity, likeness, style, layout, "
            "or editing, preserve the requested reference role exactly."
        ),
        "input": [{"role": "user", "content": content}],
        "tools": [{"type": "image_generation", "size": args.size, "quality": args.quality, "output_format": "png"}],
        "store": False,
    }

    result_b64 = _stream_image_result(payload, token)
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(result_b64))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
