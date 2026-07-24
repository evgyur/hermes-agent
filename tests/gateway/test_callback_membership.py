"""Callback authorization must use current authority-group membership."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.platforms.telegram import TelegramAdapter


@pytest.mark.asyncio
async def test_callback_is_silently_rejected_before_family_dispatch():
    adapter = object.__new__(TelegramAdapter)
    adapter._team_membership_allows_callback = AsyncMock(return_value=False)
    adapter._handle_gptprof_callback = AsyncMock()
    query = SimpleNamespace(
        data="gptprof:refresh",
        from_user=SimpleNamespace(id=42, is_bot=False),
        message=SimpleNamespace(chat_id=42, chat=SimpleNamespace(id=42, type="private")),
        answer=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)

    await adapter._handle_callback_query(update, SimpleNamespace())

    adapter._team_membership_allows_callback.assert_awaited_once_with(query)
    adapter._handle_gptprof_callback.assert_not_awaited()
    query.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_lookup_uses_actor_and_authority_policy():
    adapter = object.__new__(TelegramAdapter)
    adapter._team_membership_policy = SimpleNamespace(
        authorize=AsyncMock(return_value=SimpleNamespace(allowed=True))
    )
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42, is_bot=False),
        message=SimpleNamespace(chat=SimpleNamespace(id=-100, type="supergroup")),
    )

    assert await adapter._team_membership_allows_callback(query) is True
    adapter._team_membership_policy.authorize.assert_awaited_once_with(
        user_id=42,
        source_chat_id=-100,
        source_chat_type="group",
        sender_is_bot=False,
    )


def test_sync_callback_family_guard_cannot_fall_back_to_static_auth():
    adapter = object.__new__(TelegramAdapter)
    adapter._team_membership_policy = SimpleNamespace(
        cached_decision=Mock(return_value=SimpleNamespace(allowed=False))
    )
    adapter._message_handler = Mock()

    assert adapter._is_callback_user_authorized("42") is False
    adapter._message_handler.assert_not_called()
