"""Tests for SessionTraceReader."""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from hermes_cli.session_handoff.trace_reader import SessionTraceReader


class TestSessionTraceReader:
    """Tests for SessionTraceReader.query() with various filters."""

    def test_empty_query_returns_all_sessions(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query()
        assert len(results) >= 3
        ids = {r["id"] for r in results}
        assert "ses-1" in ids
        assert "ses-2" in ids
        assert "ses-3" in ids

    def test_source_filter(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(source="telegram")
        assert all(r["source"] == "telegram" for r in results)
        assert all(r["id"] != "ses-2" for r in results)  # cli only session

    def test_model_filter(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(model="gpt-5.5")
        assert all("gpt-5.5" in (r.get("model") or "") for r in results)
        # Should not include claude session
        assert not any(r["id"] == "ses-2" for r in results)

    def test_tool_filter(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(tool="terminal")
        assert all(r["id"] in ("ses-1", "ses-2") for r in results)

    def test_days_back_7d(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(days_back=7)
        # All fixtures are within 7 days
        assert len(results) >= 3
        # ses-today must appear
        assert any(r["id"] == "ses-today" for r in results)

    def test_days_back_0_today_only(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(days_back=0)
        # Should only include sessions that started today
        now = time.time()
        for r in results:
            started = r.get("started_at")
            if isinstance(started, str):
                from datetime import datetime, timezone
                started = datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
            assert started is not None and started >= now - 86400, f"Session {r['id']} should be within today"

    def test_status_active(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(status="active")
        assert all(r.get("ended_at") is None for r in results)
        assert any(r["id"] == "ses-2" for r in results)

    def test_status_completed(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(status="completed")
        assert all(r.get("ended_at") is not None for r in results)
        assert any(r["id"] == "ses-1" for r in results)

    def test_status_error(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(status="error")
        assert any(r["id"] == "ses-3" for r in results)

    def test_combined_filters(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(
            source="telegram",
            model="gpt-5.5",
            status="completed",
        )
        assert all(r["source"] == "telegram" for r in results)
        assert all("gpt-5.5" in (r.get("model") or "") for r in results)

    def test_fts_free_text(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        # FTS5 with unicode61 tokenizer: "us" is a separate token (not "setup")
        results = reader.query(free_text="us")
        assert any(r["id"] == "ses-1" for r in results)

    def test_fts_free_text_no_match(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(free_text="xyzzy_nonexistent_query_term_12345")
        assert len(results) == 0

    def test_limit_capped(self, temp_db: Path):
        """Limit should be capped at MAX_SESSIONS=50 even if higher is requested."""
        from hermes_cli.session_handoff.trace_reader import _MAX_SESSIONS, SessionTraceReader
        conn = __import__("sqlite3").connect(str(temp_db))
        for i in range(100):
            conn.execute(
                "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                (f"bulk-ses-{i}", "cli", 1_700_000_000 + i),
            )
        conn.commit()
        conn.close()
        reader = SessionTraceReader(db_path=temp_db)
        results = reader.query(limit=200)
        assert len(results) <= _MAX_SESSIONS


class TestSessionTraceReaderGetSession:
    def test_get_session(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        ses = reader.get_session("ses-1")
        assert ses is not None
        assert ses["id"] == "ses-1"
        assert ses["title"] == "Guardian setup"

    def test_get_session_not_found(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        ses = reader.get_session("nonexistent-session")
        assert ses is None


class TestSessionTraceReaderGetMessages:
    def test_get_messages(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        msgs = reader.get_messages("ses-1")
        assert len(msgs) >= 3
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"].startswith("Let us")

    def test_get_messages_truncated(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        # Content should be truncated at 200 chars
        msgs = reader.get_messages("ses-1", limit=10)
        for m in msgs:
            if m.get("content"):
                assert len(m["content"]) <= 200

    def test_get_messages_empty_session(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        msgs = reader.get_messages("ses-today")  # has no messages
        assert msgs == []


class TestSessionTraceReaderGetToolNames:
    def test_get_tool_names(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        tools = reader.get_tool_names("ses-1")
        assert "terminal" in tools


class TestInvalidFilters:
    """Invalid filter values should not raise — reader degrades gracefully."""

    def test_invalid_source_no_raises(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(source="nonexistent-source-xyz")
        assert isinstance(results, list)

    def test_invalid_model_no_raises(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(model="nonexistent-model-xyz")
        assert isinstance(results, list)

    def test_invalid_status_no_raises(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(status="nonexistent-status-xyz")
        assert isinstance(results, list)  # Should return empty or all, not raise

    def test_invalid_days_back_no_raises(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(days_back=-999)
        assert isinstance(results, list)  # Graceful degradation

    def test_invalid_limit_no_raises(self, populated_db: Path):
        reader = SessionTraceReader(db_path=populated_db)
        results = reader.query(limit=-5)
        assert isinstance(results, list)