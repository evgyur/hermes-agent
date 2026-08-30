#!/usr/bin/env python3
"""Restricted SSH command gate for the Human20 meeting voice transport."""
from __future__ import annotations

import os
import re
import shlex

STATE_SCRIPT = "/home/chip/.local/lib/sigurd-meeting/meeting_state.py"
original = os.environ.get("SSH_ORIGINAL_COMMAND", "")
try:
    argv = shlex.split(original)
except ValueError:
    raise SystemExit(126)

if argv == ["pactl", "set-sink-volume", "agent_mic", "100%"]:
    os.environ["PULSE_SERVER"] = "unix:/run/user/1000/pulse/native"
    os.execvp("pactl", argv)
if argv == ["pactl", "set-source-volume", "agent_mic_source", "100%"]:
    os.environ["PULSE_SERVER"] = "unix:/run/user/1000/pulse/native"
    os.execvp("pactl", argv)
if len(argv) == 5 and argv[:2] == ["python3", STATE_SCRIPT]:
    provider, action, meeting_key = argv[2:]
    valid_key = re.fullmatch(r"\d+", meeting_key) if provider == "zoom" else re.fullmatch(r"[a-z0-9]{3}-[a-z0-9]{4}-[a-z0-9]{3}", meeting_key)
    if provider in {"zoom", "google"} and action in {"unmute", "mute", "ensure-recording", "status"} and valid_key:
        os.execvp("python3", argv)
if argv and argv[0] == "PULSE_SERVER=unix:/run/user/1000/pulse/native" and len(argv) >= 2:
    args = argv[2:] if argv[1] == "exec" else argv[1:]
    command = args[0] if args else ""
    if command == "parec" and args == ["parec", "--raw", "--device=meet_output.monitor", "--format=s16le", "--rate=24000", "--channels=1", "--latency-msec=40"]:
        os.environ["PULSE_SERVER"] = "unix:/run/user/1000/pulse/native"
        os.execvp(command, args)
    if command == "paplay" and args == ["paplay", "--raw", "--device=agent_mic", "--format=s16le", "--rate=24000", "--channels=1", "--client-name=sigurd-gpt-realtime"]:
        os.environ["PULSE_SERVER"] = "unix:/run/user/1000/pulse/native"
        os.execvp(command, args)
raise SystemExit(126)
