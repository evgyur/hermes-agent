"""Tests for gateway /goal verdict-message delivery.

The judge verdict message ("✓ Goal achieved", "⏸ budget exhausted", etc.)
must reach the user after each turn. Before this fix the code checked
``hasattr(adapter, "send_message")`` — but adapters expose ``send()``,
never ``send_message``, so the check always evaluated False and users
never saw verdicts. This test locks in the fix.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.session import SessionEntry, SessionSource, build_session_key


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import goals

    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


class _RecordingAdapter:
    """Minimal adapter that records send() invocations."""

    def __init__(self) -> None:
        self._pending_messages: dict = {}
        self.sends: list[dict] = []

    async def send(self, chat_id: str, content: str, reply_to=None, metadata=None):
        self.sends.append({"chat_id": chat_id, "content": content, "metadata": metadata})

        class _R:
            success = True
            message_id = "mock-msg"

        return _R()


def _make_runner_with_adapter(session_id: str = None):
    from gateway.run import GatewayRunner
    import uuid

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
    )
    runner.adapters = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._queued_events = {}

    src = _make_source()
    # Default to a unique session_id so xdist parallel runs on the same worker
    # don't see each other's GoalManager state (DEFAULT_DB_PATH gets frozen at
    # module-import time, defeating per-test HERMES_HOME monkeypatches).
    session_entry = SessionEntry(
        session_key=build_session_key(src),
        session_id=session_id or f"goal-sess-{uuid.uuid4().hex[:8]}",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )

    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store._generate_session_key.return_value = build_session_key(src)

    adapter = _RecordingAdapter()
    runner.adapters[Platform.TELEGRAM] = adapter
    return runner, adapter, session_entry, src


@pytest.mark.asyncio
async def test_goal_verdict_continue_enqueues_continuation(hermes_home):
    """When the judge says continue, both the 'continuing' status and the
    continuation-prompt event must be delivered. The continuation prompt is
    routed through the adapter's pending-messages FIFO so the goal loop
    proceeds on the next turn."""
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli.goals import GoalManager

    mgr = GoalManager(session_entry.session_id)
    mgr.set("polish the docs")

    with patch("hermes_cli.goals.judge_goal", return_value=("continue", "still needs work", False, None, False)):
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="here's a partial edit",
        )
        await asyncio.sleep(0.05)

    # Status line sent back
    assert len(adapter.sends) == 1
    assert "Continuing toward goal" in adapter.sends[0]["content"]
    # Continuation prompt enqueued for next turn
    assert adapter._pending_messages, "continuation prompt must be enqueued in pending_messages"


@pytest.mark.asyncio
async def test_goal_iteration_limit_bypasses_judge_and_enqueues_fresh_cycle(hermes_home):
    """A technical per-turn cap must resume /goal even if a queued callback ran next."""
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli import goals
    from hermes_cli.goals import GoalManager

    GoalManager(session_entry.session_id).set("finish the live repair")
    boundary = {
        "turn_exit_reason": "max_iterations_reached(200/200)",
        "final_response": "Partial checkpoint; work is not done.",
        "response_already_delivered": True,
    }

    with patch.object(goals, "judge_goal") as judge:
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="NO_REPLY",
            turn_outcomes=[boundary],
        )

    judge.assert_not_called()
    assert len(adapter.sends) == 1
    assert "technical iteration limit" in adapter.sends[0]["content"]
    assert adapter._pending_messages, "a fresh goal cycle must be enqueued"


@pytest.mark.asyncio
async def test_cap_then_normal_completion_accounts_both_and_judges_latest(hermes_home):
    """A later completion wins even if an earlier cap hits the turn budget."""
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli import goals
    from hermes_cli.goals import GoalManager

    GoalManager(session_entry.session_id).set("finish the live repair", max_turns=1)
    outcomes = [
        {
            "turn_exit_reason": "max_iterations_reached(200/200)",
            "final_response": "Partial checkpoint.",
            "response_already_delivered": True,
        },
        {
            "turn_exit_reason": "text_response(finish_reason=stop)",
            "final_response": "The standing goal is complete.",
            "response_already_delivered": False,
        },
    ]

    with patch.object(
        goals,
        "judge_goal",
        return_value=("done", "verified completion", False, None),
    ) as judge:
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response=outcomes[-1]["final_response"],
            turn_outcomes=outcomes,
        )
        await asyncio.sleep(0.05)

    judge.assert_called_once()
    state = goals.load_goal(session_entry.session_id)
    assert state is not None
    assert state.status == "done"
    assert state.turns_used == 2
    assert not adapter._pending_messages
    assert len(adapter.sends) == 1
    assert "Goal achieved" in adapter.sends[0]["content"]


@pytest.mark.asyncio
async def test_visible_latest_turn_defers_status_until_adapter_delivery(hermes_home):
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli import goals
    from hermes_cli.goals import GoalManager

    callbacks = []

    def register_post_delivery_callback(_session_key, callback, generation=None):
        callbacks.append(callback)

    setattr(adapter, "register_post_delivery_callback", register_post_delivery_callback)
    GoalManager(session_entry.session_id).set("finish the live repair", max_turns=5)
    outcomes = [
        {
            "turn_exit_reason": "max_iterations_reached(200/200)",
            "final_response": "Partial checkpoint.",
            "response_already_delivered": True,
        },
        {
            "turn_exit_reason": "text_response(finish_reason=stop)",
            "final_response": "The standing goal is complete.",
            "response_already_delivered": False,
        },
    ]

    with patch.object(
        goals,
        "judge_goal",
        return_value=("done", "verified completion", False, None),
    ):
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response=outcomes[-1]["final_response"],
            turn_outcomes=outcomes,
        )

    assert adapter.sends == []
    assert len(callbacks) == 1
    await callbacks[0]()
    assert len(adapter.sends) == 1
    assert "Goal achieved" in adapter.sends[0]["content"]


@pytest.mark.asyncio
async def test_two_caps_account_twice_and_enqueue_only_once(hermes_home):
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli import goals
    from hermes_cli.goals import GoalManager

    GoalManager(session_entry.session_id).set("finish the live repair", max_turns=5)
    outcomes = [
        {
            "turn_exit_reason": "max_iterations_reached(200/200)",
            "final_response": "Checkpoint one.",
            "response_already_delivered": True,
        },
        {
            "turn_exit_reason": "max_iterations_reached(200/200)",
            "final_response": "Checkpoint two.",
            "response_already_delivered": True,
        },
    ]

    with patch.object(goals, "judge_goal") as judge:
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="NO_REPLY",
            turn_outcomes=outcomes,
        )

    judge.assert_not_called()
    state = goals.load_goal(session_entry.session_id)
    assert state is not None
    assert state.status == "active"
    assert state.turns_used == 2
    assert len(adapter._pending_messages) == 1
    assert len(adapter.sends) == 1


@pytest.mark.asyncio
async def test_cap_then_silent_callback_resumes_once_after_judging_latest(hermes_home):
    """Production incident shape: cap summary, then an intentional-silence callback."""
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli import goals
    from hermes_cli.goals import GoalManager

    GoalManager(session_entry.session_id).set("finish the live repair", max_turns=5)
    outcomes = [
        {
            "turn_exit_reason": "max_iterations_reached(200/200)",
            "final_response": "Partial checkpoint.",
            "response_already_delivered": True,
        },
        {
            "turn_exit_reason": "text_response(finish_reason=stop)",
            "final_response": "",
            "response_already_delivered": False,
            "delivery_suppressed": True,
        },
    ]

    with patch.object(
        goals,
        "judge_goal",
        return_value=("continue", "callback did not complete the goal", False, None),
    ) as judge:
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="",
            turn_outcomes=outcomes,
        )

    judge.assert_called_once()
    state = goals.load_goal(session_entry.session_id)
    assert state is not None
    assert state.status == "active"
    assert state.turns_used == 2
    assert len(adapter._pending_messages) == 1
    assert len(adapter.sends) == 1
    assert "Continuing toward goal" in adapter.sends[0]["content"]


@pytest.mark.asyncio
async def test_earlier_wait_does_not_mask_later_completed_queued_turn(hermes_home):
    """A wait set by one drained turn is not a barrier to later drained evidence."""
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli import goals
    from hermes_cli.goals import GoalManager

    GoalManager(session_entry.session_id).set("finish after the worker returns", max_turns=5)
    outcomes = [
        {
            "turn_exit_reason": "text_response(finish_reason=stop)",
            "final_response": "The build is still running.",
            "response_already_delivered": True,
        },
        {
            "turn_exit_reason": "text_response(finish_reason=stop)",
            "final_response": "The build passed and deployment is verified.",
            "response_already_delivered": False,
        },
    ]

    with patch.object(
        goals,
        "judge_goal",
        side_effect=[
            ("wait", "build still running", False, {"seconds": 60}),
            ("done", "deployment verified", False, None),
        ],
    ) as judge:
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response=outcomes[-1]["final_response"],
            turn_outcomes=outcomes,
        )

    assert judge.call_count == 2
    state = goals.load_goal(session_entry.session_id)
    assert state is not None
    assert state.status == "done"
    assert state.turns_used == 2
    assert not adapter._pending_messages
    assert len(adapter.sends) == 1


@pytest.mark.asyncio
async def test_goal_verdict_budget_exhausted_sends_pause(hermes_home):
    """When the budget is exhausted, a '⏸ Goal paused' message must be sent
    and no further continuation enqueued."""
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli.goals import GoalManager, save_goal

    mgr = GoalManager(session_entry.session_id, default_max_turns=2)
    state = mgr.set("tiny goal", max_turns=2)
    state.turns_used = 2
    save_goal(session_entry.session_id, state)

    with patch("hermes_cli.goals.judge_goal", return_value=("continue", "keep going", False, None, False)):
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="still partial",
        )
        await asyncio.sleep(0.05)

    assert len(adapter.sends) == 1
    content = adapter.sends[0]["content"]
    assert "paused" in content.lower()
    assert "turns used" in content.lower()
    # No continuation enqueued when budget is exhausted
    assert not adapter._pending_messages


