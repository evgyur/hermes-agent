import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tools import parent_task_barrier as barrier


def _use_db(monkeypatch, tmp_path):
    path = tmp_path / "state.db"
    monkeypatch.setattr(barrier, "_db_path", lambda: path)
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE async_delegations (
               delegation_id TEXT PRIMARY KEY,
               result_json TEXT
           )"""
    )
    conn.execute(
        """CREATE TABLE delivery_obligations (
               obligation_id TEXT PRIMARY KEY,
               state TEXT NOT NULL
           )"""
    )
    conn.commit()
    conn.close()
    return path


def _put_result(task_id, result):
    conn = sqlite3.connect(barrier._db_path())
    conn.execute(
        "INSERT OR REPLACE INTO async_delegations(delegation_id, result_json) "
        "VALUES (?, ?)",
        (task_id, json.dumps(result)),
    )
    conn.commit()
    conn.close()


def _accept(claim):
    assert barrier.accept_continuation(
        claim["barrier_id"],
        claim["continuation_claim"],
        accepted_turn_id="test-turn",
        owner_pid=os.getpid(),
    )


def _complete(barrier_id, claim, *, result=None):
    obligation_id = f"delivery-{claim}"
    if not barrier.bind_delivery_obligation(
        barrier_id,
        claim,
        obligation_id=obligation_id,
        result=result,
    ):
        return False
    with barrier._transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO delivery_obligations(obligation_id, state) "
            "VALUES (?, 'delivered')",
            (obligation_id,),
        )
    return barrier.complete_continuation_after_delivery(
        barrier_id,
        claim,
        obligation_id=obligation_id,
    )


def _admit_pair():
    first = barrier.admit_required_child(
        origin_session="agent:main:telegram:group:1:topic:2",
        parent_session_id="parent-session",
        root_turn_id="root-turn",
        task_id="child-a",
    )
    second = barrier.admit_required_child(
        origin_session="agent:main:telegram:group:1:topic:2",
        parent_session_id="parent-session",
        root_turn_id="root-turn",
        task_id="child-b",
    )
    assert second == first
    return first


def test_ordinary_session_delivery_parks_only_unbound_terminal_barriers(
    monkeypatch, tmp_path
):
    _use_db(monkeypatch, tmp_path)
    ready = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent-ready",
        root_turn_id="turn-ready",
        task_id="child-ready",
    )
    running = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent-running",
        root_turn_id="turn-running",
        task_id="child-running",
    )
    other = barrier.admit_required_child(
        origin_session="other-origin",
        parent_session_id="parent-other",
        root_turn_id="turn-other",
        task_id="child-other",
    )
    for parent, turn in (
        ("parent-ready", "turn-ready"),
        ("parent-running", "turn-running"),
        ("parent-other", "turn-other"),
    ):
        barrier.finalization_policy(parent_session_id=parent, root_turn_id=turn)
    for task_id in ("child-ready", "child-other"):
        _put_result(task_id, {"summary": "done"})
        barrier.record_child_terminal(
            task_id=task_id, state="completed", result={"summary": "done"}
        )

    assert (
        barrier.supersede_terminal_session_barriers_after_delivery(
            origin_session="origin"
        )
        == 1
    )
    ready_snapshot = barrier.barrier_snapshot(ready)
    running_snapshot = barrier.barrier_snapshot(running)
    other_snapshot = barrier.barrier_snapshot(other)
    assert ready_snapshot is not None
    assert running_snapshot is not None
    assert other_snapshot is not None
    assert ready_snapshot["barrier"]["state"] == "cancelled"
    assert (
        ready_snapshot["barrier"]["continuation_status"]
        == "superseded_by_session_delivery"
    )
    assert running_snapshot["barrier"]["state"] == "open"
    assert other_snapshot["barrier"]["state"] == "ready"
    assert barrier.claim_next_ready_continuation(owner="gateway") is not None


def test_ordinary_session_delivery_never_supersedes_bound_delivery(
    monkeypatch, tmp_path
):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result("child", {"summary": "done"})
    barrier.record_child_terminal(
        task_id="child", state="completed", result={"summary": "done"}
    )
    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert claim is not None
    _accept(claim)
    assert barrier.bind_delivery_obligation(
        barrier_id,
        claim["continuation_claim"],
        obligation_id="delivery-bound",
    )

    assert (
        barrier.supersede_terminal_session_barriers_after_delivery(
            origin_session="origin"
        )
        == 0
    )
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "continuing"
    assert snapshot["barrier"]["continuation_status"] == "accepted"


def test_ordinary_session_delivery_never_supersedes_live_accepted_continuation(
    monkeypatch, tmp_path
):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result("child", {"summary": "done"})
    barrier.record_child_terminal(task_id="child", state="completed", result={})
    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert claim is not None
    _accept(claim)

    assert (
        barrier.supersede_terminal_session_barriers_after_delivery(
            origin_session="origin",
            knowledge_cutoff=10**10,
        )
        == 0
    )
    assert barrier.bind_delivery_obligation(
        barrier_id,
        claim["continuation_claim"],
        obligation_id="delivery-live",
    )


def test_ordinary_delivery_requires_every_child_terminal_before_knowledge_cutoff(
    monkeypatch, tmp_path
):
    _use_db(monkeypatch, tmp_path)
    barrier_id = _admit_pair()
    barrier.finalization_policy(
        parent_session_id="parent-session", root_turn_id="root-turn"
    )
    for task_id in ("child-a", "child-b"):
        _put_result(task_id, {"summary": task_id})
        barrier.record_child_terminal(task_id=task_id, state="completed", result={})
    with barrier._transaction() as conn:
        conn.execute(
            "UPDATE parent_task_children SET terminal_at=100 WHERE task_id='child-a'"
        )
        conn.execute(
            "UPDATE parent_task_children SET terminal_at=300 WHERE task_id='child-b'"
        )

    assert (
        barrier.supersede_terminal_session_barriers_after_delivery(
            origin_session="agent:main:telegram:group:1:topic:2",
            knowledge_cutoff=200,
        )
        == 0
    )
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "ready"


def test_barrier_withholds_until_all_required_children_are_terminal(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = _admit_pair()

    policy = barrier.finalization_policy(
        parent_session_id="parent-session", root_turn_id="root-turn"
    )
    assert policy == {
        "action": "withhold",
        "barrier_id": barrier_id,
        "defer_goal_evaluation": True,
    }

    _put_result("child-a", {"summary": "A"})
    barrier.record_child_terminal(
        task_id="child-a", state="completed", result={"summary": "A"}
    )
    assert barrier.claim_next_ready_continuation(owner="gateway-1") is None

    _put_result("child-b", {"error": "B failed"})
    barrier.record_child_terminal(
        task_id="child-b", state="failed", result={"error": "B failed"}
    )
    event = barrier.claim_next_ready_continuation(owner="gateway-1")
    assert isinstance(event, barrier.TrustedParentTaskContinuation)
    assert event["barrier_id"] == barrier_id
    assert "\"summary\": \"A\"" in event["text"]
    assert "B failed" in event["text"]
    assert barrier.claim_next_ready_continuation(owner="gateway-2") is None

    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "resuming"
    assert [child["state"] for child in snapshot["children"]] == [
        "completed",
        "failed",
    ]


def test_batch_outcomes_are_read_from_authoritative_delegation_result(
    monkeypatch, tmp_path
):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="batch",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result(
        "batch",
        {
            "results": [
                {"task_index": 0, "status": "completed", "summary": "alpha"},
                {"task_index": 1, "status": "completed", "summary": "beta"},
            ]
        },
    )
    barrier.record_child_terminal(task_id="batch", state="completed", result={})

    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert claim is not None
    assert "alpha" in claim["synthetic_message"]
    assert "beta" in claim["synthetic_message"]
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert "result_json" not in snapshot["children"][0]


def test_continuation_claim_releases_reclaims_and_closes_once(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result("child", {"summary": "done"})
    barrier.record_child_terminal(
        task_id="child", state="completed", result={"summary": "done"}
    )

    first = barrier.claim_next_ready_continuation(owner="g1")
    assert first is not None
    assert barrier.release_continuation_claim(
        barrier_id, first["continuation_claim"]
    )
    second = barrier.claim_next_ready_continuation(owner="g2")
    assert second is not None
    assert second["continuation_claim"] != first["continuation_claim"]
    _accept(second)
    assert _complete(
        barrier_id,
        second["continuation_claim"],
        result={"final_response": "done"},
    )
    assert not _complete(
        barrier_id, second["continuation_claim"], result={}
    )
    assert barrier.claim_next_ready_continuation(owner="g3") is None
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "closed"


def test_expired_claim_is_recovered_and_retry_budget_parks(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result("child", {"summary": "done"})
    barrier.record_child_terminal(
        task_id="child", state="completed", result={"summary": "done"}
    )
    first = barrier.claim_next_ready_continuation(
        owner="gateway-1", lease_seconds=1
    )
    assert first is not None

    with barrier._transaction() as conn:
        conn.execute(
            """UPDATE parent_task_barriers
               SET continuation_lease_until=0, continuation_attempts=?
               WHERE barrier_id=?""",
            (barrier._MAX_CONTINUATION_ATTEMPTS, barrier_id),
        )

    terminal = barrier.claim_next_ready_continuation(owner="gateway-2")
    assert terminal is not None
    assert terminal["terminal_failure"] is True
    assert "terminal recovery" in terminal["synthetic_message"]
    _accept(terminal)
    assert _complete(
        barrier_id,
        terminal["continuation_claim"],
        result={"final_response": "truthful terminal result"},
    )
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "closed"


def test_dead_gateway_owner_is_reconciled_without_waiting_for_lease(
    monkeypatch, tmp_path
):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result("child", {"summary": "done"})
    barrier.record_child_terminal(
        task_id="child", state="completed", result={"summary": "done"}
    )
    first = barrier.claim_next_ready_continuation(owner="gateway:99999999:old")
    assert first is not None

    second = barrier.claim_next_ready_continuation(owner="gateway:1:new")
    assert second is not None
    assert second["barrier_id"] == barrier_id
    assert second["continuation_claim"] != first["continuation_claim"]


def test_accepted_live_owner_is_not_reclaimed_but_dead_owner_is(
    monkeypatch, tmp_path
):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result("child", {"summary": "done"})
    barrier.record_child_terminal(task_id="child", state="completed", result={})
    first = barrier.claim_next_ready_continuation(owner=f"gateway:{os.getpid()}:one")
    assert first is not None
    _accept(first)
    # Accepted work is owned by the live executor, not by the expired pre-start lease.
    assert barrier.claim_next_ready_continuation(owner="other") is None

    with barrier._transaction() as conn:
        conn.execute(
            "UPDATE parent_task_barriers SET accepted_owner_pid=99999999 "
            "WHERE barrier_id=?",
            (barrier_id,),
        )
    second = barrier.claim_next_ready_continuation(owner="gateway:1:recovery")
    assert second is not None
    assert second["continuation_claim"] != first["continuation_claim"]


def test_accepted_pid_reuse_is_reclaimed(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result("child", {"summary": "done"})
    barrier.record_child_terminal(task_id="child", state="completed", result={})
    first = barrier.claim_next_ready_continuation(owner="gateway")
    assert first is not None
    assert barrier.accept_continuation(
        barrier_id,
        first["continuation_claim"],
        accepted_turn_id="accepted",
        owner_pid=os.getpid(),
        owner_started_at=1,
    )
    second = barrier.claim_next_ready_continuation(owner="recovery")
    assert second is not None
    assert second["continuation_claim"] != first["continuation_claim"]


def test_terminal_delivery_retries_are_bounded(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result("child", {"summary": "done"})
    barrier.record_child_terminal(task_id="child", state="completed", result={})
    with barrier._transaction() as conn:
        conn.execute(
            "UPDATE parent_task_barriers SET continuation_attempts=? "
            "WHERE barrier_id=?",
            (barrier._MAX_CONTINUATION_ATTEMPTS, barrier_id),
        )

    for index in range(barrier._MAX_TERMINAL_DELIVERY_ATTEMPTS):
        with barrier._transaction() as conn:
            conn.execute(
                "UPDATE parent_task_barriers SET next_attempt_at=0 WHERE barrier_id=?",
                (barrier_id,),
            )
        claim = barrier.claim_next_ready_continuation(owner=f"gateway-{index}")
        assert claim is not None and claim["terminal_failure"] is True
        assert barrier.release_continuation_claim(
            barrier_id, claim["continuation_claim"]
        )

    with barrier._transaction() as conn:
        conn.execute(
            "UPDATE parent_task_barriers SET next_attempt_at=0 WHERE barrier_id=?",
            (barrier_id,),
        )
    assert barrier.claim_next_ready_continuation(owner="gateway-final") is None
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "failed"
    assert (
        snapshot["barrier"]["continuation_status"]
        == "terminal_delivery_exhausted"
    )


def test_delivered_obligation_closes_live_accepted_continuation(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result("child", {"summary": "done"})
    barrier.record_child_terminal(task_id="child", state="completed", result={})
    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert claim is not None
    _accept(claim)
    assert barrier.bind_delivery_obligation(
        barrier_id,
        claim["continuation_claim"],
        obligation_id="delivery-1",
        result={"final_response": "done"},
    )
    with barrier._transaction() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS delivery_obligations(
                   obligation_id TEXT PRIMARY KEY, state TEXT NOT NULL
               )"""
        )
        conn.execute(
            "INSERT INTO delivery_obligations VALUES ('delivery-1', 'delivered')"
        )
    assert barrier.claim_next_ready_continuation(owner="recovery") is None
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "closed"


