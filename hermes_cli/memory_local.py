"""CLI helpers for profile-scoped Hermes local memory."""

from __future__ import annotations

import json
import sys
from argparse import Namespace

from agent.memory.local_memory import HermesLocalMemory


def _print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def local_memory_command(args: Namespace) -> None:
    store = HermesLocalMemory()
    command = getattr(args, "local_memory_command", None)

    if command == "append":
        text_parts = getattr(args, "text", None) or []
        text = " ".join(text_parts).strip()
        if not text:
            text = sys.stdin.read().strip()
        if not text:
            print("Error: text is required (argument or stdin)", file=sys.stderr)
            raise SystemExit(2)
        note = store.append_event(
            text,
            source_class=getattr(args, "source_class", "operator_note"),
            origin_ref=getattr(args, "origin_ref", "cli"),
            confidence=getattr(args, "confidence", 0.5),
            policy_labels=getattr(args, "label", None) or [],
            ttl_seconds=getattr(args, "ttl_seconds", None),
        )
        _print_json(note.to_json())
        return

    if command == "compact":
        result = store.compact_hot(limit=getattr(args, "limit", None))
        _print_json(result.to_json())
        return

    if command == "rotate":
        result = store.rotate(
            hot_max_age_seconds=getattr(args, "hot_max_age_seconds", 86400),
            warm_max_age_seconds=getattr(args, "warm_max_age_seconds", 30 * 86400),
        )
        _print_json(result.to_json())
        return

    if command == "delete":
        result = store.delete(getattr(args, "note_id"), reason=getattr(args, "reason", "operator_delete"))
        _print_json(result.to_json())
        if not result.ok:
            raise SystemExit(1)
        return

    if command == "doctor":
        report = store.doctor()
        _print_json(report.to_json())
        raise SystemExit(0 if report.verdict.value == "green" else 1)

    raise SystemExit("unknown local memory command")
