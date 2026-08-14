from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionEntry, SessionSource, build_session_key


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _session_entry() -> SessionEntry:
    src = _source()
    return SessionEntry(
        session_key=build_session_key(src),
        session_id="blocked-goal-session",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
        total_tokens=0,
    )


def _runner():
    from gateway.run import GatewayRunner

    runner = cast(Any, object.__new__(GatewayRunner))
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter._pending_messages = {}
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = _session_entry()
    runner._session_db = None
    runner._update_prompt_pending = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._queued_events = {}
    runner._is_user_authorized = lambda _source: True
    runner._handle_message_with_agent = AsyncMock(return_value={"final_response": "should not run"})
    runner._goal_still_active_for_session = MagicMock(return_value=False)
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    return runner


@pytest.mark.asyncio
async def test_stale_internal_goal_continuation_is_dropped_before_agent_run():
    runner = _runner()
    event = MessageEvent(
        text="[Continuing toward your standing goal]\nGoal: deploy only after approval",
        message_type=MessageType.TEXT,
        source=_source(),
        internal=True,
    )

    result = await runner._handle_message(event)

    assert result is None
    runner._goal_still_active_for_session.assert_called_once_with("blocked-goal-session")
    runner._handle_message_with_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_genuine_user_turn_pauses_active_goal_before_agent_run():
    from hermes_cli.goals import GoalManager

    runner = _runner()
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        get_or_create_session=AsyncMock(return_value=_session_entry()),
    )
    runner._clear_goal_pending_continuations = MagicMock()
    runner._goal_max_turns_from_config = MagicMock(return_value=20)
    GoalManager("blocked-goal-session").set("old standing goal")

    paused = await runner._pause_active_goal_for_user_turn(
        source=_source(),
        session_key="telegram:c1:u1",
        user_initiated=True,
    )

    state = GoalManager("blocked-goal-session").state
    assert paused is True
    assert state is not None
    assert state.status == "paused"
    assert state.paused_reason == "new-user-request"
    runner._clear_goal_pending_continuations.assert_called_once()


@pytest.mark.asyncio
async def test_synthetic_continuation_does_not_pause_active_goal():
    from hermes_cli.goals import GoalManager

    runner = _runner()
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        get_or_create_session=AsyncMock(return_value=_session_entry()),
    )
    GoalManager("blocked-goal-session").set("standing goal")

    paused = await runner._pause_active_goal_for_user_turn(
        source=_source(),
        session_key="telegram:c1:u1",
        user_initiated=False,
    )

    assert paused is False
    assert GoalManager("blocked-goal-session").is_active() is True
