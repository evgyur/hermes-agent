import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools import async_delegation as ad
from tools import parent_task_barrier as barrier


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ad._reset_for_tests()
    yield
    ad._reset_for_tests()


def _seed_required_completion():
    now = time.time()
    delegation_id = "deleg-required"
    record = {
        "delegation_id": delegation_id,
        "goal": "required child",
        "context": None,
        "toolsets": None,
        "role": "leaf",
        "model": "m",
        "session_key": "agent:main:telegram:group:-100:4",
        "origin_ui_session_id": "",
        "origin_session_id": "",
        "parent_session_id": "parent-session",
        "is_batch": False,
        "dispatched_at": now,
        "status": "running",
    }
    assert ad._persist_dispatch(record)
    barrier_id = barrier.admit_required_child(
        origin_session=record["session_key"],
        parent_session_id=record["parent_session_id"],
        root_turn_id="root-turn",
        task_id=delegation_id,
    )
    event = {
        "type": "async_delegation",
        "delegation_id": delegation_id,
        "session_key": record["session_key"],
        "parent_session_id": record["parent_session_id"],
        "status": "completed",
        "completed_at": now + 1,
    }
    assert ad._persist_completion(event, {"summary": "evidence"})
    return barrier_id, event


@pytest.mark.asyncio
async def test_gateway_consumes_bound_child_without_direct_reinjection():
    from gateway.run import GatewayRunner

    _barrier_id, event = _seed_required_completion()
    runner = object.__new__(GatewayRunner)

    consumed = await runner._consume_parent_barrier_child(event)

    assert consumed is True
    assert ad.completion_delivery_disposition(event["delegation_id"]) == "delivered"


@pytest.mark.asyncio
async def test_gateway_injects_only_trusted_aggregate_continuation():
    from gateway.run import GatewayRunner

    barrier_id, _event = _seed_required_completion()
    policy = barrier.finalization_policy(
        parent_session_id="parent-session", root_turn_id="root-turn"
    )
    assert policy["action"] == "withhold"
    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert isinstance(claim, barrier.TrustedParentTaskContinuation)

    runner = object.__new__(GatewayRunner)
    runner._inject_watch_notification = AsyncMock(return_value=True)

    accepted = await runner._deliver_parent_task_continuation(claim)

    assert accepted is True
    runner._inject_watch_notification.assert_awaited_once_with(
        claim["synthetic_message"], claim
    )
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "resuming"


@pytest.mark.asyncio
async def test_failed_aggregate_injection_releases_durable_claim():
    from gateway.run import GatewayRunner

    barrier_id, _event = _seed_required_completion()
    barrier.finalization_policy(
        parent_session_id="parent-session", root_turn_id="root-turn"
    )
    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert claim is not None

    runner = object.__new__(GatewayRunner)
    runner._inject_watch_notification = AsyncMock(return_value=False)
    assert await runner._deliver_parent_task_continuation(claim) is False

    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "ready"
    assert snapshot["barrier"]["continuation_status"] == "pending"


@pytest.mark.asyncio
async def test_ordinary_user_event_is_parked_behind_active_barrier():
    from gateway.run import GatewayRunner

    barrier.admit_required_child(
        origin_session="agent:main:telegram:dm:1",
        parent_session_id="parent",
        root_turn_id="root",
        task_id="child",
    )
    runner = object.__new__(GatewayRunner)
    queued = []
    runner._queue_or_replace_pending_event = (
        lambda session_key, event: queued.append((session_key, event))
    )
    event = SimpleNamespace(text="steer me after the child")

    parked = await runner._park_user_event_for_parent_barrier(
        event=event,
        session_key="agent:main:telegram:dm:1",
        parent_session_id="parent",
    )
    assert parked is True
    assert queued == [("agent:main:telegram:dm:1", event)]

    barrier.cancel_session_barriers(origin_session="agent:main:telegram:dm:1")
    assert not await runner._park_user_event_for_parent_barrier(
        event=event,
        session_key="agent:main:telegram:dm:1",
        parent_session_id="parent",
    )


