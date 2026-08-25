import json

import pytest

from hermes_state import SessionDB


def _ledger(db, suffix):
    return db.record_gateway_message_received(
        platform="telegram", chat_id="1", message_id=f"message-{suffix}",
        session_key="sk", session_id="sid",
    )


def _admit(db, receipt_id, kind, generation=7, ingress_ledger_id=None):
    ledger_id = ingress_ledger_id or _ledger(db, receipt_id)
    return db.admit_gateway_busy_receipt(
        receipt_id=receipt_id,
        kind=kind,
        session_key="sk",
        session_id="sid",
        generation=generation,
        ingress_ledger_id=ledger_id,
        origin_json='{"chat_id":"1"}',
        origin_sha256="digest",
        payload_json=json.dumps({"text": receipt_id}),
    )


@pytest.mark.parametrize("kind", ["redirect", "steer"])
@pytest.mark.parametrize("offered", [False, True])
def test_restart_reconciles_accepted_busy_input_to_one_queue_row(
    tmp_path, kind, offered
):
    path = tmp_path / "state.db"
    db = SessionDB(path)
    receipt = f"r{1 if kind == 'redirect' else 2}"
    try:
        _admit(db, receipt, kind)
        if offered:
            assert db.transition_gateway_busy_receipt(
                receipt, generation=7, expected_states=("ADMITTED",), state="OFFERED"
            )
    finally:
        db.close()

    reopened = SessionDB(path)
    try:
        assert reopened.reconcile_gateway_busy_receipts_after_restart() == {
            "ambiguous": 0, "queued_next": 1, "cancelled": 0
        }
        assert reopened.reconcile_gateway_busy_receipts_after_restart() == {
            "ambiguous": 0, "queued_next": 0, "cancelled": 0
        }
        ready = reopened.list_gateway_busy_queue_ready(current_generations={"sk": 7})
        assert [row["receipt_id"] for row in ready] == [receipt]
        assert ready[0]["kind"] == kind
    finally:
        reopened.close()


