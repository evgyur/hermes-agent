"""Gateway delivery half of the deterministic pre-tool acknowledgment."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from gateway.run import (
    GatewayRunner,
    TurnRunner,
    _resolve_start_ack_policy,
    _resolve_start_ack_text,
)
from gateway.session import SessionSource
from gateway.turn_context import TurnContext
from plugins.platforms.telegram.adapter import TelegramAdapter


@pytest.mark.asyncio
async def test_start_ack_preserves_topic_reply_metadata_and_tracks_cleanup():
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="ack-42"))
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-100123",
        chat_type="group",
        thread_id="1858",
    )
    gateway_runner = object.__new__(GatewayRunner)
    metadata = gateway_runner._thread_metadata_for_source(
        source, reply_to_message_id="47289"
    )
    assert metadata == {
        "thread_id": "1858",
        "telegram_reply_to_message_id": "47289",
    }
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "↳ Принял. Начинаю проверку."
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._status_thread_metadata = metadata
    ctx._loop_for_step = asyncio.get_running_loop()
    ctx.event_message_id = "47289"
    ctx._cleanup_progress = True
    runner = TurnRunner(SimpleNamespace(), ctx)

    delivered = await asyncio.to_thread(runner.start_ack_callback)

    assert delivered is True
    adapter.send.assert_awaited_once_with(
        source.chat_id,
        "↳ Принял. Начинаю проверку.",
        reply_to="47289",
        metadata={
            **metadata,
            "_interim_send": True,
        },
    )
    assert ctx._cleanup_msg_ids == ["ack-42"]


@pytest.mark.asyncio
async def test_start_ack_fails_open_when_delivery_fails():
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=False, error="offline"))
    )
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "↳ Принял. Начинаю проверку."
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    runner = TurnRunner(SimpleNamespace(), ctx)

    assert await asyncio.to_thread(runner.start_ack_callback) is False
    assert ctx._cleanup_msg_ids == []


@pytest.mark.asyncio
async def test_start_ack_timeout_cancels_blocked_send_without_late_delivery():
    started = asyncio.Event()
    cancelled = asyncio.Event()
    delivered = []

    async def blocked_send(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        delivered.append(True)

    adapter = SimpleNamespace(send=blocked_send)
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "configured"
    ctx.start_ack_timeout_s = 0.02
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    runner = TurnRunner(SimpleNamespace(), ctx)

    assert await asyncio.to_thread(runner.start_ack_callback) is False
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    await asyncio.sleep(0)
    assert started.is_set()
    assert delivered == []


@pytest.mark.parametrize(
    "platform,kwargs",
    [
        (Platform.WEBHOOK, {}),
        (Platform.TELEGRAM, {"startup_resume": True}),
        (Platform.TELEGRAM, {"trusted_restart_wake": object()}),
        (Platform.TELEGRAM, {"trusted_parent_task_continuation": object()}),
        (
            Platform.TELEGRAM,
            {"persist_user_display_kind": "internal_notification"},
        ),
    ],
)
def test_start_ack_is_suppressed_for_non_user_turn_origins(platform, kwargs):
    config = {
        "display": {
            "platforms": {
                "telegram": {"start_ack_text": "configured"},
                "webhook": {"start_ack_text": "configured"},
            }
        }
    }

    assert _resolve_start_ack_text(config, platform.value, platform, **kwargs) == ""


def test_start_ack_is_enabled_for_an_ordinary_configured_telegram_turn():
    config = {
        "display": {
            "platforms": {"telegram": {"start_ack_text": "  configured  "}}
        }
    }

    assert (
        _resolve_start_ack_text(config, "telegram", Platform.TELEGRAM)
        == "configured"
    )


def test_required_policy_cannot_silently_downgrade_with_empty_text():
    config = {
        "display": {
            "platforms": {
                "telegram": {"start_ack_mode": "required", "start_ack_text": ""}
            }
        }
    }

    with pytest.raises(ValueError, match="must be non-empty"):
        _resolve_start_ack_policy(config, "telegram", Platform.TELEGRAM)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply_mode,expected_reply",
    [("first", 47289), ("off", None)],
)
async def test_start_ack_reaches_real_telegram_forum_wire(
    reply_mode, expected_reply
):
    adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="test-token", reply_to_mode=reply_mode)
    )
    adapter._bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=99))
    )
    adapter._rich_messages_enabled = False
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-100123",
        chat_type="group",
        thread_id="1858",
    )
    gateway_runner = object.__new__(GatewayRunner)
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "configured"
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._status_thread_metadata = gateway_runner._thread_metadata_for_source(
        source, reply_to_message_id="47289"
    )
    ctx.event_message_id = "47289"
    ctx._loop_for_step = asyncio.get_running_loop()

    assert await asyncio.to_thread(TurnRunner(SimpleNamespace(), ctx).start_ack_callback)
    wire = adapter._bot.send_message.await_args.kwargs
    assert wire["chat_id"] == -100123
    assert wire["message_thread_id"] == 1858
    assert wire["reply_to_message_id"] == expected_reply
