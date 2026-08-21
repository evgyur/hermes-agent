from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.topic_handoffs import build_topic_handoff_note, load_topic_handoff


def _write(root: Path, *, chat="-1003971448755", thread="45009", **updates):
    data = {
        "schema_version": 1,
        "chat_id": chat,
        "thread_id": thread,
        "updated_at": "2026-08-21T12:00:00Z",
        "current_state": "Tests pass.",
        "decisions": ["Keep route scoped."],
        "done": ["Implemented reader."],
        "next": ["Run canary."],
        "blockers": [],
        "repo_state": "main@abc123",
    }
    data.update(updates)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    data["integrity_sha256"] = hashlib.sha256(payload).hexdigest()
    path = root / "topic-context/topics" / chat / thread / "handoff.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    (root / "topic-context/enabled").write_text("1\n", encoding="utf-8")
    return path


@pytest.fixture
def profile_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import hermes_cli.profiles

    monkeypatch.setattr(hermes_cli.profiles, "get_profile_dir", lambda profile: tmp_path)
    return tmp_path


def test_exact_topic_handoff_is_injected(profile_root: Path):
    _write(profile_root)
    source = SimpleNamespace(
        profile="hermesdev",
        platform=SimpleNamespace(value="telegram"),
        chat_id="-1003971448755",
        thread_id="45009",
    )
    note = build_topic_handoff_note(source)
    assert note is not None
    assert "PRIVATE TOPIC HANDOFF" in note
    assert "Tests pass." in note
    assert "Run canary." in note


def test_other_topic_and_other_chat_are_isolated(profile_root: Path):
    _write(profile_root)
    assert load_topic_handoff(
        profile="hermesdev", platform="telegram", chat_id="-1003971448755", thread_id="1"
    ) is None
    assert load_topic_handoff(
        profile="hermesdev", platform="telegram", chat_id="-1000000000000", thread_id="45009"
    ) is None


def test_tampered_handoff_fails_closed(profile_root: Path):
    path = _write(profile_root)
    data = json.loads(path.read_text())
    data["current_state"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_topic_handoff(
        profile="hermesdev", platform="telegram", chat_id="-1003971448755", thread_id="45009"
    ) is None


def test_disabled_profile_fails_closed(profile_root: Path):
    _write(profile_root)
    (profile_root / "topic-context/enabled").unlink()
    assert load_topic_handoff(
        profile="hermesdev", platform="telegram", chat_id="-1003971448755", thread_id="45009"
    ) is None


def test_rejects_path_traversal_ids(profile_root: Path):
    _write(profile_root)
    assert load_topic_handoff(
        profile="hermesdev", platform="telegram", chat_id="../../etc", thread_id="45009"
    ) is None
