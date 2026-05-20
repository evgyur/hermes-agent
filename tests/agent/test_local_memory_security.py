import json
from datetime import datetime, timezone

import pytest

from agent.memory.local_memory import (
    HermesLocalMemory,
    LocalMemoryLayer,
    LocalMemoryState,
    RedactionStatus,
    RETENTION_POLICY_SECONDS,
)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "profiles" / "security"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _parse_z(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_private_key_block_is_rejected_without_payload_or_artifact_leak(hermes_home):
    store = HermesLocalMemory()
    payload = "-----BEGIN PRIVATE KEY-----\n" + "A" * 80 + "\n-----END PRIVATE KEY-----"

    note = store.append_event(payload, source_class="synthetic_test", origin_ref="fixture://private-key")

    assert note.state is LocalMemoryState.REJECTED
    assert note.redaction_status is RedactionStatus.REJECTED_SECRET
    assert note.payload_text is None
    persisted = (hermes_home / "local-memory").read_text(encoding="utf-8") if (hermes_home / "local-memory").is_file() else ""
    for path in (hermes_home / "local-memory").rglob("*"):
        if path.is_file():
            persisted += path.read_text(encoding="utf-8")
    assert "BEGIN PRIVATE KEY" not in persisted
    assert "END PRIVATE KEY" not in persisted


def test_raw_telegram_json_dump_is_rejected_without_payload(hermes_home):
    store = HermesLocalMemory()
    raw_dump = json.dumps(
        {
            "update_id": 1,
            "message": {
                "message_id": 42,
                "chat": {"id": -100000, "type": "supergroup"},
                "from": {"id": 123456, "username": "private"},
                "text": "raw private message must not be stored",
            },
        }
    )

    note = store.append_event(raw_dump, source_class="synthetic_test", origin_ref="fixture://telegram")

    assert note.state is LocalMemoryState.REJECTED
    assert note.redaction_status is RedactionStatus.REJECTED_RAW_PAYLOAD
    assert note.payload_text is None


def test_oversized_tool_output_is_rejected_without_prefix_leak(hermes_home):
    store = HermesLocalMemory()
    payload = "TOOL_OUTPUT_PREFIX_SHOULD_NOT_PERSIST\n" + ("x" * (store.max_payload_bytes + 1))

    note = store.append_event(payload, source_class="tool_result_summary", origin_ref="fixture://oversize")

    assert note.state is LocalMemoryState.REJECTED
    assert note.redaction_status is RedactionStatus.REJECTED_OVERSIZE
    assert note.payload_text is None
    for path in (hermes_home / "local-memory").rglob("*"):
        if path.is_file():
            assert "TOOL_OUTPUT_PREFIX_SHOULD_NOT_PERSIST" not in path.read_text(encoding="utf-8")


def test_hot_ttl_is_capped_by_retention_policy(hermes_home):
    store = HermesLocalMemory()
    before = datetime.now(timezone.utc)

    note = store.append_event(
        "synthetic long ttl",
        source_class="synthetic_test",
        origin_ref="fixture://ttl-cap",
        ttl_seconds=RETENTION_POLICY_SECONDS[LocalMemoryLayer.HOT] * 10,
    )

    expires_at = _parse_z(note.expires_at)
    ttl_seconds = (expires_at - before).total_seconds()
    assert ttl_seconds <= RETENTION_POLICY_SECONDS[LocalMemoryLayer.HOT] + 2


def test_rotate_enforces_cold_retention_policy(hermes_home):
    store = HermesLocalMemory()
    note = store.append_event("cold retention", source_class="synthetic_test", origin_ref="fixture://cold")
    cold = store._transition(note, LocalMemoryState.COMPACTED_WARM, LocalMemoryLayer.COLD, payload_text="cold retention")
    cold = store._transition(cold, LocalMemoryState.CURATED_COLD, LocalMemoryLayer.COLD, payload_text="cold retention")
    store._append(LocalMemoryLayer.COLD, cold)

    result = store.rotate(cold_max_age_seconds=0)

    assert result.ok is True
    assert result.details["expired"] == 1
    assert all(n.id != note.id for n in store._read(LocalMemoryLayer.COLD))
    tombstone = [n for n in store._read(LocalMemoryLayer.TOMBSTONES) if n.id == note.id][-1]
    assert tombstone.state is LocalMemoryState.EXPIRED
    assert tombstone.payload_text is None


def test_doctor_reports_blocked_write_counts_and_retention_policy(hermes_home):
    store = HermesLocalMemory()
    store.append_event("api_key = sk-test-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", source_class="synthetic_test", origin_ref="fixture://secret")
    store.append_event("raw private chat dump: full transcript", source_class="synthetic_test", origin_ref="fixture://raw")
    store.append_event("x" * (store.max_payload_bytes + 1), source_class="tool_result_summary", origin_ref="fixture://oversize")

    report = store.doctor().to_json()

    assert report["verdict"] == "green"
    assert report["secret_guard"]["blocked_write_counts"] == {
        "OVERSIZE_PAYLOAD": 1,
        "RAW_PAYLOAD_REJECTED": 1,
        "SECRET_DETECTED": 1,
    }
    assert report["rotation"]["retention_seconds"] == {
        "hot": RETENTION_POLICY_SECONDS[LocalMemoryLayer.HOT],
        "warm": RETENTION_POLICY_SECONDS[LocalMemoryLayer.WARM],
        "cold": RETENTION_POLICY_SECONDS[LocalMemoryLayer.COLD],
    }
