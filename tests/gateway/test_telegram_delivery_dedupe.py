"""Synthetic ingress/final-delivery idempotency replays for P03."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    SendResult,
)
from gateway.session import SessionSource


class SyntheticTelegramAdapter(BasePlatformAdapter):
    def __init__(self) -> None:
        super().__init__(
            PlatformConfig(enabled=True, token="synthetic-token", typing_indicator=False),
            Platform.TELEGRAM,
        )
        self.sent: list[tuple[str, str]] = []

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def get_chat_info(self, chat_id: str) -> dict[str, str]:
        return {"id": chat_id}

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
        metadata: dict | None = None,
    ) -> SendResult:
        self.sent.append((chat_id, content))
        return SendResult(success=True, message_id=f"synthetic-{len(self.sent)}")


async def _settle(adapter: SyntheticTelegramAdapter) -> None:
    while adapter._background_tasks:
        await asyncio.gather(*tuple(adapter._background_tasks))
        # A task can finish just before gather() snapshots it. In that case
        # gather returns immediately, while BasePlatformAdapter's done callback
        # still needs one loop turn to remove the task from the tracking set.
        await asyncio.sleep(0)


def _event(
    *,
    profile: str = "profile-alpha",
    chat: str = "chat-alpha",
    thread: str = "thread-alpha",
    update_id: int | None = 41,
) -> MessageEvent:
    return MessageEvent(
        text="synthetic request",
        message_id="message-alpha",
        platform_update_id=update_id,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            profile=profile,
            chat_id=chat,
            chat_type="group",
            thread_id=thread,
            user_id="actor-alpha",
        ),
    )


@pytest.mark.asyncio
async def test_duplicate_update_executes_and_delivers_once() -> None:
    adapter = SyntheticTelegramAdapter()
    handler = AsyncMock(return_value="SYNTHETIC FINAL")
    adapter.set_message_handler(handler)

    await adapter.handle_message(_event())
    await _settle(adapter)
    await adapter.handle_message(_event())
    await _settle(adapter)

    assert handler.await_count == 1
    assert adapter.sent == [("chat-alpha", "SYNTHETIC FINAL")]


@pytest.mark.asyncio
async def test_update_dedupe_identity_is_scoped_by_profile_chat_and_thread() -> None:
    adapter = SyntheticTelegramAdapter()
    handler = AsyncMock(return_value="SYNTHETIC FINAL")
    adapter.set_message_handler(handler)

    events = [
        _event(profile="profile-alpha", chat="chat-alpha", thread="thread-alpha"),
        _event(profile="profile-beta", chat="chat-alpha", thread="thread-alpha"),
        _event(profile="profile-alpha", chat="chat-beta", thread="thread-alpha"),
        _event(profile="profile-alpha", chat="chat-alpha", thread="thread-beta"),
    ]
    for event in events:
        await adapter.handle_message(event)
        await _settle(adapter)

    assert handler.await_count == 4
    assert len(adapter.sent) == 4


@pytest.mark.asyncio
async def test_events_without_platform_update_id_are_not_deduped() -> None:
    adapter = SyntheticTelegramAdapter()
    handler = AsyncMock(return_value="SYNTHETIC FINAL")
    adapter.set_message_handler(handler)

    await adapter.handle_message(_event(update_id=None))
    await _settle(adapter)
    await adapter.handle_message(_event(update_id=None))
    await _settle(adapter)

    assert handler.await_count == 2
    assert len(adapter.sent) == 2


@pytest.mark.asyncio
async def test_concurrent_duplicate_update_is_claimed_atomically() -> None:
    adapter = SyntheticTelegramAdapter()
    handler = AsyncMock(return_value="SYNTHETIC FINAL")
    adapter.set_message_handler(handler)

    await asyncio.gather(
        adapter.handle_message(_event()),
        adapter.handle_message(_event()),
    )
    await _settle(adapter)

    assert handler.await_count == 1
    assert len(adapter.sent) == 1


@pytest.mark.asyncio
async def test_update_identity_window_is_bounded() -> None:
    adapter = SyntheticTelegramAdapter()
    adapter._update_dedupe_max_entries = 2
    handler = AsyncMock(return_value="SYNTHETIC FINAL")
    adapter.set_message_handler(handler)

    for update_key in (1, 2, 3):
        await adapter.handle_message(_event(update_id=update_key))
        await _settle(adapter)

    assert len(adapter._recent_update_keys) == 2

    await adapter.handle_message(_event(update_id=1))
    await _settle(adapter)

    assert handler.await_count == 4
