import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import hermes_state
import hermes_state_common
from hermes_state import AsyncSessionDB, SessionDB
from gateway.durable_continuation import GatewayContinuationStore


ALL_STATES = {
    "pending",
    "claimed",
    "waiting_unknown_effect",
    "completed",
    "cancelled",
    "superseded",
    "failed_terminal",
}


def _create(db, *, continuation_id="cont-1", generation=1, kind="restart"):
    return db.create_durable_continuation(
        continuation_id=continuation_id,
        session_key="telegram:chat:topic",
        session_id="session-1",
        origin_turn_id=f"turn-{generation}",
        kind=kind,
        generation=generation,
        input_digest=f"sha256:input-{generation}",
        descriptor={"source": "startup_recovery", "attempt": generation},
        now=float(generation),
    )


@pytest.fixture
def db(tmp_path):
    store = SessionDB(db_path=tmp_path / "state.db")
    try:
        yield store
    finally:
        store.close()


def test_schema_is_additive_versioned_and_contains_no_raw_payload_columns(db):
    assert hermes_state_common.SCHEMA_VERSION == 26
    assert hermes_state.SCHEMA_VERSION == 26
    assert "CREATE TABLE IF NOT EXISTS durable_continuations" in hermes_state_common.SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS durable_continuations" in hermes_state.SCHEMA_SQL
    assert hermes_state.DURABLE_CONTINUATION_STATES == ALL_STATES

    with db._read_ctx() as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(durable_continuations)")
        }
        index_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'idx_durable_continuations_one_active'"
        ).fetchone()[0]

    assert not ({"prompt", "payload", "tool_payload", "tool_input", "tool_output"} & columns)
    assert {"input_digest", "descriptor_json", "outcome_digest", "outcome_descriptor_json"} <= columns
    assert "WHERE state IN ('pending', 'claimed', 'waiting_unknown_effect')" in index_sql


@pytest.mark.asyncio
async def test_gateway_reuses_same_owner_claim_after_real_inbound_displaces_wake(db):
    store = GatewayContinuationStore(AsyncSessionDB(db), owner="gateway:123:1")
    entry = SimpleNamespace(
        session_key="telegram:chat:topic",
        session_id="session-1",
        resume_task_id="resume-task",
        resume_reason="restart_timeout",
    )

    first = await store.claim_entry(entry)
    second = await store.claim_entry(entry)

    assert first.acquired is True
    assert second.acquired is True
    assert second.claim == first.claim


def test_create_is_idempotent_and_active_generation_is_transactionally_unique(db):
    first = _create(db)
    retried = db.create_durable_continuation(
        continuation_id="different-retry-id",
        session_key="telegram:chat:topic",
        session_id="session-1",
        origin_turn_id="turn-1",
        kind="restart",
        generation=1,
        input_digest="sha256:input-1",
        descriptor={"attempt": 1, "source": "startup_recovery"},
        now=99,
    )
    assert retried["continuation_id"] == first["continuation_id"]
    assert len(db.list_durable_continuations()) == 1

    with pytest.raises(ValueError, match="non-terminal"):
        _create(db, continuation_id="cont-2", generation=2)

    # The partial unique index protects the invariant even if a caller bypasses
    # the SessionDB creation API.
    with pytest.raises(sqlite3.IntegrityError):
        db._execute_write(
            lambda conn: conn.execute(
                "INSERT INTO durable_continuations ("
                "continuation_id, session_key, origin_turn_id, kind, generation, "
                "state, input_digest, created_at, updated_at"
                ") VALUES ('direct-duplicate', 'telegram:chat:topic', 'turn-x', "
                "'restart', 9, 'pending', 'sha256:x', 9, 9)"
            )
        )

    replacement = db.create_durable_continuation(
        continuation_id="cont-2",
        session_key="telegram:chat:topic",
        session_id="session-2",
        origin_turn_id="turn-2",
        kind="restart",
        generation=2,
        input_digest="sha256:input-2",
        descriptor={"source": "startup_recovery"},
        supersede_existing=True,
        now=10,
    )
    assert replacement["state"] == "pending"
    superseded = db.get_durable_continuation("cont-1")
    assert superseded["state"] == "superseded"
    assert superseded["superseded_by_continuation_id"] == "cont-2"


