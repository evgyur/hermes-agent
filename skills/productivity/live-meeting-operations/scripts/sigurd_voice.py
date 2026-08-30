#!/usr/bin/env python3
"""Control one installed Human20/Sigurd meeting voice user service."""
from __future__ import annotations

import re
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] not in {"start", "stop", "restart", "status", "logs"}:
        print("usage: sigurd-voice {start|stop|restart|status|logs} {zoom|google} <meeting-id-or-meet-code>", file=sys.stderr)
        return 2
    action, provider, raw_key = sys.argv[1:]
    if provider == "zoom":
        key = re.sub(r"\D", "", raw_key)
        if not key:
            raise SystemExit("invalid Zoom meeting id")
        unit = f"sigurd-gpt-voice@{key}.service"
    elif provider == "google":
        key = raw_key.strip().lower()
        if re.fullmatch(r"[a-z0-9]{3}-[a-z0-9]{4}-[a-z0-9]{3}", key) is None:
            raise SystemExit("invalid Google Meet code")
        unit = f"sigurd-gpt-meet@{key}.service"
    else:
        raise SystemExit("provider must be zoom or google")

    if action == "logs":
        command = ["journalctl", "--user", "-u", unit, "-n", "120", "--no-pager"]
    elif action == "status":
        command = ["systemctl", "--user", "status", unit, "--no-pager"]
    else:
        command = ["systemctl", "--user", action, unit]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
