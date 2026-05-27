"""Pytest fixtures for session_handoff tests."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def temp_db() -> Generator[Path, None, None]:
    """Create a temp SQLite DB with the sessions/messages schema."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "state.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL DEFAULT 'cli',
                user_id TEXT,
                model TEXT,
                title TEXT,
                started_at REAL NOT NULL,
                ended_at REAL,
                end_reason TEXT,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0,
                handoff_state TEXT,
                handoff_platform TEXT,
                handoff_error TEXT
            );

            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL,
                content TEXT,
                tool_name TEXT,
                tool_calls TEXT,
                timestamp REAL NOT NULL,
                finish_reason TEXT
            );

            CREATE VIRTUAL TABLE messages_fts USING fts5(
                content, tool_name, content=messages, content_rowid=id
            );

            CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content, tool_name)
                VALUES (new.id, new.content, new.tool_name);
            END;
        """)
        conn.close()
        yield db_path


@pytest.fixture
def populated_db(temp_db: Path) -> Path:
    """Populate temp_db with two sessions + messages for filter tests."""
    import time
    now = time.time()
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "INSERT INTO sessions (id, source, model, title, started_at, ended_at, end_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ses-1", "telegram", "gpt-5.5", "Guardian setup", now - 86400, now - 86000, "completed"),
    )
    conn.execute(
        "INSERT INTO sessions (id, source, model, title, started_at, ended_at, end_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ses-2", "cli", "claude-3-5-sonnet", "Debug auth bug", now - 3600, None, None),
    )
    conn.execute(
        "INSERT INTO sessions (id, source, model, title, started_at, ended_at, end_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ses-3", "telegram", "gpt-5.5", "Payment research", now - 100000, now - 90000, "error"),
    )
    conn.execute(
        "INSERT INTO sessions (id, source, model, title, started_at) VALUES (?, ?, ?, ?, ?)",
        ("ses-today", "telegram", "gpt-5.5", "Today's session", now),
    )

    conn.execute(
        "INSERT INTO messages (session_id, role, content, tool_name, timestamp) VALUES (?, ?, ?, ?, ?)",
        ("ses-1", "user", "Let us set up the Guardian", None, now - 86400),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, tool_name, timestamp) VALUES (?, ?, ?, ?, ?)",
        ("ses-1", "assistant", "I'll configure the Guardian settings", None, now - 86300),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, tool_name, timestamp) VALUES (?, ?, ?, ?, ?)",
        ("ses-1", "assistant", "", "terminal", now - 86200),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, tool_name, timestamp) VALUES (?, ?, ?, ?, ?)",
        ("ses-2", "user", "The auth flow broke", None, now - 3600),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, tool_name, timestamp) VALUES (?, ?, ?, ?, ?)",
        ("ses-2", "assistant", "", "terminal", now - 3500),
    )

    # Index FTS
    conn.execute(
        "INSERT INTO messages_fts(rowid, content, tool_name) "
        "SELECT id, content, tool_name FROM messages"
    )

    conn.commit()
    conn.close()
    return temp_db