def test_competing_claimers_have_exactly_one_winner(tmp_path):
    path = tmp_path / "state.db"
    creator = SessionDB(db_path=path)
    _create(creator)
    creator.close()

    first = SessionDB(db_path=path)
    second = SessionDB(db_path=path)
    barrier = threading.Barrier(2)

    def claim(store, owner, token):
        barrier.wait()
        return store.claim_durable_continuation(
            "cont-1",
            1,
            owner=owner,
            claim_token=token,
            lease_seconds=60,
            now=10,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda args: claim(*args),
                    [(first, "worker-a", "token-a"), (second, "worker-b", "token-b")],
                )
            )
        winners = [result for result in results if result is not None]
        assert len(winners) == 1
        assert winners[0]["claim_token"] in {"token-a", "token-b"}
    finally:
        first.close()
        second.close()


def test_claim_cas_rejects_wrong_generation_token_owner_and_stale_worker(db):
    _create(db)
    claimed = db.claim_durable_continuation(
        "cont-1", 1, owner="worker-a", claim_token="token-a", lease_seconds=10, now=10
    )
    assert claimed["state"] == "claimed"
    assert db.claim_durable_continuation(
        "cont-1", 1, owner="worker-b", claim_token="token-b", lease_seconds=10, now=10
    ) is None
    assert not db.renew_durable_continuation_claim(
        "cont-1", 2, owner="worker-a", claim_token="token-a", lease_seconds=10, now=11
    )
    assert not db.renew_durable_continuation_claim(
        "cont-1", 1, owner="worker-a", claim_token="stale", lease_seconds=10, now=11
    )
    assert not db.terminalize_durable_continuation(
        "cont-1",
        1,
        owner="worker-b",
        claim_token="token-a",
        state="completed",
        outcome_digest="sha256:wrong-owner",
        now=12,
    )
    after_rejected_outcome = db.get_durable_continuation("cont-1")
    assert after_rejected_outcome is not None
    assert after_rejected_outcome["state"] == "claimed"
    assert after_rejected_outcome["outcome_digest"] is None

    assert db.cancel_durable_continuation(
        "cont-1", 1, descriptor={"reason": "operator_cancel"}, now=12
    )
    assert not db.terminalize_durable_continuation(
        "cont-1",
        1,
        owner="worker-a",
        claim_token="token-a",
        state="completed",
        outcome_digest="sha256:stale",
        now=13,
    )
    assert db.get_durable_continuation("cont-1")["state"] == "cancelled"


def test_expired_unfenced_claim_returns_pending_and_old_worker_cannot_complete(db):
    _create(db)
    db.claim_durable_continuation(
        "cont-1", 1, owner="old", claim_token="old-token", lease_seconds=5, now=10
    )
    assert not db.terminalize_durable_continuation(
        "cont-1",
        1,
        owner="old",
        claim_token="old-token",
        state="completed",
        outcome_digest="sha256:late",
        now=15,
    )
    assert db.reap_expired_durable_continuation_claims(now=15) == {
        "pending": 1,
        "waiting_unknown_effect": 0,
    }
    reclaimed = db.claim_durable_continuation(
        "cont-1", 1, owner="new", claim_token="new-token", lease_seconds=20, now=16
    )
    assert reclaimed["claim_token"] == "new-token"
    assert not db.terminalize_durable_continuation(
        "cont-1",
        1,
        owner="old",
        claim_token="old-token",
        state="completed",
        outcome_digest="sha256:stale",
        now=17,
    )
    assert db.terminalize_durable_continuation(
        "cont-1",
        1,
        owner="new",
        claim_token="new-token",
        state="completed",
        outcome_digest="sha256:receipt",
        outcome_descriptor={"receipt_id": "safe-redacted-id"},
        now=18,
    )


