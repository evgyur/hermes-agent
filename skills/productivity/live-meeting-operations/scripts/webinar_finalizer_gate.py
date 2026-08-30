#!/usr/bin/env python3
"""No-agent retry gate for webinar finalization.

Run this script from a recurring Hermes no-agent cron or systemd timer. Deferred
or still-processing states stay silent and are retried; completion or a real
recording blocker wakes the agent with bounded context.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


def _last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {"status": "PIPELINE_ERROR", "error": "pipeline returned no JSON status"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Mode-0600 JSON file containing pipeline_argv")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--pipeline-script", default=str(Path(__file__).with_name("webinar_pipeline.py")))
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_file() or config_path.is_symlink():
        print(json.dumps({"wakeAgent": True, "context": {"status": "PIPELINE_ERROR", "error": "finalizer config missing or unsafe"}}))
        return 0
    stat_result = config_path.stat()
    if stat_result.st_uid != os.geteuid() or stat_result.st_mode & 0o077:
        print(json.dumps({"wakeAgent": True, "context": "Webinar finalizer config must be owner-only mode 0600."}))
        return 0
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pipeline_argv = config.get("pipeline_argv") if isinstance(config, dict) else None
    if not isinstance(pipeline_argv, list) or not all(isinstance(item, str) for item in pipeline_argv):
        print(json.dumps({"wakeAgent": True, "context": {"status": "PIPELINE_ERROR", "error": "invalid pipeline_argv"}}))
        return 0

    result = subprocess.run(
        [args.python, args.pipeline_script, *pipeline_argv],
        capture_output=True,
        text=True,
        timeout=int(config.get("timeout_seconds", 7200)),
    )
    payload = _last_json(result.stdout)
    status = str(payload.get("status") or "PIPELINE_ERROR")
    if status in {"FINALIZATION_DEFERRED", "ARTIFACTS_PROCESSING", "RECORDING_VERIFIED"}:
        print(json.dumps({"wakeAgent": False, "context": {"status": status}}))
        return 0
    if status == "PACKAGE_COMPLETE":
        print(json.dumps({"wakeAgent": True, "context": payload}))
        return 0
    bounded = {"status": status, "error": str(payload.get("error") or payload.get("blocker") or "unknown")[:500]}
    print(json.dumps({"wakeAgent": True, "context": bounded}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
