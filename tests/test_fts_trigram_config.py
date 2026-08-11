import sqlite3

from hermes_state import SessionDB


def _objects(path, names):
    conn = sqlite3.connect(path)
    try:
        placeholders = ",".join("?" for _ in names)
        return {
            row[0]
            for row in conn.execute(
                f"SELECT name FROM sqlite_master WHERE name IN ({placeholders})",
                tuple(names),
            )
        }
    finally:
        conn.close()


def test_trigram_config_off_skips_new_derivative_but_keeps_base(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_TRIGRAM_FTS", "0")
    path = tmp_path / "state.db"

    db = SessionDB(db_path=path)
    try:
        assert db._fts_enabled is True
        assert db._trigram_available is False
    finally:
        db.close()

    objects = _objects(
        path,
        {
            "messages_fts",
            "messages_fts_insert",
            "messages_fts_trigram",
            "messages_fts_trigram_insert",
        },
    )
    assert "messages_fts" in objects
    assert "messages_fts_insert" in objects
    assert "messages_fts_trigram" not in objects
    assert "messages_fts_trigram_insert" not in objects


def test_disabling_trigram_stops_maintenance_without_dropping_pages(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setenv("HERMES_TRIGRAM_FTS", "1")
    db = SessionDB(db_path=path)
    try:
        db.create_session("s1", source="cli")
        db.append_message("s1", "user", "first searchable text")
    finally:
        db.close()

    conn = sqlite3.connect(path)
    before = conn.execute("SELECT COUNT(*) FROM messages_fts_trigram_docsize").fetchone()[0]
    conn.close()
    assert before == 1

    monkeypatch.setenv("HERMES_TRIGRAM_FTS", "0")
    db = SessionDB(db_path=path)
    try:
        assert db._trigram_available is False
        db.append_message("s1", "user", "second searchable text 项目")
        hits = db.search_messages("项目", limit=10)
        assert any(hit["session_id"] == "s1" for hit in hits)
    finally:
        db.close()

    objects = _objects(
        path,
        {"messages_fts_trigram", "messages_fts_trigram_insert"},
    )
    assert "messages_fts_trigram" in objects
    assert "messages_fts_trigram_insert" not in objects
    conn = sqlite3.connect(path)
    try:
        after = conn.execute(
            "SELECT COUNT(*) FROM messages_fts_trigram_docsize"
        ).fetchone()[0]
        base = conn.execute("SELECT COUNT(*) FROM messages_fts_docsize").fetchone()[0]
    finally:
        conn.close()
    assert after == before
    assert base == 2


def test_read_only_open_respects_disabled_trigram(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setenv("HERMES_TRIGRAM_FTS", "1")
    db = SessionDB(db_path=path)
    db.close()

    monkeypatch.setenv("HERMES_TRIGRAM_FTS", "0")
    db = SessionDB(db_path=path, read_only=True)
    try:
        assert db._fts_enabled is True
        assert db._trigram_available is False
    finally:
        db.close()