def test_nested_generation_limit_fails_closed(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn-limit",
        task_id="child-limit",
    )
    barrier.finalization_policy(
        parent_session_id="parent", root_turn_id="turn-limit"
    )
    _put_result("child-limit", {"summary": "first"})
    barrier.record_child_terminal(
        task_id="child-limit", state="completed", result={}
    )
    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert claim is not None
    _accept(claim)
    with barrier._transaction() as conn:
        conn.execute(
            "UPDATE parent_task_barriers SET generation=? WHERE barrier_id=?",
            (barrier._MAX_GENERATIONS, barrier_id),
        )
    with pytest.raises(RuntimeError, match="generation limit"):
        barrier.admit_required_child(
            origin_session="origin",
            parent_session_id="parent",
            root_turn_id="turn-too-far",
            task_id="child-too-far",
            existing_barrier_id=barrier_id,
        )


def test_nested_required_child_advances_same_barrier_generation(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn-1",
        task_id="child-1",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn-1")
    _put_result("child-1", {"summary": "first"})
    barrier.record_child_terminal(task_id="child-1", state="completed", result={})
    first = barrier.claim_next_ready_continuation(owner="gateway")
    assert first is not None
    _accept(first)

    nested_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn-2",
        task_id="child-2",
        existing_barrier_id=barrier_id,
    )
    assert nested_id == barrier_id
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["generation"] == 1
    assert snapshot["barrier"]["root_turn_id"] == "turn-2"
    assert snapshot["barrier"]["state"] == "open"
    policy = barrier.finalization_policy(
        parent_session_id="parent", root_turn_id="turn-2"
    )
    assert policy["action"] == "withhold"
    _put_result("child-2", {"summary": "second"})
    barrier.record_child_terminal(task_id="child-2", state="completed", result={})
    second = barrier.claim_next_ready_continuation(owner="gateway")
    assert second is not None
    assert second["barrier_id"] == barrier_id


