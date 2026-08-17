import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import tools.parent_task_barrier as barrier


def _use_db(monkeypatch, tmp_path):
    path = tmp_path / "state.db"
    monkeypatch.setattr(barrier, "_db_path", lambda: path)
    return path


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

    barrier.record_child_terminal(
        task_id="child-a", state="completed", result={"summary": "A"}
    )
    assert barrier.claim_next_ready_continuation(owner="gateway-1") is None

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


def test_continuation_claim_releases_reclaims_and_closes_once(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="turn",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="turn")
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

    assert barrier.claim_next_ready_continuation(owner="gateway-2") is None
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "failed"
    assert snapshot["barrier"]["continuation_status"] == "exhausted"


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
    assert json.loads(snapshot["children"][0]["result_json"])["summary"] == "first"


def test_schema_migrates_legacy_database_without_rewriting_legacy_rows(
    monkeypatch, tmp_path
):
    path = _use_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE async_delegations(delegation_id TEXT PRIMARY KEY, state TEXT)"
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
    ) == {"action": "deliver"}
