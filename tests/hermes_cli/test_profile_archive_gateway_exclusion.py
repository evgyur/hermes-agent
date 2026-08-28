"""Runtime exclusion for profiles carrying the explicit archive contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.profiles import profile_exists, profiles_to_serve


@pytest.fixture()
def profile_root(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    return root


def _seed_profile(profile_root: Path, name: str, *, archived: bool = False) -> Path:
    from hermes_state import SessionDB

    profile_home = profile_root / "profiles" / name
    profile_home.mkdir(parents=True)
    db = SessionDB(db_path=profile_home / "state.db")
    try:
        db.create_session(
            session_id=f"{name}-session",
            source="cli",
            model="test-model",
        )
    finally:
        db.close()
    if archived:
        (profile_home / "ARCHIVE_README.md").write_text(
            "Non-resumable archive. Query only through read-only session_search.\n",
            encoding="utf-8",
        )
    return profile_home


def test_marked_archive_is_not_runtime_served_but_remains_readonly_discoverable(
    profile_root,
):
    _seed_profile(profile_root, "worker")
    archive_home = _seed_profile(profile_root, "history-2026-08", archived=True)

    served = dict(profiles_to_serve(multiplex=True, profile_allowlist=None))

    assert set(served) == {"default", "worker"}
    assert profile_exists("history-2026-08") is True

    from tools.session_search_tool import _resolve_profile_db

    archive_db = _resolve_profile_db("history-2026-08")
    try:
        assert archive_db.read_only is True
        assert archive_db.get_session("history-2026-08-session") is not None
        assert archive_db.db_path == archive_home / "state.db"
    finally:
        archive_db.close()


def test_explicit_allowlist_cannot_reenable_marked_archive(profile_root):
    _seed_profile(profile_root, "worker")
    _seed_profile(profile_root, "history-2026-08", archived=True)

    served = dict(
        profiles_to_serve(
            multiplex=True,
            profile_allowlist=["worker", "history-2026-08"],
        )
    )

    assert set(served) == {"default", "worker"}


def test_unmarked_active_profile_is_not_hidden(profile_root):
    active_home = _seed_profile(profile_root, "history-active")

    served = dict(profiles_to_serve(multiplex=True, profile_allowlist=None))

    assert served["history-active"] == active_home


def test_unmarked_broken_profile_keeps_startup_failure_loud(profile_root):
    broken_home = profile_root / "profiles" / "broken-live"
    broken_home.mkdir(parents=True)
    (broken_home / "state.db").write_bytes(b"not a sqlite database")

    served = dict(profiles_to_serve(multiplex=True, profile_allowlist=None))
    assert served["broken-live"] == broken_home

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner, MultiplexConfigError

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        multiplex_profiles=True,
        multiplex_profile_allowlist=["broken-live"],
    )

    with pytest.raises(MultiplexConfigError, match="broken-live"):
        runner._restore_multiplex_delegation_lifecycle()
