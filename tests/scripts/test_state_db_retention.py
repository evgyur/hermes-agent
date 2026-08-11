import importlib.util
import json
import sqlite3
import stat
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "state_db_retention.py"
SPEC = importlib.util.spec_from_file_location("state_db_retention", SCRIPT)
assert SPEC and SPEC.loader
retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retention)


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA journal_mode=DELETE;
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            user_id TEXT,
            chat_id TEXT,
            thread_id TEXT,
            title TEXT,
            parent_session_id TEXT,
            started_at REAL,
            ended_at REAL,
            last_activity_at REAL,
            archived INTEGER DEFAULT 0,
            pinned INTEGER DEFAULT 0
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT,
            content TEXT,
            tool_calls TEXT,
            reasoning TEXT,
            reasoning_content TEXT,
            reasoning_details TEXT,
            codex_reasoning_items TEXT,
            codex_message_items TEXT,
            api_content TEXT,
            active INTEGER DEFAULT 1,
            compacted INTEGER DEFAULT 0
        );
        CREATE TABLE system_prompts (hash TEXT PRIMARY KEY, content TEXT);
        CREATE TABLE session_model_usage (
            session_id TEXT,
            model TEXT,
            billing_provider TEXT DEFAULT '',
            PRIMARY KEY(session_id, model, billing_provider)
        );
        CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE async_delegations (
            id TEXT PRIMARY KEY,
            parent_session_id TEXT
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            content, tool_calls, content='messages', content_rowid='id'
        );
        CREATE VIEW messages_fts_trigram_src AS
            SELECT id, role, content, tool_calls FROM messages WHERE role <> 'tool';
        CREATE VIRTUAL TABLE messages_fts_trigram USING fts5(
            content, tool_calls, content='messages_fts_trigram_src',
            content_rowid='id', tokenize='trigram'
        );
        CREATE TRIGGER messages_fts_trigram_insert AFTER INSERT ON messages
        WHEN new.role <> 'tool' BEGIN
          INSERT INTO messages_fts_trigram(rowid, content, tool_calls)
          VALUES(new.id, new.content, new.tool_calls);
        END;
        CREATE TRIGGER messages_fts_trigram_delete AFTER DELETE ON messages
        WHEN old.role <> 'tool' BEGIN
          INSERT INTO messages_fts_trigram(messages_fts_trigram, rowid, content, tool_calls)
          VALUES('delete', old.id, old.content, old.tool_calls);
        END;
        CREATE TRIGGER messages_fts_trigram_update AFTER UPDATE ON messages
        WHEN old.role <> 'tool' BEGIN
          INSERT INTO messages_fts_trigram(messages_fts_trigram, rowid, content, tool_calls)
          VALUES('delete', old.id, old.content, old.tool_calls);
          INSERT INTO messages_fts_trigram(rowid, content, tool_calls)
          VALUES(new.id, new.content, new.tool_calls);
        END;
        """
    )
    conn.executemany(
        "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("root", "telegram", "u", "c", "t", "production incident", None, 1, 2, 2, 0, 0),
            ("child", "telegram", "u", "c", "t", "old child", "root", 1, 2, 2, 0, 0),
            ("cron", "cron", None, None, None, "old cron", None, 1, 2, 2, 0, 0),
        ],
    )
    conn.executemany(
        "INSERT INTO messages(id, session_id, role, content, tool_calls, active, compacted) "
        "VALUES(?,?,?,?,?,?,?)",
        [
            (1, "root", "user", "Hermes production recovery", None, 1, 0),
            (2, "child", "assistant", "important answer", '[{"name":"x"}]', 0, 1),
            (3, "cron", "tool", "large technical output", None, 0, 1),
        ],
    )
    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO messages_fts_trigram(messages_fts_trigram) VALUES('rebuild')")
    conn.execute("INSERT INTO system_prompts VALUES('h', 'prompt')")
    conn.executemany(
        "INSERT INTO session_model_usage(session_id, model) VALUES(?, ?)",
        [("root", "m2"), ("root", "m1")],
    )
    conn.execute("INSERT INTO async_delegations VALUES('d', 'root')")
    conn.commit()
    conn.close()


def _manifest_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_manifest_is_conservative_and_promotes_complete_lineage(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    manifest = tmp_path / "manifest.jsonl"
    _make_db(db)

    result = retention.build_manifest(
        db,
        manifest,
        recent_days=0,
        archive_sha256="archive-hash",
    )

    rows = _manifest_rows(manifest)
    by_id = {row["session_id"]: row for row in rows[1:]}
    assert result["deletion_authority"] is False
    assert by_id["root"]["decision"] == "preserve_hot"
    assert by_id["child"]["decision"] == "preserve_hot"
    assert "hot_lineage_member" in by_id["child"]["reasons"]
    assert by_id["cron"]["decision"] == "preserve_cold"
    assert all(row["eligible_for_hot_deletion"] is False for row in rows[1:])
    assert rows[0]["source_sha256"] == "archive-hash"
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600


def test_lean_copy_removes_only_trigram_derivatives(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "lean.db"
    receipt = tmp_path / "receipt.json"
    _make_db(source)
    before = retention._file_sha256(source)

    result = retention.build_lean_copy(
        source,
        output,
        receipt=receipt,
        full_digest=True,
    )

    assert retention._file_sha256(source) == before
    assert result["quick_check"] == "ok"
    assert result["counts"] == {"sessions": 3, "messages": 3}
    assert result["conversation_rows_deleted"] == 0
    assert result["source_mutated"] is False
    assert result["canonical_digest"] == retention.canonical_digest(source)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    conn = sqlite3.connect(output)
    objects = conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'messages_fts_trigram%'"
    ).fetchall()
    base_hits = conn.execute(
        "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'Hermes'"
    ).fetchall()
    conn.close()
    assert objects == []
    assert base_hits == [(1,)]
    assert json.loads(receipt.read_text())["output_sha256"] == result["output_sha256"]


def test_lean_copy_rejects_output_receipt_alias(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    alias = tmp_path / "lean.db"
    _make_db(source)

    with pytest.raises(ValueError, match="distinct paths"):
        retention.build_lean_copy(
            source,
            alias,
            receipt=alias,
            full_digest=False,
        )
    assert not alias.exists()


def test_failed_verification_publishes_no_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "lean.db"
    receipt = tmp_path / "receipt.json"
    _make_db(source)
    conn = sqlite3.connect(source)
    conn.execute("DROP TABLE messages_fts")
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.OperationalError, match="messages_fts_docsize"):
        retention.build_lean_copy(
            source,
            output,
            receipt=receipt,
            full_digest=False,
        )

    assert not output.exists()
    assert not receipt.exists()
    assert not list(tmp_path.glob(".lean.db.candidate-*"))
    assert not list(tmp_path.glob(".receipt.json.candidate-*"))
    assert not list(tmp_path.glob("state-lean-work-*"))


def test_lean_copy_refuses_existing_outputs(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "lean.db"
    receipt = tmp_path / "receipt.json"
    _make_db(source)
    output.write_text("occupied")

    with pytest.raises(FileExistsError):
        retention.build_lean_copy(
            source,
            output,
            receipt=receipt,
            full_digest=False,
        )


def test_manifest_refuses_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "manifest.jsonl"
    _make_db(source)
    output.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        retention.build_manifest(
            source,
            output,
            recent_days=30,
            archive_sha256=None,
        )
    assert output.read_text(encoding="utf-8") == "keep"
