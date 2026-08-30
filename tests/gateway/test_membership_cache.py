"""Focused cache contract for Telegram team membership."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.telegram_team_membership import TelegramTeamMembershipPolicy


@pytest.mark.asyncio
async def test_positive_and_negative_results_are_cached_separately():
    lookup = AsyncMock(
        side_effect=[
            SimpleNamespace(status="member", user=SimpleNamespace(is_bot=False)),
            SimpleNamespace(status="left", user=SimpleNamespace(is_bot=False)),
        ]
    )
    policy = TelegramTeamMembershipPolicy(
        authority_chat_id="authority",
        get_chat_member=lookup,
        positive_ttl_seconds=30,
        negative_ttl_seconds=5,
    )

    assert (await policy.authorize(user_id="1", source_chat_id="1", source_chat_type="dm", sender_is_bot=False)).allowed
    assert (await policy.authorize(user_id="1", source_chat_id="1", source_chat_type="dm", sender_is_bot=False)).allowed
    assert not (await policy.authorize(user_id="2", source_chat_id="2", source_chat_type="dm", sender_is_bot=False)).allowed
    assert not (await policy.authorize(user_id="2", source_chat_id="2", source_chat_type="dm", sender_is_bot=False)).allowed
    assert lookup.await_count == 2


@pytest.mark.asyncio
async def test_invalidation_forces_fresh_lookup():
    lookup = AsyncMock(
        return_value=SimpleNamespace(status="member", user=SimpleNamespace(is_bot=False))
    )
    policy = TelegramTeamMembershipPolicy(
        authority_chat_id="authority", get_chat_member=lookup
    )
    await policy.authorize(user_id="1", source_chat_id="1", source_chat_type="dm", sender_is_bot=False)
    policy.invalidate("1")
    await policy.authorize(user_id="1", source_chat_id="1", source_chat_type="dm", sender_is_bot=False)
    assert lookup.await_count == 2
