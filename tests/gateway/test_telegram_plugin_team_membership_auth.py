"""Runtime-plugin contract tests for Telegram team membership authorization."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
async def test_plugin_raw_text_denial_precedes_ingress_side_effects():
    from plugins.platforms.telegram.adapter import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    msg = SimpleNamespace(text="hello")
    adapter._effective_update_message = Mock(return_value=msg)
    adapter._team_membership_allows_message = AsyncMock(return_value=False)
    adapter._is_native_voice_transcript_followup = Mock()

    await TelegramAdapter._handle_text_message(
        adapter, SimpleNamespace(update_id=1), SimpleNamespace()
    )

    adapter._team_membership_allows_message.assert_awaited_once_with(msg)
    adapter._is_native_voice_transcript_followup.assert_not_called()


@pytest.mark.asyncio
async def test_plugin_negative_source_stops_before_base_session_state(monkeypatch):
    from gateway.config import Platform
    from gateway.platforms.base import BasePlatformAdapter
    from gateway.session import SessionSource
    from plugins.platforms.telegram.adapter import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter._team_membership_policy = SimpleNamespace(
        authorize=AsyncMock(
            return_value=SimpleNamespace(
                allowed=False,
                reason="not_current_member",
            )
        )
    )
    base_handle = AsyncMock()
    monkeypatch.setattr(BasePlatformAdapter, "handle_message", base_handle)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="actor-1",
        user_id="actor-1",
    )

    result = await TelegramAdapter.handle_message(
        adapter, SimpleNamespace(source=source)
    )

    assert result is None
    assert source.telegram_team_membership_required is True
    assert source.telegram_team_membership_authorized is False
    assert source.telegram_team_membership_reason == "not_current_member"
    base_handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_plugin_callback_denial_precedes_callback_dispatch():
    from plugins.platforms.telegram.adapter import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    query = SimpleNamespace(data="model:x")
    adapter._team_membership_allows_callback = AsyncMock(return_value=False)
    update = SimpleNamespace(callback_query=query)

    result = await TelegramAdapter._handle_callback_query(
        adapter, update, SimpleNamespace()
    )

    assert result is None
    adapter._team_membership_allows_callback.assert_awaited_once_with(query)


@pytest.mark.asyncio
async def test_plugin_member_removal_invalidates_and_cancels_actor():
    from plugins.platforms.telegram.adapter import TelegramAdapter

    policy = SimpleNamespace(
        authority_chat_id="authority",
        invalidate=Mock(),
        member_is_allowed=Mock(return_value=False),
    )
    adapter = object.__new__(TelegramAdapter)
    adapter._team_membership_policy = policy
    adapter._cancel_team_actor_sessions = AsyncMock()
    member = SimpleNamespace(user=SimpleNamespace(id=244, is_bot=False))
    change = SimpleNamespace(
        chat=SimpleNamespace(id="authority"),
        new_chat_member=member,
    )

    await TelegramAdapter._handle_team_chat_member_update(
        adapter,
        SimpleNamespace(chat_member=change),
        SimpleNamespace(),
    )

    policy.invalidate.assert_called_once_with(244)
    adapter._cancel_team_actor_sessions.assert_awaited_once_with("244")


def test_plugin_exposes_runtime_membership_control_surface():
    from plugins.platforms.telegram.adapter import TelegramAdapter

    for name in (
        "_authorize_team_source",
        "_team_membership_allows_message",
        "_team_membership_allows_callback",
        "_handle_team_chat_member_update",
        "_cancel_team_actor_sessions",
    ):
        assert callable(getattr(TelegramAdapter, name, None)), name
