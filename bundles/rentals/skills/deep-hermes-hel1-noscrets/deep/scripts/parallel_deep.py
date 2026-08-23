#!/usr/bin/env python3
"""Parallel Task API deep research runner for Hermes /deep skill.

Usage:
  parallel_deep.py "research question" --processor ultra --timeout 3600 --output /tmp/report.md
  parallel_deep.py --prompt-file prompt.md --processor pro-fast --schema text
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

API_BASE = "https://api.parallel.ai/v1"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except PermissionError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def api_request(method: str, path: str, api_key: str, payload: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    data = None
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API_BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Parallel API HTTP {e.code}: {body[:2000]}") from e
    except TimeoutError as e:
        raise RuntimeError(f"Parallel API timeout after {timeout}s") from e


def pick_status(d: dict[str, Any]) -> str:
    return str((d.get("run") or {}).get("status") or d.get("status") or "unknown")


def extract_markdown(result: dict[str, Any]) -> str:
    out = result.get("output")
    if isinstance(out, str):
        return out
    if isinstance(out, dict):
        for key in ("content", "output", "text", "markdown"):
            val = out.get(key)
            if isinstance(val, str):
                return val
        # Auto-schema: keep content compact but preserve citations/basis.
        return json.dumps(out, ensure_ascii=False, indent=2)
    return json.dumps(result, ensure_ascii=False, indent=2)


def main() -> int:
    load_env_file(Path.home() / ".hermes" / ".env")
    load_env_file(Path("/opt/clawd-workspace/.env"))

    p = argparse.ArgumentParser(description="Run Parallel Task API Deep Research and save markdown + raw JSON")
    p.add_argument("query", nargs="?", help="Research question. Prefer concise prompts under 15k chars.")
    p.add_argument("--prompt-file", help="Read research prompt from a UTF-8 file")
    p.add_argument("--processor", default="ultra", help="Recommended: pro, pro-fast, ultra, ultra-fast. Default: ultra")
    p.add_argument("--schema", choices=["text", "auto"], default="text", help="text=markdown report; auto=structured JSON")
    p.add_argument("--description", default="Return a rigorous analyst-grade markdown report with inline citations, source URLs, confidence notes, and an operator-ready action section.", help="Description for text output schema")
    p.add_argument("--timeout", type=int, default=3600, help="Overall wait timeout seconds; deep research can take up to 45m")
    p.add_argument("--poll", type=int, default=20, help="Polling interval seconds")
    p.add_argument("--output", default="", help="Markdown/text output path. Default: ~/.hermes/research/deep_<run_id>.md")
    p.add_argument("--raw-output", default="", help="Raw JSON output path. Default: same as output with .json")
    p.add_argument("--previous-interaction-id", default="", help="Follow-up on a previous Parallel interaction")
    p.add_argument("--api-key", default=os.getenv("PARALLEL_API_KEY", ""), help=argparse.SUPPRESS)
    args = p.parse_args()

    query = args.query or ""
    if args.prompt_file:
        query = Path(args.prompt_file).read_text(encoding="utf-8")
    query = query.strip()
    if not query:
        print("ERROR: query or --prompt-file is required", file=sys.stderr)
        return 2
    if len(query) > 15000:
        print(f"ERROR: prompt is {len(query)} chars; Parallel recommends <15000 for deep research", file=sys.stderr)
        return 2
    if not args.api_key:
        print("ERROR: PARALLEL_API_KEY is not set in ~/.hermes/.env or environment", file=sys.stderr)
        return 2

    payload: dict[str, Any] = {"input": query, "processor": args.processor}
    if args.schema == "text":
        payload["task_spec"] = {"output_schema": {"type": "text", "description": args.description}}
    if args.previous_interaction_id:
        payload["previous_interaction_id"] = args.previous_interaction_id

    created = api_request("POST", "/tasks/runs", args.api_key, payload, timeout=60)
    run_id = created.get("run_id") or (created.get("run") or {}).get("run_id")
    interaction_id = created.get("interaction_id") or (created.get("run") or {}).get("interaction_id")
    if not run_id:
        print("ERROR: Parallel did not return run_id", file=sys.stderr)
        print(json.dumps(created, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    print(f"RUN_ID={run_id}")
    if interaction_id:
        print(f"INTERACTION_ID={interaction_id}")
    print(f"PROCESSOR={args.processor}")
    print("STATUS=running")

    deadline = time.time() + args.timeout
    last_status = "running"
    result: dict[str, Any] | None = None
    while time.time() < deadline:
        try:
            status_doc = api_request("GET", f"/tasks/runs/{run_id}", args.api_key, timeout=30)
            status = pick_status(status_doc)
            result = status_doc
        except RuntimeError as e:
            print(f"WARN: status poll failed: {e}", file=sys.stderr)
            time.sleep(args.poll)
            continue
        if status != last_status:
            print(f"STATUS={status}")
            last_status = status
        if status == "completed":
            break
        if status in {"failed", "canceled", "cancelled"}:
            print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        time.sleep(args.poll)
    else:
        print(f"TIMEOUT after {args.timeout}s; run_id={run_id}", file=sys.stderr)
        return 124

    # Fetch final result after status says completed; result endpoint may long-poll while running.
    try:
        result = api_request("GET", f"/tasks/runs/{run_id}/result", args.api_key, timeout=120)
    except RuntimeError as e:
        print(f"ERROR: completed run but final result fetch failed: {e}", file=sys.stderr)
        return 1
    md = extract_markdown(result)
    out_path = Path(args.output) if args.output else (Path.home() / ".hermes" / "research" / f"deep_{run_id}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    raw_path = Path(args.raw_output) if args.raw_output else out_path.with_suffix(".json")
    raw_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"STATUS=completed")
    print(f"OUTPUT={out_path}")
    print(f"RAW_OUTPUT={raw_path}")
    print("---BEGIN_OUTPUT_PREVIEW---")
    print(md[:4000])
    if len(md) > 4000:
        print("\n...[truncated preview; see OUTPUT file]...")
    print("---END_OUTPUT_PREVIEW---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
