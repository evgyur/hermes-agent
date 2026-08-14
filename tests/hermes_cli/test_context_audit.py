"""Focused tests for the strict local context attribution audit."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from hermes_cli.context_audit import (
    SideEffectBlocked,
    _safe_name,
    _strict_local_guard,
    run_local_audit,
)


def _seed_skill(home: Path, name: str = "demo-skill") -> Path:
    root = home / "skills" / "demo" / name
    root.mkdir(parents=True)
    skill = root / "SKILL.md"
    skill.write_text(
        f"---\nname: {name}\ndescription: Use when a demo audit runs.\n---\n# Demo\nbody\n",
        encoding="utf-8",
    )
    return skill


def _clear_prompt_cache() -> None:
    from agent import prompt_builder

    with prompt_builder._SKILLS_PROMPT_CACHE_LOCK:
        prompt_builder._SKILLS_PROMPT_CACHE.clear()


def _seed_history_db(home: Path) -> None:
    connection = sqlite3.connect(home / "state.db")
    connection.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            started_at TEXT,
            last_activity_at TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp TEXT,
            content TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO sessions VALUES (?, ?, ?)",
        ("private-session-id", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:10+00:00"),
    )
    write_call = json.dumps(
        [{"id": "write-1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]
    )
    skill_call = json.dumps(
        [{
            "id": "skill-1",
            "type": "function",
            "function": {"name": "skill_view", "arguments": json.dumps({"name": "demo-skill"})},
        }]
    )
    skill_result = json.dumps(
        {"success": True, "content": "# Demo\nbody\n", "content_returned": True}
    )
    rows = [
        (1, "user", None, None, None, "2026-01-01T00:00:00+00:00", "private user text"),
        (2, "assistant", None, write_call, None, "2026-01-01T00:00:01+00:00", ""),
        (3, "tool", "write-1", None, "write_file", "2026-01-01T00:00:02+00:00", "ok"),
        (4, "assistant", None, skill_call, None, "2026-01-01T00:00:03+00:00", ""),
        (5, "tool", "skill-1", None, "skill_view", "2026-01-01T00:00:04+00:00", skill_result),
        (6, "assistant", None, None, None, "2026-01-01T00:00:10+00:00", "private final text"),
    ]
    connection.executemany(
        "INSERT INTO messages(id, session_id, role, tool_call_id, tool_calls, tool_name, timestamp, content) "
        "VALUES (?, 'private-session-id', ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    connection.close()


def test_strict_guard_blocks_all_side_effect_classes(tmp_path):
    target = tmp_path / "blocked.txt"
    with _strict_local_guard() as attempts:
        with pytest.raises(SideEffectBlocked):
            target.write_text("blocked", encoding="utf-8")
        with pytest.raises(SideEffectBlocked):
            socket.getaddrinfo("example.com", 443)
        with pytest.raises(SideEffectBlocked):
            threading.Thread(target=lambda: None).start()
    assert attempts["filesystem_write"] == 1
    assert attempts["network"] == 1
    assert attempts["thread_starts"] == 1
    assert not target.exists()


def test_local_audit_is_read_only_and_does_not_create_snapshot(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    _seed_skill(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    _clear_prompt_cache()

    before = {str(path.relative_to(home)): path.stat().st_mtime_ns for path in home.rglob("*")}
    receipt, code = run_local_audit(home, include_history=False)
    after = {str(path.relative_to(home)): path.stat().st_mtime_ns for path in home.rglob("*")}

    assert code == 0
    assert receipt["status"] == "ok"
    assert receipt["guards"]["network_attempts"] == 0
    assert receipt["guards"]["filesystem_write_attempts"] == 0
    assert receipt["guards"]["thread_start_attempts"] == 0
    assert receipt["guards"]["profile_manifest_unchanged"] is True
    assert before == after
    assert not (home / "skills" / ".skills_prompt_snapshot.json").exists()


def test_local_audit_attributes_structural_history_without_private_text(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    skill = _seed_skill(home)
    _seed_history_db(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    _clear_prompt_cache()

    receipt, code = run_local_audit(home, session_limit=10, task_limit=5)
    rendered = json.dumps(receipt, ensure_ascii=False)

    assert code == 0
    assert len(receipt["history"]["tasks"]) == 1
    assert len(receipt["history"]["loads"]) == 1
    load = receipt["history"]["loads"][0]
    assert load["requested_skill"] == "demo-skill"
    assert load["canonical_skill_path"] == "profile:demo/demo-skill/SKILL.md"
    assert load["body_bytes"] == skill.stat().st_size
    assert load["trigger_reason"] == "unknown"
    assert load["content_status"] == "full"
    assert "private-session-id" not in rendered
    assert "private user text" not in rendered
    assert "private final text" not in rendered
    assert str(home) not in rendered


def test_duplicate_frontmatter_names_fail_closed(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    _seed_skill(home, "duplicate-name")
    second = home / "skills" / "other" / "second"
    second.mkdir(parents=True)
    (second / "SKILL.md").write_text(
        "---\nname: duplicate-name\ndescription: second\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    _clear_prompt_cache()

    receipt, code = run_local_audit(home, include_history=False)

    assert code == 0
    duplicate = next(
        row for row in receipt["catalog"]["duplicate_names"]
        if row["name"] == "duplicate-name"
    )
    assert duplicate["candidate_count"] == 2
    assert duplicate["index_occurrences"] == 1
    assert all(not candidate["canonical_skill_path"].startswith("/") for candidate in duplicate["candidates"])


def test_early_cli_dispatch_stays_read_only(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    _seed_skill(home)
    repo = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["PYTHONPATH"] = str(repo)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "context",
            "audit",
            "--local",
            "--json",
            "--no-history",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "ok"
    assert receipt["guards"]["profile_manifest_unchanged"] is True
    assert not (home / "logs").exists()
    assert not (home / "skills" / ".skills_prompt_snapshot.json").exists()


def test_prompt_builder_read_only_flag_preserves_rendered_index(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    _seed_skill(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    _clear_prompt_cache()

    from agent.prompt_builder import build_skills_system_prompt

    rendered = build_skills_system_prompt(persist_snapshot=False)

    assert "demo-skill" in rendered
    assert not (home / "skills" / ".skills_prompt_snapshot.json").exists()


def test_safe_name_preserves_spaces_but_separates_secret_redactions():
    assert _safe_name("Git PR Workflow") == "Git PR Workflow"
    assert _safe_name("durable-secret-redaction") == "durable-secret-redaction"
    assert _safe_name("oauth-token-lifecycle-operations") == "oauth-token-lifecycle-operations"
    assert _safe_name("task-assignment-provenance") == "task-assignment-provenance"
    first = _safe_name("api_key=sk-aaaaaaaaaaaa")
    second = _safe_name("Bearer abcdefghijklmnop")
    assert first.startswith("[REDACTED:")
    assert second.startswith("[REDACTED:")
    assert first != second
    assert "aaaaaaaa" not in first


def test_v2_skill_snapshot_is_invalidated_after_catalog_semantics_change(tmp_path, monkeypatch):
    from agent import prompt_builder as pb

    skills = tmp_path / "skills"
    skills.mkdir()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "version": 2,
                "manifest": pb._build_skills_manifest(skills),
                "skills": [],
                "category_descriptions": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pb, "_skills_prompt_snapshot_path", lambda: snapshot)

    assert pb._load_skills_snapshot(skills) is None
