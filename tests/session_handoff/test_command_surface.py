"""Integration smoke tests for session-handoff slash command helpers."""

from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.handoff_summary import HandoffSummaryCommand
from hermes_cli.recall import RecallCommand


def test_recall_command_registered():
    from hermes_cli.commands import resolve_command

    cmd = resolve_command("recall")
    assert cmd is not None
    assert cmd.name == "recall"
    assert cmd.category == "Session"


def test_handoff_command_dual_mode_description_stays_cli_only():
    from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS, resolve_command

    cmd = resolve_command("handoff")
    assert cmd is not None
    assert cmd.cli_only is True
    assert "handoff" not in GATEWAY_KNOWN_COMMANDS
    assert "topic" in cmd.args_hint
    assert "platform" in cmd.args_hint


def test_recall_command_smoke(populated_db: Path):
    output = RecallCommand(db_path=str(populated_db)).execute("guardian")

    assert "Guardian setup" in output
    assert "session(s)" in output
    assert "session://" not in output  # compact cards avoid raw evidence dumps


def test_handoff_summary_command_writes_artifacts(populated_db: Path, tmp_path: Path):
    cmd = HandoffSummaryCommand(db_path=str(populated_db), base_dir=tmp_path)
    output = cmd.execute("guardian")

    assert "wrote handoff" in output
    md_files = list(tmp_path.rglob("handoff.*.md"))
    json_files = list(tmp_path.rglob("handoff.*.json"))
    assert len(md_files) == 1
    assert len(json_files) == 1

    md = md_files[0].read_text(encoding="utf-8")
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert "## Context" in md
    assert "## Resume prompt" in md
    assert payload["schema"] == "session-handoff-artifact.v1"
    assert payload["topic"] == "guardian"
    assert payload["evidence_refs"]
    assert payload["resume_prompt"]
