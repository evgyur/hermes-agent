import hashlib
import json

import pytest

from hermes_state import SessionDB


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _admit(db, message_id, *, session_key="sk", thread_id="topic", now=10.0):
    origin = {
        "platform": "telegram",
        "profile": "main",
        "chat_id": "-1001",
        "thread_id": thread_id,
        "message_id": str(message_id),
        "user_id": "42",
    }
    payload = {
        "text": f"message-{message_id}",
        "message_type": "text",
        "message_id": str(message_id),
    }
    origin_json = _canonical_json(origin)
    payload_json = _canonical_json(payload)
    return db.admit_gateway_drain_inbox(
        platform="telegram",
        chat_id="-1001",
        thread_id=thread_id,
        message_id=str(message_id),
        user_id="42",
        session_key=session_key,
        session_id=None,
        origin_json=origin_json,
        origin_sha256=hashlib.sha256(origin_json.encode()).hexdigest(),
        payload_json=payload_json,
        payload_sha256=hashlib.sha256(payload_json.encode()).hexdigest(),
        busy_mode="interrupt",
        received_at=now,
    )


def test_drain_admission_is_atomic_deduplicated_and_fifo(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        first = _admit(db, "100", now=10.0)
        duplicate = _admit(db, "100", now=11.0)
        second = _admit(db, "101", now=12.0)

        assert first["accepted"] is True
        assert duplicate["accepted"] is True
        assert duplicate["duplicate"] is True
        assert duplicate["sequence"] == first["sequence"]
        assert second["sequence"] > first["sequence"]
        assert [row["message_id"] for row in db.list_gateway_drain_inbox_ready()] == [
            "100",
            "101",
        ]
        assert db.get_gateway_message_ledger(first["ingress_ledger_id"])["status"] == "received"
    finally:
        db.close()


def test_drain_claim_reclaim_and_terminal_payload_scrub(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        admitted = _admit(db, "200")
        row_id = admitted["inbox_id"]
        assert db.claim_gateway_drain_inbox(
            row_id, owner="boot-a", token="lease-a", lease_expires_at=20.0
        )
        assert db.reclaim_gateway_drain_inbox(now=21.0) == {
            "leased": 1,
            "materialized": 0,
            "dispatched": 0,
        }
        assert db.claim_gateway_drain_inbox(
            row_id, owner="boot-b", token="lease-b", lease_expires_at=30.0
        )
        assert db.ack_gateway_drain_inbox_materialized(
            row_id, owner="boot-b", token="lease-b"
        )
        assert db.handoff_gateway_drain_inbox(
            row_id,
            owner="boot-b",
            token="lease-b",
            dispatch_token="dispatch-b",
            dispatch_expires_at=40.0,
        )
        assert db.accept_gateway_drain_dispatch(
            row_id,
            dispatch_token="dispatch-b",
            consumer_owner="runner-b",
            consumer_token="consumer-b",
            consumer_expires_at=50.0,
        )
        assert db.complete_gateway_drain_inbox(
            row_id,
            dispatch_token="dispatch-b",
            consumer_token="consumer-b",
        )
        terminal = db.get_gateway_drain_inbox(row_id)
        assert terminal["state"] == "TERMINAL"
        assert terminal["payload_json"] is None
        assert terminal["payload_sha256"]
        assert db.get_gateway_message_ledger(admitted["ingress_ledger_id"])["status"] == "completed"
    finally:
        db.close()


def test_drain_queue_limits_fail_closed_without_orphan_ledger(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        assert _admit(db, "300")["accepted"] is True
        rejected = db.admit_gateway_drain_inbox(
            platform="telegram",
            chat_id="-1001",
            thread_id="topic",
            message_id="301",
            user_id="42",
            session_key="sk",
            session_id=None,
            origin_json='{"message_id":"301"}',
            origin_sha256=hashlib.sha256(b'{"message_id":"301"}').hexdigest(),
            payload_json='{"text":"overflow"}',
            payload_sha256=hashlib.sha256(b'{"text":"overflow"}').hexdigest(),
            busy_mode="queue",
            max_route_pending=1,
            max_total_pending=4096,
        )
        assert rejected == {"accepted": False, "reason": "route_limit"}
        with db._read_ctx() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM gateway_message_ledger WHERE message_id='301'"
            ).fetchone()[0] == 0
    finally:
        db.close()


def test_drain_invalid_claim_is_quarantined_without_reopening_identity(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        admitted = _admit(db, "400")
        row_id = admitted["inbox_id"]
        assert db.claim_gateway_drain_inbox(
            row_id, owner="boot-a", token="lease-a", lease_expires_at=20.0
        )
        assert db.cancel_gateway_drain_inbox(
            row_id,
            reason="invalid_envelope:ValueError",
            owner="boot-a",
            token="lease-a",
        )
        cancelled = db.get_gateway_drain_inbox(row_id)
        assert cancelled["state"] == "CANCELLED"
        assert cancelled["payload_json"] is None
        assert cancelled["payload_sha256"]
        assert cancelled["failure_reason"] == "invalid_envelope:ValueError"
        assert db.get_gateway_message_ledger(admitted["ingress_ledger_id"])[
            "status"
        ] == "failed"

        duplicate = _admit(db, "400")
        assert duplicate["duplicate"] is True
        assert duplicate["state"] == "CANCELLED"
        assert db.list_gateway_drain_inbox_ready() == []
    finally:
        db.close()


@pytest.mark.parametrize("crash_state", ["LEASED", "MATERIALIZED", "DISPATCHED"])
def test_drain_each_preterminal_crash_boundary_reclaims_once(tmp_path, crash_state):
    db = SessionDB(tmp_path / f"{crash_state}.db")
    try:
        admitted = _admit(db, f"500-{crash_state}")
        row_id = admitted["inbox_id"]
        assert db.claim_gateway_drain_inbox(
            row_id, owner="dead-boot", token="lease", lease_expires_at=999.0
        )
        if crash_state in {"MATERIALIZED", "DISPATCHED"}:
            assert db.ack_gateway_drain_inbox_materialized(
                row_id, owner="dead-boot", token="lease"
            )
        if crash_state == "DISPATCHED":
            assert db.handoff_gateway_drain_inbox(
                row_id,
                owner="dead-boot",
                token="lease",
                dispatch_token="dispatch",
                dispatch_expires_at=999.0,
            )
            assert db.accept_gateway_drain_dispatch(
                row_id,
                dispatch_token="dispatch",
                consumer_owner="dead-boot",
                consumer_token="consumer",
                consumer_expires_at=999.0,
            )

        counts = db.reclaim_gateway_drain_inbox(
            now=20.0, dead_owners=("dead-boot",)
        )
        assert counts[crash_state.lower()] == 1
        ready = db.get_gateway_drain_inbox(row_id)
        assert ready["state"] == "READY"
        assert ready["lease_token"] is None
        assert ready["dispatch_token"] is None
        assert ready["consumer_token"] is None
        assert db.get_gateway_message_ledger(admitted["ingress_ledger_id"])[
            "status"
        ] in {"received", "requeued"}

        # The same dead-owner sweep is idempotent and a fresh owner can claim
        # the exact same ingress row rather than materializing a duplicate.
        assert db.reclaim_gateway_drain_inbox(
            now=21.0, dead_owners=("dead-boot",)
        ) == {"leased": 0, "materialized": 0, "dispatched": 0}
        assert db.claim_gateway_drain_inbox(
            row_id, owner="new-boot", token="new-lease", lease_expires_at=40.0
        )
        with db._read_ctx() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM gateway_drain_inbox WHERE inbox_id=?",
                (row_id,),
            ).fetchone()[0] == 1
    finally:
        db.close()


def test_drain_materialization_is_atomic_and_reuses_crash_tail_once(tmp_path):
    db = SessionDB(tmp_path / "materialized.db")
    try:
        session_id = "materialized-session"
        holder = "gateway-turn-holder"
        db.create_session(session_id, source="telegram")
        assert db.acquire_session_turn_lease(
            session_id, holder, wait_seconds=0.1
        )
        admitted = _admit(db, "600", session_key="sk")
        inbox_id = admitted["inbox_id"]
        assert db.claim_gateway_drain_inbox(
            inbox_id,
            owner="boot-a",
            token="lease-a",
            lease_expires_at=30.0,
        )

        first = db.append_or_reuse_gateway_user_authority(
            session_id,
            content="message-600",
            platform_message_id="600",
            turn_lease_holder=holder,
        )
        assert first.inserted is True

        # Simulate a process death after the canonical user row committed but
        # before the inbox consumer acknowledgement reached the same DB.
        assert db.reclaim_gateway_drain_inbox(
            now=20.0, dead_owners=("boot-a",)
        )["leased"] == 1
        assert db.claim_gateway_drain_inbox(
            inbox_id,
            owner="boot-b",
            token="lease-b",
            lease_expires_at=40.0,
        )
        reused = db.append_or_reuse_gateway_user_authority(
            session_id,
            content="message-600",
            platform_message_id="600",
            turn_lease_holder=holder,
        )
        assert reused.inserted is False
        assert reused.row_id == first.row_id

        assert db.accept_gateway_drain_materialization(
            inbox_id,
            owner="boot-b",
            token="lease-b",
            session_id=session_id,
            message_row_id=reused.row_id,
        )
        terminal = db.get_gateway_drain_inbox(inbox_id)
        assert terminal["state"] == "TERMINAL"
        assert terminal["materialized_session_id"] == session_id
        assert terminal["materialized_message_row_id"] == first.row_id
        assert terminal["payload_json"] is None
        with db._read_ctx() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=? "
                "AND role='user' AND platform_message_id='600'",
                (session_id,),
            ).fetchone()[0] == 1
    finally:
        db.close()


def test_drain_gateway_command_completes_at_router_acceptance(tmp_path):
    db = SessionDB(tmp_path / "command.db")
    try:
        admitted = _admit(db, "601")
        inbox_id = admitted["inbox_id"]
        assert db.claim_gateway_drain_inbox(
            inbox_id,
            owner="boot-command",
            token="lease-command",
            lease_expires_at=30.0,
        )

        assert db.accept_gateway_drain_processing(
            inbox_id,
            owner="boot-command",
            token="lease-command",
            consumer_kind="gateway-command:stop",
        )
        terminal = db.get_gateway_drain_inbox(inbox_id)
        assert terminal["state"] == "TERMINAL"
        assert terminal["consumer_kind"] == "gateway-command:stop"
        assert terminal["payload_json"] is None
        assert db.get_gateway_message_ledger(admitted["ingress_ledger_id"])[
            "status"
        ] == "completed"
    finally:
        db.close()
