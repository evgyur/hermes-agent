"""Membership removal cancels actor-local runtime state, not durable work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.platforms.telegram import TelegramAdapter


class PendingTask:
    def __init__(self):
        self.cancelled = False

    def done(self):
        return False

    def cancel(self):
        self.cancelled = True


@pytest.mark.asyncio
async def test_removal_cancels_active_and_pre_session_queues():
    adapter = object.__new__(TelegramAdapter)
    actor_task = object()
    adapter._team_actor_active_tasks = {"42": {"telegram:42": actor_task}}
    adapter._team_session_task_owner = {"telegram:42": ("42", actor_task)}
    adapter._session_tasks = {"telegram:42": actor_task}
    adapter._pending_messages = {}
    adapter._text_debounce = {}
    adapter._discard_text_debounce = Mock()
    adapter.cancel_session_processing = AsyncMock()

    text_task = PendingTask()
    photo_task = PendingTask()
    media_task = PendingTask()
    actor_event = SimpleNamespace(source=SimpleNamespace(user_id="42"))
    other_event = SimpleNamespace(source=SimpleNamespace(user_id="7"))
    adapter._pending_text_batches = {"actor": actor_event, "other": other_event}
    adapter._pending_text_batch_tasks = {"actor": text_task}
    adapter._pending_photo_batches = {"actor-photo": actor_event}
    adapter._pending_photo_batch_tasks = {"actor-photo": photo_task}
    adapter._media_group_events = {"actor-media": actor_event}
    adapter._media_group_tasks = {"actor-media": media_task}

    await adapter._cancel_team_actor_sessions("42")

    adapter.cancel_session_processing.assert_awaited_once_with(
        "telegram:42", discard_pending=True
    )
    assert "other" in adapter._pending_text_batches
    assert "actor" not in adapter._pending_text_batches
    assert text_task.cancelled and photo_task.cancelled and media_task.cancelled


@pytest.mark.asyncio
async def test_chat_member_removal_invalidates_then_cancels_actor():
    adapter = object.__new__(TelegramAdapter)
    policy = SimpleNamespace(
        authority_chat_id="authority",
        invalidate=Mock(),
        member_is_allowed=Mock(return_value=False),
    )
    adapter._team_membership_policy = policy
    adapter._cancel_team_actor_sessions = AsyncMock()
    member = SimpleNamespace(user=SimpleNamespace(id=42, is_bot=False), status="left")
    update = SimpleNamespace(
        chat_member=SimpleNamespace(
            chat=SimpleNamespace(id="authority"),
            new_chat_member=member,
        )
    )

    await adapter._handle_team_chat_member_update(update, SimpleNamespace())

    policy.invalidate.assert_called_once_with(42)
    adapter._cancel_team_actor_sessions.assert_awaited_once_with("42")


@pytest.mark.asyncio
async def test_unrelated_membership_update_has_no_effect():
    adapter = object.__new__(TelegramAdapter)
    policy = SimpleNamespace(authority_chat_id="authority", invalidate=Mock())
    adapter._team_membership_policy = policy
    adapter._cancel_team_actor_sessions = AsyncMock()
    update = SimpleNamespace(
        chat_member=SimpleNamespace(
            chat=SimpleNamespace(id="other"),
            new_chat_member=SimpleNamespace(
                user=SimpleNamespace(id=42, is_bot=False), status="left"
            ),
        )
    )

    await adapter._handle_team_chat_member_update(update, SimpleNamespace())

    policy.invalidate.assert_not_called()
    adapter._cancel_team_actor_sessions.assert_not_awaited()
