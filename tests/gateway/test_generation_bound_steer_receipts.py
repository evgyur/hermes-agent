"""Durable, ordered, generation-bound ownership for gateway mid-turn steering."""
from __future__ import annotations

import threading
import json
from types import SimpleNamespace

from gateway.deferred_event_spool import serialize_message_event
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import Platform, SessionSource
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


def _event(message_id: str, text: str = "change course") -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        user_id="owner",
        user_name="Owner",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            user_id="owner",
            chat_id="chat",
            chat_type="dm",
        ),
        message_id=message_id,
        platform_update_id=42,
        reply_to_message_id="parent",
        metadata={"preserve": True},
    )


def _runner(db: SessionDB, adapter) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner._session_db = db
    runner._adapter_for_source = lambda _source: adapter
    runner._session_key_for_source = lambda _source: "sk"
    runner._is_session_run_current = lambda _key, generation: generation == 7
    return runner


def _admit(db: SessionDB, event: MessageEvent, *, receipt_id: str, generation: int, state="OFFERED"):
    row = db.record_gateway_message_received(
        platform="telegram",
        chat_id="chat",
        message_id=event.message_id,
        user_id="owner",
        session_key="sk",
        origin_type="real_user",
    )
    event._hermes_gateway_ledger_id = row
    db.admit_gateway_steer_receipt(
        receipt_id=receipt_id,
        session_key="sk",
        session_id="sid",
        generation=generation,
        ingress_ledger_id=row,
        payload_json=json.dumps({"event": serialize_message_event(event)}),
    )
    if state != "ADMITTED":
        db.transition_gateway_steer_receipt(
            receipt_id,
            generation=generation,
            expected_states=("ADMITTED",),
            state=state,
        )
    return row


def test_terminal_before_request_fence_enqueues_once_to_successor_fifo(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    db = SessionDB(tmp_path / "state.db")
    try:
        adapter = SimpleNamespace(_pending_messages={})
        runner = _runner(db, adapter)
        event = _event("steer-next")
        ledger_id = _admit(db, event, receipt_id="next", generation=7)

        assert runner._reconcile_terminal_steer_receipts("sk", 7) == 1
        assert adapter._pending_messages["sk"].message_id == "steer-next"
        assert db.get_gateway_message_ledger(ledger_id)["status"] == "requeued"
        assert db.list_gateway_steer_receipts("sk")[0]["state"] == "QUEUED_NEXT"

        assert runner._reconcile_terminal_steer_receipts("sk", 7) == 0
        assert list(adapter._pending_messages) == ["sk"]
    finally:
        db.close()


def test_stale_generation_is_suppressed_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    db = SessionDB(tmp_path / "state.db")
    try:
        adapter = SimpleNamespace(_pending_messages={})
        runner = _runner(db, adapter)
        runner._is_session_run_current = lambda _key, _generation: False
        event = _event("stale")
        _admit(db, event, receipt_id="stale", generation=6)

        assert runner._reconcile_terminal_steer_receipts("sk", 6) == 0
        assert adapter._pending_messages == {}
        assert db.list_gateway_steer_receipts("sk")[0]["state"] == "CANCELLED"
    finally:
        db.close()


def test_startup_restores_unfenced_receipt_and_holds_fenced_receipt(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    db = SessionDB(tmp_path / "state.db")
    try:
        adapter = SimpleNamespace(_pending_messages={})
        runner = _runner(db, adapter)
        queued = _event("crashed-before-fence", "queued after restart")
        fenced = _event("crashed-after-fence", "do not replay")
        _admit(db, queued, receipt_id="queued", generation=7)
        _admit(db, fenced, receipt_id="fenced", generation=7, state="REQUEST_FENCED")

        restored = runner._restore_spooled_deferred_events()

        assert [event.message_id for event in restored] == ["crashed-before-fence"]
        states = {row["receipt_id"]: row["state"] for row in db.list_gateway_steer_receipts("sk")}
        assert states == {
            "queued": "QUEUED_NEXT",
            "fenced": "AMBIGUOUS_PROVIDER_REQUEST",
        }
        assert "incident hold" in caplog.text
    finally:
        db.close()