"""Todo/continuity compaction snapshots stay non-user-owned."""

from pathlib import Path

from agent.conversation_compression import _strip_stale_todo_snapshot
from tools.todo_tool import TODO_INJECTION_HEADER


def test_todo_snapshot_is_system_owned():
    source = Path("agent/conversation_compression.py").read_text(encoding="utf-8")

    assert '"role": "user", "content": todo_snapshot' not in source
    assert '"role": "system",\n                    "content": todo_snapshot' in source


def test_legacy_user_snapshot_strip_preserves_human_prefix():
    standalone = TODO_INJECTION_HEADER + "\n- [ ] old. stale"
    merged = "keep this human request\n\n" + standalone

    assert _strip_stale_todo_snapshot(standalone) == ""
    assert _strip_stale_todo_snapshot(merged) == "keep this human request"