def test_busy_receipts_preserve_fifo_and_generation_fence(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        first = _admit(db, "r1", "redirect")
        second = _admit(db, "r2", "steer")
        assert first["sequence"] < second["sequence"]
        assert not db.transition_gateway_busy_receipt(
            "r1", generation=8, expected_states=("ADMITTED",), state="OFFERED"
        )
        assert db.transition_gateway_busy_receipt(
            "r1", generation=7, expected_states=("ADMITTED",), state="OFFERED"
        )
        assert db.reconcile_gateway_busy_receipts_after_restart() == {
            "ambiguous": 0, "queued_next": 2, "cancelled": 0
        }
        assert [row["receipt_id"] for row in db.list_gateway_busy_queue_ready(
            current_generations={"sk": 7}
        )] == [
            "r1", "r2"
        ]
    finally:
        db.close()


def test_request_fenced_is_ambiguous_and_never_replayed(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        _admit(db, "r1", "steer")
        assert db.transition_gateway_busy_receipt(
            "r1", generation=7, expected_states=("ADMITTED",), state="OFFERED"
        )
        assert db.transition_gateway_busy_receipt(
            "r1", generation=7, expected_states=("OFFERED",), state="REQUEST_FENCED"
        )
        assert db.reconcile_gateway_busy_receipts_after_restart() == {
            "ambiguous": 1, "queued_next": 0, "cancelled": 0
        }
        assert db.list_gateway_busy_queue_ready(current_generations={"sk": 7}) == []
    finally:
        db.close()


def test_queue_claim_same_token_is_idempotent_and_conflict_fails(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        _admit(db, "r1", "redirect")
        db.reconcile_gateway_busy_receipts_after_restart()
        assert db.claim_gateway_busy_queue(
            "r1", owner="owner", token="token", lease_expires_at=20.0,
            current_generation=7,
        )
        assert db.claim_gateway_busy_queue(
            "r1", owner="owner", token="token", lease_expires_at=20.0,
            current_generation=7,
        )
        assert not db.claim_gateway_busy_queue(
            "r1", owner="other", token="other", lease_expires_at=20.0,
            current_generation=7,
        )
    finally:
        db.close()


def test_expired_queue_lease_reclaims_once_without_changing_identity(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        original = _admit(db, "r1", "steer")
        db.reconcile_gateway_busy_receipts_after_restart()
        assert db.claim_gateway_busy_queue(
            "r1", owner="dead", token="token", lease_expires_at=20.0,
            current_generation=7,
        )
        assert db.reclaim_gateway_busy_queue(
            now=21.0, current_generations={"sk": 7}
        ) == {
            "leased": 1, "materialized": 0, "dispatched": 0
        }
        assert db.reclaim_gateway_busy_queue(
            now=21.0, current_generations={"sk": 7}
        ) == {
            "leased": 0, "materialized": 0, "dispatched": 0
        }
        ready = db.list_gateway_busy_queue_ready(current_generations={"sk": 7})
        assert [(row["receipt_id"], row["sequence"]) for row in ready] == [
            ("r1", original["sequence"])
        ]
    finally:
        db.close()


def test_handoff_and_consumer_acceptance_bind_exact_ingress_ledger(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        ledger_id = _ledger(db, "handoff")
        _admit(db, "r1", "redirect", ingress_ledger_id=ledger_id)
        db.reconcile_gateway_busy_receipts_after_restart()
        assert db.claim_gateway_busy_queue(
            "r1", owner="materializer", token="lease", lease_expires_at=20.0,
            current_generation=7,
        )
        assert db.ack_gateway_busy_queue_materialized(
            "r1", owner="materializer", token="lease", current_generation=7,
        )
        assert not db.handoff_gateway_busy_queue(
            "r1", owner="materializer", token="lease",
            ingress_ledger_id=ledger_id + 1, dispatch_token="dispatch",
            dispatch_expires_at=30.0, current_generation=7,
        )
        assert db.handoff_gateway_busy_queue(
            "r1", owner="materializer", token="lease",
            ingress_ledger_id=ledger_id, dispatch_token="dispatch",
            dispatch_expires_at=30.0, current_generation=7,
        )
        assert db.get_gateway_message_ledger(ledger_id)["status"] == "in_progress"
        assert db.accept_gateway_busy_dispatch(
            "r1", dispatch_token="dispatch", consumer_owner="agent",
            consumer_token="consumer", consumer_expires_at=40.0,
            current_generation=7,
        )
        assert db.accept_gateway_busy_dispatch(
            "r1", dispatch_token="dispatch", consumer_owner="agent",
            consumer_token="consumer", consumer_expires_at=40.0,
            current_generation=7,
        )
        assert not db.accept_gateway_busy_dispatch(
            "r1", dispatch_token="dispatch", consumer_owner="other",
            consumer_token="other", consumer_expires_at=40.0,
            current_generation=7,
        )
    finally:
        db.close()


def test_stale_generation_cannot_be_claimed_or_affect_ingress(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        stale_ledger = _ledger(db, "stale")
        _admit(db, "r1", "steer", generation=7, ingress_ledger_id=stale_ledger)
        _admit(db, "r2", "steer", generation=8)
        assert db.reconcile_gateway_busy_receipts_after_restart() == {
            "ambiguous": 0, "queued_next": 1, "cancelled": 1
        }
        assert [row["receipt_id"] for row in db.list_gateway_busy_queue_ready(
            current_generations={"sk": 8}
        )] == ["r2"]
        assert not db.claim_gateway_busy_queue(
            "r1", owner="old", token="old", lease_expires_at=20.0,
            current_generation=8,
        )
        assert db.get_gateway_message_ledger(stale_ledger)["status"] == "received"
    finally:
        db.close()


def test_caller_generation_fences_queue_without_newer_receipt(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        ledger_id = _ledger(db, "generation-only")
        _admit(db, "r1", "redirect", generation=7, ingress_ledger_id=ledger_id)
        db.reconcile_gateway_busy_receipts_after_restart()
        assert db.list_gateway_busy_queue_ready(current_generations={"sk": 8}) == []
        assert not db.claim_gateway_busy_queue(
            "r1", owner="stale", token="stale", lease_expires_at=20.0,
            current_generation=8,
        )
        assert not db.claim_gateway_busy_queue(
            "r1", owner="unknown", token="unknown", lease_expires_at=20.0,
        )
        assert db.get_gateway_message_ledger(ledger_id)["status"] == "received"
    finally:
        db.close()
