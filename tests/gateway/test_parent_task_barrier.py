import time
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
