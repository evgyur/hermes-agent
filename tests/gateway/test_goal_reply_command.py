"""Tests for setting /goal from reply context in gateway platforms."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
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


class RecordingAdapter:
    def __init__(self) -> None:
        self._pending_messages: dict[str, MessageEvent] = {}


@pytest.fixture()
def runner():
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="group",
        thread_id="120",
    )
    session_entry = SessionEntry(
        session_key=build_session_key(source),
        session_id=f"goal-reply-session-{uuid.uuid4().hex[:8]}",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="group",
    )

    r = object.__new__(GatewayRunner)
    r.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
    )
    r.adapters = {Platform.TELEGRAM: RecordingAdapter()}
    r._queued_events = {}
    r.session_store = MagicMock()
    r.session_store.get_or_create_session.return_value = session_entry
    r.session_store._generate_session_key.return_value = session_entry.session_key
    return SimpleNamespace(runner=r, adapter=r.adapters[Platform.TELEGRAM], source=source, session=session_entry)


@pytest.mark.asyncio
async def test_bare_goal_reply_uses_replied_to_text_as_goal(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-2",
        reply_to_message_id="plan-1",
        reply_to_text="Implement the approved Project Storm plan with tests.",
    )

    response = await runner.runner._handle_goal_command(event)

    assert "Goal set" in response
    assert "Implement the approved Project Storm plan" in response

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert state is not None
    assert state.status == "active"
    assert state.goal == "Implement the approved Project Storm plan with tests."

    queued = runner.adapter._pending_messages[runner.session.session_key]
    assert queued.text == state.goal
    assert queued.source == runner.source


@pytest.mark.asyncio
async def test_bare_goal_without_reply_remains_status(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-1",
    )

    response = await runner.runner._handle_goal_command(event)

    assert "No active goal" in response
    assert runner.adapter._pending_messages == {}


@pytest.mark.asyncio
async def test_goal_status_reply_does_not_replace_goal(hermes_home, runner):
    from hermes_cli.goals import GoalManager

    GoalManager(runner.session.session_id).set("existing goal")
    event = MessageEvent(
        text="/goal status",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-3",
        reply_to_message_id="plan-1",
        reply_to_text="This text must not replace the active goal.",
    )

    response = await runner.runner._handle_goal_command(event)

    assert "existing goal" in response
    assert "This text must not replace" not in response
    assert GoalManager(runner.session.session_id).state.goal == "existing goal"
