"""Focused regression tests for standalone Telegram egress policy."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

telegram = pytest.importorskip(
    "telegram", reason="python-telegram-bot not installed"
)

from gateway.config import Platform
from plugins.platforms.telegram.adapter import TelegramAdapter as _TelegramAdapter
from tools.send_message_tool import (
    SEND_MESSAGE_SCHEMA,
    _send_telegram,
    _send_to_platform,
)

assert _TelegramAdapter  # Import before patching telegram.Bot in focused tests.


@pytest.fixture(autouse=True)
def _isolated_telegram_egress_registry(tmp_path, monkeypatch):
    """Keep these tests independent of the operator's live deny registry."""

    monkeypatch.setenv(
        "HERMES_TELEGRAM_EGRESS_DENY_FILE",
        str(tmp_path / "missing-deny-registry.json"),
    )
    monkeypatch.delenv("HERMES_TELEGRAM_EGRESS_DENY_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_PROXY", raising=False)


def _bot_with_successful_senders() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    bot.send_photo = AsyncMock(return_value=SimpleNamespace(message_id=2))
    bot.send_video = AsyncMock(return_value=SimpleNamespace(message_id=3))
    bot.send_voice = AsyncMock(return_value=SimpleNamespace(message_id=4))
    bot.send_audio = AsyncMock(return_value=SimpleNamespace(message_id=5))
    bot.send_document = AsyncMock(return_value=SimpleNamespace(message_id=6))
    return bot


def _business_route(
    chat_id: str,
    connection_id: str = "bc-exact-123",
    *,
    thread_id: str | None = None,
) -> dict:
    return {
        "version": 1,
        "platform": "telegram",
        "runtime_profile": "default",
        "transport_profile": "default",
        "chat_id": chat_id,
        "thread_id": thread_id,
        "user_id": "owner-1",
        "business_connection_id": connection_id,
        "external_safe_mode": True,
    }


def test_send_message_schema_exposes_explicit_route_envelope() -> None:
    route_schema = SEND_MESSAGE_SCHEMA["parameters"]["properties"]["route_envelope"]

    assert route_schema["type"] == "object"


def test_denied_plain_target_has_zero_text_wire_calls(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_TELEGRAM_EGRESS_DENY_IDS", "268754981")
    bot = _bot_with_successful_senders()
    bot_factory = MagicMock(return_value=bot)
    monkeypatch.setattr(telegram, "Bot", bot_factory)

    result = asyncio.run(_send_telegram("token", "268754981", "must not send"))

    assert result == {"error": "Telegram send failed: telegram_recipient_denied"}
    bot_factory.assert_not_called()
    bot.send_message.assert_not_awaited()


def test_denied_plain_target_has_zero_media_wire_calls(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_TELEGRAM_EGRESS_DENY_IDS", "268754981")
    media_path = tmp_path / "blocked.txt"
    media_path.write_text("blocked", encoding="utf-8")
    bot = _bot_with_successful_senders()
    bot_factory = MagicMock(return_value=bot)
    monkeypatch.setattr(telegram, "Bot", bot_factory)

    result = asyncio.run(
        _send_telegram(
            "token",
            "268754981",
            "",
            media_files=[(str(media_path), False)],
        )
    )

    assert result == {"error": "Telegram send failed: telegram_recipient_denied"}
    bot_factory.assert_not_called()
    bot.send_document.assert_not_awaited()


def test_business_connection_survives_text_retry_via_dispatch(monkeypatch) -> None:
    chat_id = "123456789"
    connection_id = "bc-text-retry-exact"
    bot = _bot_with_successful_senders()
    bot.send_message = AsyncMock(
        side_effect=[
            Exception("502 Bad Gateway"),
            SimpleNamespace(message_id=22),
        ]
    )
    monkeypatch.setattr(telegram, "Bot", MagicMock(return_value=bot))
    pconfig = SimpleNamespace(token="token", extra={})

    with patch("tools.send_message_tool.asyncio.sleep", new=AsyncMock()):
        result = asyncio.run(
            _send_to_platform(
                Platform.TELEGRAM,
                pconfig,
                chat_id,
                "safe business reply",
                route_envelope=_business_route(chat_id, connection_id),
            )
        )

    assert result["success"] is True
    assert bot.send_message.await_count == 2
    assert {
        call.kwargs["business_connection_id"]
        for call in bot.send_message.await_args_list
    } == {connection_id}


def test_business_connection_survives_media_thread_fallback(
    tmp_path, monkeypatch
) -> None:
    chat_id = "123456789"
    connection_id = "bc-media-retry-exact"
    media_path = tmp_path / "report.txt"
    media_path.write_text("report", encoding="utf-8")
    bot = _bot_with_successful_senders()
    bot.send_document = AsyncMock(
        side_effect=[
            Exception("Bad Request: message thread not found"),
            SimpleNamespace(message_id=33),
        ]
    )
    monkeypatch.setattr(telegram, "Bot", MagicMock(return_value=bot))

    result = asyncio.run(
        _send_telegram(
            "token",
            chat_id,
            "",
            media_files=[(str(media_path), False)],
            thread_id="17585",
            route_envelope=_business_route(
                chat_id, connection_id, thread_id="17585"
            ),
        )
    )

    assert result["success"] is True
    assert bot.send_document.await_count == 2
    first, second = bot.send_document.await_args_list
    assert first.kwargs["business_connection_id"] == connection_id
    assert second.kwargs["business_connection_id"] == connection_id
    assert first.kwargs["message_thread_id"] == 17585
    assert "message_thread_id" not in second.kwargs


@pytest.mark.parametrize(
    "route_envelope, expected_error",
    [
        (
            {
                "version": 1,
                "platform": "telegram",
                "chat_id": "123456789",
                "external_safe_mode": True,
            },
            "ambiguous_route_envelope",
        ),
        (
            _business_route("987654321"),
            "telegram_route_recipient_mismatch",
        ),
        (
            _business_route("123456789", thread_id="999"),
            "telegram_route_thread_mismatch",
        ),
    ],
)
def test_invalid_business_route_fails_before_bot_construction(
    monkeypatch, route_envelope, expected_error
) -> None:
    bot_factory = MagicMock(return_value=_bot_with_successful_senders())
    monkeypatch.setattr(telegram, "Bot", bot_factory)

    result = asyncio.run(
        _send_telegram(
            "token",
            "123456789",
            "must not fall back to plain DM",
            route_envelope=route_envelope,
        )
    )

    assert result == {"error": f"Telegram send failed: {expected_error}"}
    bot_factory.assert_not_called()
