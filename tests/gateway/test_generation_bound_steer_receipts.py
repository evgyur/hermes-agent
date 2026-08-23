"""Durable, ordered, generation-bound ownership for gateway mid-turn steering."""
from __future__ import annotations

import threading

from hermes_state import SessionDB


def test_steer_receipts_are_ordered_generation_bound_and_restart_safe(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(path)
    try:
        first = db.admit_gateway_steer_receipt(
            receipt_id="r1",
            session_key="sk",
            session_id="sid",
            generation=7,
            ingress_ledger_id=101,
            payload_json='{"text":"first"}',
        )
        second = db.admit_gateway_steer_receipt(
            receipt_id="r2",
            session_key="sk",
            session_id="sid",
            generation=7,
            ingress_ledger_id=102,
            payload_json='{"text":"second"}',
        )
        assert first["sequence"] < second["sequence"]
        assert db.transition_gateway_steer_receipt(
            "r1", generation=8, expected_states=("ADMITTED",), state="OFFERED"
        ) is False
        assert db.transition_gateway_steer_receipt(
            "r1", generation=7, expected_states=("ADMITTED",), state="OFFERED"
        ) is True
        assert db.transition_gateway_steer_receipt(
            "r1", generation=7, expected_states=("OFFERED",), state="REQUEST_FENCED"
        ) is True
    finally:
        db.close()

    reopened = SessionDB(path)
    try:
        rows = reopened.list_gateway_steer_receipts("sk", terminal=False)
        assert [row["receipt_id"] for row in rows] == ["r1", "r2"]
        assert [row["state"] for row in rows] == ["REQUEST_FENCED", "ADMITTED"]
        assert reopened.reconcile_gateway_steer_receipts_after_restart() == {
            "ambiguous": 1,
            "queued_next": 1,
        }
        assert [
            row["state"] for row in reopened.list_gateway_steer_receipts("sk")
        ] == ["AMBIGUOUS_PROVIDER_REQUEST", "QUEUED_NEXT"]
        # A second restart is idempotent: neither row becomes replayable again.
        assert reopened.reconcile_gateway_steer_receipts_after_restart() == {
            "ambiguous": 0,
            "queued_next": 0,
        }
    finally:
        reopened.close()


def test_agent_receipt_transitions_follow_trusted_marker_then_provider_result():
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent._pending_steer = None
    agent._pending_steer_lock = threading.Lock()
    transitions = []

    assert agent.steer(
        "change course",
        receipt_id="r1",
        receipt_transition=lambda receipt_id, state: transitions.append(
            (receipt_id, state)
        ),
    )
    assert agent._drain_pending_steer() == "change course"
    assert transitions == []
    agent._mark_drained_steer_request_fenced()
    assert transitions == [("r1", "REQUEST_FENCED")]
    agent._mark_fenced_steer_provider_result(accepted=True)
    assert transitions == [
        ("r1", "REQUEST_FENCED"),
        ("r1", "CONSUMED_CURRENT"),
    ]


def test_agent_provider_failure_after_fence_is_ambiguous_not_replayed():
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent._pending_steer = None
    agent._pending_steer_lock = threading.Lock()
    transitions = []
    assert agent.steer(
        "do not duplicate",
        receipt_id="r2",
        receipt_transition=lambda receipt_id, state: transitions.append(
            (receipt_id, state)
        ),
    )
    assert agent._drain_pending_steer() == "do not duplicate"
    agent._mark_drained_steer_request_fenced()
    agent._mark_fenced_steer_provider_result(accepted=False)
    assert transitions[-1] == ("r2", "AMBIGUOUS_PROVIDER_REQUEST")
    assert agent._drain_pending_steer() is None