def test_expired_fenced_claim_waits_for_explicit_unknown_effect_resolution(db):
    _create(db)
    db.claim_durable_continuation(
        "cont-1", 1, owner="worker", claim_token="fence-token", lease_seconds=5, now=10
    )
    assert db.mark_durable_continuation_effect_started(
        "cont-1", 1, owner="worker", claim_token="fence-token", now=11
    )
    assert db.reap_expired_durable_continuation_claims(now=15) == {
        "pending": 0,
        "waiting_unknown_effect": 1,
    }
    waiting = db.get_durable_continuation("cont-1")
    assert waiting["state"] == "waiting_unknown_effect"
    assert waiting["effect_fence"] == "fence-token"
    assert db.claim_durable_continuation(
        "cont-1", 1, owner="replay", claim_token="replay", lease_seconds=5, now=16
    ) is None
    assert db.resolve_durable_continuation_unknown_effect(
        "cont-1",
        1,
        state="completed",
        outcome_digest="sha256:reconciled",
        outcome_descriptor={"reconciliation": "provider_receipt_found"},
        now=17,
    )


def test_terminal_outcome_survives_close_and_reopen(tmp_path):
    path = tmp_path / "state.db"
    store = SessionDB(db_path=path)
    _create(store)
    store.claim_durable_continuation(
        "cont-1", 1, owner="worker", claim_token="token", lease_seconds=20, now=10
    )
    assert store.terminalize_durable_continuation(
        "cont-1",
        1,
        owner="worker",
        claim_token="token",
        state="failed_terminal",
        outcome_digest="sha256:failure-receipt",
        outcome_descriptor={"error_code": "provider_rejected"},
        now=11,
    )
    store.close()

    reopened = SessionDB(db_path=path)
    try:
        row = reopened.get_durable_continuation("cont-1")
        assert row is not None
        assert row["state"] == "failed_terminal"
        assert row["outcome_digest"] == "sha256:failure-receipt"
        assert row["outcome_descriptor"] == {"error_code": "provider_rejected"}
        assert row["claim_token"] is None
        assert row["completed_at"] == 11
        assert not reopened.cancel_durable_continuation("cont-1", 1, now=12)
        assert not reopened.supersede_durable_continuation(
            "cont-1", 1, superseded_by_continuation_id="cont-2", now=12
        )
    finally:
        reopened.close()


def test_cancel_and_supersede_are_deterministic(db):
    _create(db, continuation_id="cancel-me", kind="cancel")
    assert db.cancel_durable_continuation("cancel-me", 1, now=3)
    assert db.cancel_durable_continuation("cancel-me", 1, now=4)
    assert db.get_durable_continuation("cancel-me")["completed_at"] == 3

    _create(db, continuation_id="old", kind="replace")
    assert db.supersede_durable_continuation(
        "old", 1, superseded_by_continuation_id="new", now=5
    )
    assert db.supersede_durable_continuation(
        "old", 1, superseded_by_continuation_id="new", now=6
    )
    assert not db.supersede_durable_continuation(
        "old", 1, superseded_by_continuation_id="different", now=7
    )
    assert db.get_durable_continuation("old")["completed_at"] == 5


def test_raw_prompt_and_tool_payload_descriptors_are_rejected(db):
    for forbidden in ("prompt", "tool_payload"):
        with pytest.raises(ValueError, match="raw continuation field"):
            db.create_durable_continuation(
                continuation_id=f"unsafe-{forbidden}",
                session_key="s",
                origin_turn_id="turn",
                kind="restart",
                generation=1,
                input_digest="sha256:safe",
                descriptor={forbidden: "do not persist me"},
            )
    assert db.list_durable_continuations() == []


def test_async_session_db_generically_forwards_continuation_methods(db):
    async def exercise():
        facade = AsyncSessionDB(db)
        created = await facade.create_durable_continuation(
            continuation_id="async-cont",
            session_key="async-key",
            origin_turn_id="async-turn",
            kind="restart",
            generation=1,
            input_digest="sha256:async",
            descriptor={"source": "async_test"},
            now=1,
        )
        assert isinstance(created, dict)
        loaded = await facade.get_durable_continuation(created["continuation_id"])
        assert isinstance(loaded, dict)
        return created, loaded

    created, loaded = asyncio.run(exercise())
    assert created["continuation_id"] == "async-cont"
    assert loaded["state"] == "pending"
