import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.memory.local_memory import (
    HermesLocalMemory,
    LocalMemoryError,
    LocalMemoryLayer,
    LocalMemoryState,
    RedactionStatus,
)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "profiles" / "tester"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_append_creates_profile_scoped_hot_store(hermes_home):
    store = HermesLocalMemory()

    note = store.append_event(
        "remember this synthetic handoff",
        source_class="synthetic_test",
        origin_ref="test://append",
        confidence=0.8,
    )

    assert note.profile_id == "tester"
    assert note.layer is LocalMemoryLayer.HOT
    assert note.state is LocalMemoryState.ACCEPTED_HOT
    assert note.authority == "private_derived_working_memory"
    assert note.canonical_store == "mem0g-api"
    assert (hermes_home / "local-memory" / "layers" / "hot.jsonl").exists()
    assert (hermes_home / "local-memory" / "layers" / "warm.jsonl").exists()
    assert (hermes_home / "local-memory" / "layers" / "cold.jsonl").exists()


def test_compact_rotate_delete_state_transitions(hermes_home):
    store = HermesLocalMemory()
    note = store.append_event("hot note", source_class="synthetic_test", origin_ref="test://flow")

    compact = store.compact_hot()
    assert compact.ok is True
    assert compact.details["moved"] == 1
    warm_notes = store._read(LocalMemoryLayer.WARM)
    assert warm_notes[0].id == note.id
    assert warm_notes[0].state is LocalMemoryState.COMPACTED_WARM

    delete = store.delete(note.id, reason="test_cleanup")
    assert delete.ok is True
    tombstones = store._read(LocalMemoryLayer.TOMBSTONES)
    deleted = [n for n in tombstones if n.id == note.id and n.state is LocalMemoryState.DELETED]
    assert deleted
    assert deleted[0].payload_text is None


def test_rotate_expires_old_hot_note_to_tombstone(hermes_home):
    store = HermesLocalMemory()
    note = store.append_event("short lived", source_class="synthetic_test", origin_ref="test://ttl", ttl_seconds=-1)

    result = store.rotate()

    assert result.ok is True
    assert result.details["expired"] == 1
    assert all(n.id != note.id for n in store._read(LocalMemoryLayer.HOT))
    expired = [n for n in store._read(LocalMemoryLayer.TOMBSTONES) if n.id == note.id]
    assert expired[0].state is LocalMemoryState.EXPIRED
    assert expired[0].payload_text is None


def test_secret_and_raw_payloads_are_rejected_without_payload(hermes_home):
    store = HermesLocalMemory()

    secret = store.append_event("api_key = sk-test-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", source_class="synthetic_test", origin_ref="test://secret")
    raw = store.append_event("raw_telegram_dump message_id chat_id from_user text entities", source_class="synthetic_test", origin_ref="test://raw")

    assert secret.state is LocalMemoryState.REJECTED
    assert secret.redaction_status is RedactionStatus.REJECTED_SECRET
    assert secret.payload_text is None
    assert raw.state is LocalMemoryState.REJECTED
    assert raw.redaction_status is RedactionStatus.REJECTED_RAW_PAYLOAD
    assert raw.payload_text is None


def test_invalid_transition_is_typed_error(hermes_home):
    store = HermesLocalMemory()
    note = store.append_event("x", source_class="synthetic_test", origin_ref="test://bad-transition")

    with pytest.raises(LocalMemoryError):
        store._transition(note, LocalMemoryState.CURATED_COLD, LocalMemoryLayer.COLD)


def test_doctor_green_for_empty_and_synthetic_store(hermes_home):
    store = HermesLocalMemory()
    empty = store.doctor().to_json()
    assert empty["verdict"] == "green"
    assert empty["local_store"]["counts"]["hot"] == 0

    store.append_event("synthetic", source_class="synthetic_test", origin_ref="test://doctor")
    report = store.doctor().to_json()
    assert report["verdict"] == "green"
    assert report["local_store"]["counts"]["hot"] == 1
    assert report["promotion_adapter"]["canonical_store"] == "mem0g-api"


def test_cli_smoke_append_doctor_compact_rotate_delete(tmp_path):
    home = tmp_path / "profiles" / "cli"
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    py = sys.executable

    append = subprocess.run(
        [py, "-m", "hermes_cli.main", "memory", "local", "append", "cli synthetic note", "--source-class", "synthetic_test", "--origin-ref", "smoke://cli", "--confidence", "0.9"],
        cwd=Path(__file__).parents[2],
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    note = json.loads(append.stdout)
    assert note["profile_id"] == "cli"
    assert note["state"] == "accepted_hot"

    for args in (
        ["memory", "local", "doctor"],
        ["memory", "local", "compact"],
        ["memory", "local", "rotate"],
    ):
        completed = subprocess.run([py, "-m", "hermes_cli.main", *args], cwd=Path(__file__).parents[2], env=env, check=True, text=True, capture_output=True)
        assert json.loads(completed.stdout)

    delete = subprocess.run(
        [py, "-m", "hermes_cli.main", "memory", "local", "delete", note["id"], "--reason", "smoke_cleanup"],
        cwd=Path(__file__).parents[2],
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    assert json.loads(delete.stdout)["ok"] is True
