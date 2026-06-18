"""Gateway message processing ledger tests.

The ledger is intentionally additive: it records lifecycle metadata for restart
recovery without changing user-visible gateway behavior or persisting full
message bodies outside the normal transcript.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import build_session_key
from hermes_state import SessionDB
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


@pytest.fixture
def ledger_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        yield db
    finally:
        db.close()


def _runner_with_ledger(db: Any) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner._session_db = db
    return runner


def _event(*, text="hello", message_id: str | None = "m1", internal=False):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=make_restart_source(chat_id="chat-1"),
        message_id=message_id,
        platform_update_id=777,
        internal=internal,
    )


def test_ledger_schema_and_direct_api_transition(ledger_db):
    ledger_id = ledger_db.record_gateway_message_received(
        platform="telegram",
        chat_id="chat-1",
        thread_id="topic-1",
        message_id="msg-1",
        user_id="user-1",
        session_key="agent:main:telegram:dm:chat-1",
        session_id="sid-1",
        origin_type="real_user",
        snippet="x" * 500,
        metadata={"message_type": "text"},
    )

    row = ledger_db.get_gateway_message_ledger(ledger_id)
    assert row is not None
    assert row["status"] == "received"
    assert row["platform"] == "telegram"
    assert row["chat_id"] == "chat-1"
    assert row["thread_id"] == "topic-1"
    assert row["message_id"] == "msg-1"
    assert row["origin_type"] == "real_user"
    assert len(row["snippet"]) == 240
    assert row["metadata"] == {"message_type": "text"}

    assert ledger_db.update_gateway_message_ledger(
        ledger_id,
        status="in_progress",
        session_key="agent:main:telegram:dm:chat-1",
        session_id="sid-1",
        reason="claimed",
    )
    assert ledger_db.update_gateway_message_ledger(
        ledger_id,
        status="completed",
        reason="done",
    )
    row = ledger_db.get_gateway_message_ledger(ledger_id)
    assert row["status"] == "completed"
    assert row["dispatch_started_at"] is not None
    assert row["completed_at"] is not None
    assert row["drained_at"] is None
    assert row["failed_at"] is None

    found = ledger_db.find_gateway_message_ledger(
        platform="telegram",
        chat_id="chat-1",
        thread_id="topic-1",
        message_id="msg-1",
    )
    assert found["id"] == ledger_id


@pytest.mark.asyncio
async def test_handle_message_wires_real_user_received_in_progress_completed(ledger_db):
    runner, _adapter = make_restart_runner()
    runner._session_db = ledger_db
    setattr(runner.session_store, "_generate_session_key", lambda source: build_session_key(source))
    setattr(runner, "_claim_active_session_slot", lambda session_key, source: (None, None))
    setattr(runner, "_begin_session_run_generation", lambda session_key: 1)
    setattr(runner, "_release_running_agent_state", lambda session_key, run_generation=None: True)
    setattr(runner, "_is_telegram_topic_root_lobby", lambda source: False)
    runner._handle_message_with_agent = AsyncMock(
        return_value={"final_response": "done", "completed": True, "session_id": "sid-real"}
    )
    runner._auto_dispatch_supergoal_from_response = AsyncMock(return_value=False)
    runner._post_turn_goal_continuation = AsyncMock()

    event = _event(text="please check this", message_id="msg-real")
    result = await runner._handle_message(event)

    assert isinstance(result, dict)
    assert result["final_response"] == "done"
    row = ledger_db.find_gateway_message_ledger(
        platform=Platform.TELEGRAM.value,
        chat_id="chat-1",
        thread_id=None,
        message_id="msg-real",
    )
    assert row is not None
    assert row["status"] == "completed"
    assert row["origin_type"] == "real_user"
    assert row["session_key"].startswith("agent:main:telegram")
    assert row["session_id"] == "sid-real"
    assert row["dispatch_started_at"] is not None
    assert row["completed_at"] is not None
    assert "please check this" in row["snippet"]


def test_interrupted_turn_marks_drained_not_completed(ledger_db):
    runner = _runner_with_ledger(ledger_db)
    event = _event(text="long task", message_id="msg-drain")
    session_key = "agent:main:telegram:dm:chat-1"

    runner._record_gateway_ledger_received(event, session_key=session_key, session_id="sid-drain")
    runner._update_gateway_ledger(event, "in_progress", session_key=session_key, session_id="sid-drain")
    runner._mark_gateway_ledger_after_agent_result(
        event,
        {"interrupted": True, "session_id": "sid-drain"},
        session_key=session_key,
    )

    row = ledger_db.find_gateway_message_ledger(
        platform="telegram",
        chat_id="chat-1",
        message_id="msg-drain",
    )
    assert row["status"] == "drained"
    assert row["drained_at"] is not None
    assert row["completed_at"] is None


def test_internal_goal_and_startup_recovery_origins_are_distinguishable(ledger_db):
    runner = _runner_with_ledger(ledger_db)
    goal_text = "[Continuing toward your standing goal]\nGoal: SECRET full private task body"
    goal_event = _event(text=goal_text, message_id=None)
    startup_event = _event(text="", message_id=None, internal=True)

    goal_id = runner._record_gateway_ledger_received(goal_event, session_key="sk-goal")
    startup_id = runner._record_gateway_ledger_received(startup_event, session_key="sk-start")

    goal_row = ledger_db.get_gateway_message_ledger(goal_id)
    startup_row = ledger_db.get_gateway_message_ledger(startup_id)
    assert goal_row["origin_type"] == "internal_goal"
    assert goal_row["snippet"] == "[internal goal continuation]"
    assert "SECRET" not in goal_row["snippet"]
    assert startup_row["origin_type"] == "startup_recovery"
    assert startup_row["snippet"] == "[startup recovery continuation]"


def test_session_drain_bulk_update_leaves_completed_rows_alone(ledger_db):
    session_key = "agent:main:telegram:dm:chat-1"
    active_id = ledger_db.record_gateway_message_received(
        platform="telegram",
        chat_id="chat-1",
        message_id="active",
        session_key=session_key,
    )
    done_id = ledger_db.record_gateway_message_received(
        platform="telegram",
        chat_id="chat-1",
        message_id="done",
        session_key=session_key,
    )
    ledger_db.update_gateway_message_ledger(active_id, status="in_progress")
    ledger_db.update_gateway_message_ledger(done_id, status="completed")

    changed = ledger_db.mark_gateway_session_messages_drained(session_key, reason="restart_timeout")

    assert changed == 1
    assert ledger_db.get_gateway_message_ledger(active_id)["status"] == "drained"
    assert ledger_db.get_gateway_message_ledger(done_id)["status"] == "completed"


def test_ledger_writes_are_best_effort_and_do_not_crash_gateway():
    class BrokenDB:
        def record_gateway_message_received(self, **_kwargs):
            raise RuntimeError("db down")

        def update_gateway_message_ledger(self, **_kwargs):
            raise RuntimeError("db down")

        def mark_gateway_session_messages_drained(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    runner = _runner_with_ledger(BrokenDB())
    event = _event(text="hello", message_id="broken")

    assert runner._record_gateway_ledger_received(event) is None
    assert runner._update_gateway_ledger(event, "in_progress") is False
    runner._mark_gateway_ledger_session_drained("sk", reason="restart_timeout")
