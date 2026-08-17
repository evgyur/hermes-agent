import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import tools.parent_task_barrier as barrier


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
    assert barrier.complete_continuation(
        barrier_id,
        second["continuation_claim"],
        result={"final_response": "done"},
    )
    assert not barrier.complete_continuation(
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
    assert barrier.complete_continuation(
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
    conn.close()
    assert {"parent_task_barriers", "parent_task_children"} <= tables


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
