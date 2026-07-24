"""Team membership authorization must also govern the active-session busy path."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType, SessionSource, build_session_key
from gateway.run import GatewayRunner


def _event(*, actor: str, authorized: bool, text: str = "synthetic input") -> MessageEvent:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="synthetic-team-chat",
        chat_type="forum",
        user_id=actor,
        user_name="Synthetic Actor",
        thread_id="synthetic-team-thread",
        profile="human20team",
    )
    source.telegram_team_membership_required = True
    source.telegram_team_membership_authorized = authorized
    source.is_bot = False
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
        message_id="synthetic-message",
    )


def _runner_and_adapter():
    runner = object.__new__(GatewayRunner)
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._busy_ack_ts = {}
    runner._draining = False
    runner._busy_input_mode = "interrupt"
    runner.config = MagicMock()
    runner.session_store = None
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = True

    adapter = MagicMock()
    adapter._team_membership_policy = object()
    adapter._pending_messages = {}
    adapter._send_with_retry = AsyncMock()
    adapter.config = MagicMock()
    adapter.config.extra = {}
    adapter.platform = Platform.TELEGRAM
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._profile_adapters = {"human20team": {Platform.TELEGRAM: adapter}}
    return runner, adapter


@pytest.mark.asyncio
async def test_revoked_team_actor_cannot_interrupt_or_queue_into_busy_session() -> None:
    runner, adapter = _runner_and_adapter()
    event = _event(actor="revoked-actor", authorized=False)
    session_key = build_session_key(
        event.source,
        group_sessions_per_user=False,
        thread_sessions_per_user=False,
        profile=event.source.profile,
    )
    running_agent = MagicMock()
    runner._running_agents[session_key] = running_agent

    result = await GatewayRunner._handle_active_session_busy_message(
        runner,
        event,
        session_key,
    )

    assert result is True
    assert session_key not in adapter._pending_messages
    running_agent.interrupt.assert_not_called()
    running_agent.steer.assert_not_called()
    adapter._send_with_retry.assert_not_called()


@pytest.mark.asyncio
async def test_current_team_actor_can_queue_into_own_busy_session() -> None:
    runner, adapter = _runner_and_adapter()
    event = _event(actor="current-actor", authorized=True)
    session_key = build_session_key(
        event.source,
        group_sessions_per_user=False,
        thread_sessions_per_user=False,
        profile=event.source.profile,
    )
    running_agent = MagicMock()
    running_agent.get_activity_summary.return_value = {}
    runner._running_agents[session_key] = running_agent
    runner._running_agents_ts[session_key] = time.time()

    result = await GatewayRunner._handle_active_session_busy_message(
        runner,
        event,
        session_key,
    )

    assert result is True
    assert session_key in adapter._pending_messages