def test_duplicate_terminal_callbacks_and_concurrent_claims_are_idempotent(
    monkeypatch, tmp_path
):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
    _put_result("child", {"summary": "first"})
    barrier.record_child_terminal(
        task_id="child", state="completed", result={"summary": "first"}
    )
    barrier.record_child_terminal(
        task_id="child", state="failed", result={"error": "duplicate"}
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(
            pool.map(
                lambda index: barrier.claim_next_ready_continuation(
                    owner=f"gateway-{index}"
                ),
                range(8),
            )
        )
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["children"][0]["state"] == "completed"
    conn = sqlite3.connect(barrier._db_path())
    stored = conn.execute(
        "SELECT result_json FROM async_delegations WHERE delegation_id='child'"
    ).fetchone()[0]
    conn.close()
    assert json.loads(stored)["summary"] == "first"


def test_dead_initial_owner_recovers_after_child_terminal(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    _put_result("child", {"summary": "done"})
    barrier.record_child_terminal(task_id="child", state="completed", result={})
    with barrier._transaction() as conn:
        conn.execute(
            """UPDATE parent_task_barriers
               SET initial_owner_pid=99999999, initial_owner_started_at=1
               WHERE barrier_id=?""",
            (barrier_id,),
        )
    claim = barrier.claim_next_ready_continuation(owner="recovery")
    assert claim is not None
    assert claim["barrier_id"] == barrier_id


def test_transcript_and_initial_persisted_commit_atomically(monkeypatch, tmp_path):
    from hermes_state import SessionDB

    path = tmp_path / "state.db"
    monkeypatch.setattr(barrier, "_db_path", lambda: path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    db = SessionDB(db_path=path)
    db.create_session("parent", "telegram")
    inserted = db.append_messages_batch(
        "parent",
        [{"role": "assistant", "content": "", "timestamp": 1.0}],
        parent_task_barrier_id=barrier_id,
    )
    assert inserted == 1
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["initial_persisted"] == 1

    db.create_session("parent-rollback", "telegram")
    with pytest.raises(RuntimeError, match="atomic commit failed"):
        db.append_messages_batch(
            "parent-rollback",
            [{"role": "assistant", "content": "", "timestamp": 2.0}],
            parent_task_barrier_id="missing",
        )
    conn = sqlite3.connect(path)
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id='parent-rollback'"
    ).fetchone()[0]
    conn.close()
    assert count == 0
    db.close()


def test_schema_migrates_legacy_database_without_rewriting_legacy_rows(
    monkeypatch, tmp_path
):
    path = tmp_path / "state.db"
    monkeypatch.setattr(barrier, "_db_path", lambda: path)
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE async_delegations(
               delegation_id TEXT PRIMARY KEY,
               state TEXT,
               result_json TEXT
           )"""
    )
    conn.execute(
        "INSERT INTO async_delegations(delegation_id, state) VALUES ('legacy', 'completed')"
    )
    conn.commit()
    conn.close()

    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    assert barrier.barrier_snapshot(barrier_id) is not None

    conn = sqlite3.connect(path)
    assert conn.execute(
        "SELECT delegation_id, state FROM async_delegations"
    ).fetchall() == [("legacy", "completed")]
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (
        conn.execute(
            "SELECT schema_version FROM parent_task_barrier_meta WHERE singleton=1"
        ).fetchone()[0]
        == 6
    )
    conn.close()
    assert {"parent_task_barriers", "parent_task_children"} <= tables


def test_schema_version_five_advances_to_six(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    with barrier._transaction() as conn:
        conn.execute(
            "UPDATE parent_task_barrier_meta SET schema_version=5 WHERE singleton=1"
        )
    assert barrier.barrier_snapshot(barrier_id) is not None
    conn = sqlite3.connect(barrier._db_path())
    assert conn.execute(
        "SELECT schema_version FROM parent_task_barrier_meta WHERE singleton=1"
    ).fetchone()[0] == 6
    conn.close()


def test_explicit_session_cancellation_terminalizes_open_barrier(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    assert barrier.cancel_session_barriers(origin_session="origin") == 1
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "cancelled"
    assert snapshot["children"][0]["state"] == "cancelled"
    assert barrier.finalization_policy(
        parent_session_id="parent", root_turn_id="turn"
    )["action"] == "deliver"


def test_retention_prune_cascades_terminal_children(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    old_barrier = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent-old",
        root_turn_id="turn-old",
        task_id="child-old",
    )
    assert barrier.cancel_session_barriers(origin_session="origin") == 1
    with barrier._transaction() as conn:
        conn.execute(
            "UPDATE parent_task_barriers SET closed_at=0 WHERE barrier_id=?",
            (old_barrier,),
        )

    barrier.admit_required_child(
        origin_session="origin-new",
        parent_session_id="parent-new",
        root_turn_id="turn-new",
        task_id="child-new",
    )
    assert barrier.barrier_snapshot(old_barrier) is None
    conn = sqlite3.connect(barrier._db_path())
    count = conn.execute(
        "SELECT COUNT(*) FROM parent_task_children WHERE task_id='child-old'"
    ).fetchone()[0]
    conn.close()
    assert count == 0
