"""Regression coverage for same-route queue handoff authority.

Two Telegram messages can arrive in the same event-loop window.  The second
message must join the already-owned turn chain; it must never start a second
turn or reacquire the durable session lease from its own outer owner.
"""

from dataclasses import replace
from datetime import datetime, timezone
import importlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource
from tests.gateway.test_42039_duplicate_user_message import _bootstrap, _event
from tests.gateway.test_run_progress_topics import (
    ProgressCaptureAdapter,
    _make_runner,
)


class _TwoTurnAgent:
    calls = 0

    def __init__(self, **_kwargs):
        self.tools = []

    def run_conversation(
        self,
        message,
        conversation_history=None,
        task_id=None,
        **_kwargs,
    ):
        type(self).calls += 1
        return {
            "final_response": f"done-{type(self).calls}",
            "messages": [],
            "api_calls": 1,
        }


@pytest.mark.asyncio
async def test_late_local_claim_race_rejoins_busy_fifo(monkeypatch, tmp_path):
    """A handler that loses the final local claim must queue, not run."""

    runner = _bootstrap(monkeypatch, tmp_path)
    event = _event()
    runner._claim_active_session_slot = MagicMock(
        return_value=(
            importlib.import_module("gateway.run")._ACTIVE_SESSION_ALREADY_RUNNING,
            None,
        )
    )
    runner._handle_active_session_busy_message = AsyncMock(return_value=False)
    runner._queue_or_replace_pending_event = MagicMock(return_value=True)
    runner._handle_message_with_agent = AsyncMock(
        side_effect=AssertionError("late claimant must not start an agent")
    )

    assert await runner._handle_message(event) is None

    key = "agent:main:telegram:group:-1001:12345"
    runner._handle_active_session_busy_message.assert_awaited_once_with(event, key)
    runner._queue_or_replace_pending_event.assert_called_once_with(key, event)
    runner._handle_message_with_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_queued_followup_reuses_authority_and_exact_ingress_identity(
    monkeypatch, tmp_path
):
    """The in-band follow-up inherits the lease but keeps its own message ID."""

    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    _TwoTurnAgent.calls = 0
    fake_run_agent.AIAgent = _TwoTurnAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    adapter = ProgressCaptureAdapter(platform=Platform.TELEGRAM)
    runner = _make_runner(adapter)
    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {"api_key": "***"},
    )

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003971448755",
        chat_type="group",
        thread_id="47770",
        user_id="617744661",
        message_id="47773",
    )
    session_key = "agent:main:telegram:group:-1003971448755:47770"
    queued_at = datetime(2026, 8, 25, 10, 52, 27, tzinfo=timezone.utc)
    queued_event = MessageEvent(
        text="part two",
        message_type=MessageType.TEXT,
        # The route carrier can be copied from the running turn before the
        # adapter's exact inbound ID is attached.  The immutable PTB message
        # and MessageEvent both identify the real queued follow-up.
        source=replace(source),
        raw_message=SimpleNamespace(message_id=47774),
        message_id="47774",
        reply_to_message_id="47770",
        timestamp=queued_at,
    )
    assert runner._queue_or_replace_pending_event(
        session_key,
        queued_event,
    ) is True
    authority = {
        "session_id": "session-exact",
        "holder": "gateway-holder-exact",
        "ttl_seconds": 300.0,
        "released": False,
        "lost": False,
    }

    original_run_agent = runner._run_agent
    calls = []

    async def recording_run_agent(*args, **kwargs):
        calls.append(dict(kwargs))
        return await original_run_agent(*args, **kwargs)

    runner._run_agent = recording_run_agent

    result = await runner._run_agent(
        message="part one",
        context_prompt="",
        history=[],
        source=source,
        session_id="session-exact",
        session_key=session_key,
        durable_turn_authority=authority,
    )

    assert result["final_response"] == "done-2"
    assert len(calls) == 2
    followup = calls[1]
    assert followup["durable_turn_authority"] is authority
    assert followup["persist_user_message_id"] == "47774"
    assert followup["persist_user_timestamp"] == pytest.approx(
        queued_at.timestamp()
    )
    # Forum topics route through the exact source.thread_id; they must not
    # reply to the topic-root envelope as if it were semantic content.
    assert followup["source"].thread_id == "47770"
    assert followup["event_message_id"] is None


def test_queued_followup_rejects_unverifiable_message_identity() -> None:
    """A mismatched source cannot be repaired without exact adapter evidence."""

    adapter = ProgressCaptureAdapter(platform=Platform.TELEGRAM)
    runner = _make_runner(adapter)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003770669948",
        chat_type="group",
        thread_id="5413",
        user_id="617744661",
        message_id="22943",
    )
    session_key = "agent:main:telegram:group:-1003770669948:5413"
    event = MessageEvent(
        text="next correction",
        message_type=MessageType.TEXT,
        source=source,
        raw_message=SimpleNamespace(message_id=99999),
        message_id="22948",
    )

    assert runner._queue_or_replace_pending_event(session_key, event) is False
    assert session_key not in adapter._pending_messages
