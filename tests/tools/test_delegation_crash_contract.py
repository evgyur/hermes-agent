from __future__ import annotations

import json
import queue

import pytest

from tools import async_delegation as ad
from tools.process_registry import process_registry


@pytest.fixture(autouse=True)
def isolated_delegation_store(tmp_path, monkeypatch):
    from tools import parent_task_barrier as barrier

    monkeypatch.setattr(ad, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(barrier, "get_hermes_home", lambda: tmp_path)
    ad._reset_for_tests()
    while not process_registry.completion_queue.empty():
        process_registry.completion_queue.get_nowait()
    yield
    ad._reset_for_tests()


def _insert_restart_row(
    delegation_id: str,
    *,
    state: str = "restart_pending",
    count: int = 0,
    budget: int = 3,
    task_json: str = "{}",
    child_sessions_json: str = "[]",
) -> None:
    with ad._transaction() as conn:
        conn.execute(
            """INSERT INTO async_delegations(
                   delegation_id, origin_session, state, dispatched_at,
                   updated_at, heartbeat_at, delivery_state, task_json,
                   restart_policy, restart_nonce, restart_count, restart_budget,
                       child_session_ids_json, child_capability_names_json)
               VALUES (?, 'telegram:origin', ?, 1, 1, 1, 'pending', ?,
                       'gateway_owned_v1', 'nonce', ?, ?, ?, '[]')""",
            (delegation_id, state, task_json, count, budget, child_sessions_json),
        )


def _claim(delegation_id: str):
    return ad.claim_restartable_delegation(
        delegation_id,
        owner_pid=123,
        owner_started_at=456,
        expected_session_key="telegram:origin",
        restart_nonce="nonce",
    )


def test_failed_final_retry_is_released_for_exhaustion_finalizer():
    _insert_restart_row("last-attempt", budget=1)
    assert _claim("last-attempt") is not None
    assert ad.release_restart_claim("last-attempt", "dead_owner") is False
    assert ad.finalize_exhausted_restarts() == 1
    assert ad.get_durable_delegation("last-attempt")["state"] == "error"


@pytest.mark.parametrize("state", ["running", "stalling", "restarting"])
def test_dead_restartable_owner_returns_to_pending(state, monkeypatch):
    _insert_restart_row("dead-owner", state=state)
    with ad._transaction() as conn:
        conn.execute(
            "UPDATE async_delegations SET owner_pid=999999, owner_started_at=1 "
            "WHERE delegation_id='dead-owner'"
        )
    from gateway import status as gateway_status

    monkeypatch.setattr(gateway_status, "_pid_exists", lambda _pid: False)
    assert ad.recover_abandoned_delegations() == 1
    row = ad.get_durable_delegation("dead-owner")
    assert row["state"] == "restart_pending"
    with ad._transaction() as conn:
        nonce = conn.execute(
            "SELECT restart_nonce FROM async_delegations "
            "WHERE delegation_id='dead-owner'"
        ).fetchone()[0]
    assert nonce


def test_claim_preserves_terminal_child_and_bounds_corrupt_json():
    _insert_restart_row("typed-contract", task_json='"wrong-shape"')
    with ad._transaction() as conn:
        conn.execute(
            """INSERT INTO async_delegation_children(
                   delegation_id, child_index, child_session_id, state,
                   result_json, updated_at)
               VALUES ('typed-contract', 0, 'child', 'failed', ?, 1)""",
            (json.dumps({"status": "failed", "summary": "keep"}),),
        )
    claim = _claim("typed-contract")
    assert claim["task"] == {}
    assert claim["children"][0]["state"] == "failed"
    assert claim["children"][0]["result"]["summary"] == "keep"


def test_pruning_only_deletes_acknowledged_history(monkeypatch):
    monkeypatch.setattr(ad, "_MAX_RETAINED_COMPLETED", 1)
    for index in range(2):
        delegation_id = f"pending-{index}"
        assert ad._persist_dispatch(
            {"delegation_id": delegation_id, "dispatched_at": index + 1}
        )
        assert ad._persist_completion(
            {"delegation_id": delegation_id, "status": "completed"},
            {"status": "completed", "summary": delegation_id},
        )
    ad._prune_durable_records()
    restored: queue.Queue = queue.Queue()
    assert ad.restore_undelivered_completions(restored) == 2


def test_cold_restore_quarantines_poison_rows_without_stranding_good_work(
    monkeypatch,
):
    monkeypatch.setattr(ad, "_MAX_DURABLE_JSON_BYTES", 256)
    events = {
        "malformed": "{broken",
        "oversize": json.dumps(
            {
                "type": "async_delegation",
                "delegation_id": "oversize",
                "summary": "x" * 512,
            }
        ),
        "good": json.dumps(
            {
                "type": "async_delegation",
                "delegation_id": "good",
                "status": "completed",
            }
        ),
    }
    with ad._transaction() as conn:
        for index, (delegation_id, payload) in enumerate(events.items()):
            conn.execute(
                """INSERT INTO async_delegations(
                       delegation_id, origin_session, state, dispatched_at,
                       completed_at, updated_at, event_json, delivery_state)
                   VALUES (?, 'telegram:origin', 'completed', 1, ?, ?, ?,
                           'pending')""",
                (delegation_id, index + 1, index + 1, payload),
            )

    restored: queue.Queue = queue.Queue()
    assert ad.restore_undelivered_completions(restored) == 1
    assert restored.get_nowait()["delegation_id"] == "good"
    with ad._transaction() as conn:
        quarantined = conn.execute(
            """SELECT delegation_id, quarantine_reason
               FROM async_delegations WHERE delivery_state='quarantined'
               ORDER BY delegation_id"""
        ).fetchall()
    assert quarantined == [
        ("malformed", "invalid_json"),
        ("oversize", "oversize"),
    ]


def test_durable_admission_is_lossless_and_delivery_frees_capacity(monkeypatch):
    monkeypatch.setattr(ad, "_MAX_DURABLE_PENDING", 1)
    first = {"delegation_id": "capacity-first", "dispatched_at": 1}
    second = {"delegation_id": "capacity-second", "dispatched_at": 2}
    assert ad._persist_dispatch(first)
    ad._reset_for_tests()  # capacity lives in SQLite, not process memory
    assert ad._persist_dispatch(second) is False
    assert ad.get_durable_delegation("capacity-first") is not None
    assert ad._persist_completion(
        {
            "type": "async_delegation",
            "delegation_id": "capacity-first",
            "status": "completed",
        },
        {"status": "completed", "summary": "done"},
    )
    assert ad.mark_completion_delivered("capacity-first")
    assert ad._persist_dispatch(second)


def test_oversized_dispatch_contract_never_enters_hot_state():
    record = {
        "delegation_id": "oversized-contract",
        "dispatched_at": 1,
        "goal": "x" * (ad._MAX_DURABLE_JSON_BYTES + 1),
    }
    assert ad._persist_dispatch(record) is False
    assert ad.get_durable_delegation("oversized-contract") is None


def test_oversized_completion_is_spilled_and_hot_event_stays_bounded(monkeypatch):
    monkeypatch.setattr(ad, "_MAX_DURABLE_JSON_BYTES", 512)
    assert ad._persist_dispatch(
        {"delegation_id": "large-result", "dispatched_at": 1}
    )
    event = {
        "type": "async_delegation",
        "delegation_id": "large-result",
        "status": "completed",
    }
    result = {"status": "completed", "summary": "x" * 2048}
    assert ad._persist_completion(event, result)
    with ad._transaction() as conn:
        event_json, result_json = conn.execute(
            "SELECT event_json, result_json FROM async_delegations "
            "WHERE delegation_id='large-result'"
        ).fetchone()
    assert len(event_json.encode()) <= 512
    assert len(result_json.encode()) <= 512
    spill = json.loads(result_json)["payload_spill"]
    from pathlib import Path

    payload = Path(spill["path"])
    assert payload.exists()
    assert "x" * 100 in payload.read_text(encoding="utf-8")


def test_terminal_cas_loss_cannot_publish_ghost_completion(monkeypatch):
    _insert_restart_row(
        "cas-race", state="running", child_sessions_json='["child"]'
    )
    with ad._records_lock:
        ad._records["cas-race"] = {
            "delegation_id": "cas-race",
            "status": "running",
            "session_key": "telegram:origin",
            "dispatched_at": 1,
            "execution_generation": 0,
        }

    persist = ad._persist_completion

    def drain_wins(event, result):
        assert ad.defer_restartable_interruption("cas-race", "gateway_drain")
        return persist(event, result)

    monkeypatch.setattr(ad, "_persist_completion", drain_wins)
    ad._finalize("cas-race", {"status": "completed"}, "completed")
    row = ad.get_durable_delegation("cas-race")
    assert row["state"] == "restart_pending"
    with ad._transaction() as conn:
        event_json = conn.execute(
            "SELECT event_json FROM async_delegations "
            "WHERE delegation_id='cas-race'"
        ).fetchone()[0]
    assert event_json is None
    assert process_registry.completion_queue.empty()