@pytest.mark.asyncio
async def test_platform_exception_abandons_only_trusted_parent_delivery(monkeypatch):
    import gateway.delivery_ledger as ledger
    from gateway.platforms.base import _abort_parent_task_delivery

    calls = []
    monkeypatch.setattr(
        ledger,
        "mark_abandoned",
        lambda obligation_id, error="": calls.append(
            ("abandoned", obligation_id, error)
        ),
    )
    monkeypatch.setattr(
        barrier,
        "release_accepted_continuation",
        lambda barrier_id, claim: calls.append(("released", barrier_id, claim))
        or True,
    )
    delivery = barrier.TrustedParentTaskDelivery(
        "final",
        barrier_id="barrier",
        continuation_claim="claim",
        result={"final_response": "final"},
    )

    assert await _abort_parent_task_delivery(
        delivery, "oid", error="platform exception"
    )
    assert calls == [
        ("abandoned", "oid", "platform exception"),
        ("released", "barrier", "claim"),
    ]
    assert not await _abort_parent_task_delivery(
        "user-authored lookalike", "oid-2", error="ignored"
    )
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_platform_ack_settles_trusted_parent_delivery():
    from gateway.delivery_ledger import (
        mark_attempting,
        mark_delivered,
        record_obligation,
    )
    from gateway.platforms.base import (
        _prepare_parent_task_delivery,
        _settle_parent_task_delivery,
    )

    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="root",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="root")
    conn = sqlite3.connect(barrier._db_path())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS async_delegations(
               delegation_id TEXT PRIMARY KEY, result_json TEXT
           )"""
    )
    conn.execute(
        "INSERT OR REPLACE INTO async_delegations VALUES ('child', '{\"summary\":\"done\"}')"
    )
    conn.commit()
    conn.close()
    barrier.record_child_terminal(task_id="child", state="completed", result={})
    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert claim is not None
    assert barrier.accept_continuation(
        barrier_id,
        claim["continuation_claim"],
        accepted_turn_id="turn",
        owner_pid=1,
    )
    delivery = barrier.TrustedParentTaskDelivery(
        "final",
        barrier_id=barrier_id,
        continuation_claim=claim["continuation_claim"],
        result={"final_response": "final"},
    )
    empty_delivery = barrier.TrustedParentTaskDelivery(
        "",
        barrier_id=barrier_id,
        continuation_claim=claim["continuation_claim"],
        result={},
    )
    assert not await _settle_parent_task_delivery(
        empty_delivery, delivered=True, obligation_id=""
    )
    unbound_snapshot = barrier.barrier_snapshot(barrier_id)
    assert unbound_snapshot is not None
    assert unbound_snapshot["barrier"]["state"] == "continuing"
    record_obligation(
        obligation_id="oid",
        session_key="origin",
        platform="telegram",
        chat_id="1",
        thread_id=None,
        content="final",
    )
    mark_attempting("oid")
    assert await _prepare_parent_task_delivery(delivery, "oid")
    mark_delivered("oid")
    assert await _settle_parent_task_delivery(
        delivery, delivered=True, obligation_id="oid"
    )
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "closed"


def test_streaming_is_buffered_before_required_child_admission():
    from gateway.run import (
        _parent_task_stream_allowed,
        _parent_task_turn_buffers_streaming,
    )

    capable = SimpleNamespace(valid_tool_names={"delegate_task", "terminal"})
    incapable = SimpleNamespace(valid_tool_names={"terminal"})
    assert _parent_task_turn_buffers_streaming(capable)
    assert not _parent_task_turn_buffers_streaming(incapable)
    assert _parent_task_turn_buffers_streaming(incapable, {"barrier_id": "b"})

    agent = SimpleNamespace(_parent_task_barrier_stream_suppressed=False)
    assert _parent_task_stream_allowed(agent, run_still_current=True)
    agent._parent_task_barrier_stream_suppressed = True
    assert not _parent_task_stream_allowed(agent, run_still_current=True)
    assert not _parent_task_stream_allowed(agent, run_still_current=False)


def test_goal_outcome_defers_standing_goal_judge_for_provisional_turn():
    from gateway.run import _goal_turn_outcomes

    outcomes = _goal_turn_outcomes(
        {
            "final_response": "provisional",
            "suppress_delivery": True,
            "defer_goal_evaluation": True,
        }
    )

    assert outcomes[0]["delivery_suppressed"] is True
    assert outcomes[0]["defer_goal_evaluation"] is True
