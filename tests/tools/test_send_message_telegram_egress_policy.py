"""Focused regression tests for standalone Telegram egress policy."""

import asyncio
from contextlib import contextmanager
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
from gateway.session_context import clear_session_vars, set_session_vars

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


@contextmanager
def _bound_business_runtime(chat_id: str, *, thread_id: str | None = None):
    tokens = set_session_vars(
        platform="telegram",
        chat_id=chat_id,
        chat_type="dm",
        thread_id=thread_id or "",
        user_id="owner-1",
        profile="default",
    )
    try:
        yield
    finally:
        clear_session_vars(tokens)


def test_send_message_schema_exposes_explicit_route_envelope() -> None:
    route_schema = SEND_MESSAGE_SCHEMA["parameters"]["properties"]["route_envelope"]

    assert route_schema["type"] == "object"


def test_denied_plain_target_has_zero_text_wire_calls(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_TELEGRAM_EGRESS_DENY_IDS", "700000321")
    bot = _bot_with_successful_senders()
    bot_factory = MagicMock(return_value=bot)
    monkeypatch.setattr(telegram, "Bot", bot_factory)

    result = asyncio.run(_send_telegram("token", "700000321", "must not send"))

    assert result == {"error": "Telegram send failed: telegram_recipient_denied"}
    bot_factory.assert_not_called()
    bot.send_message.assert_not_awaited()


def test_denied_plain_target_has_zero_media_wire_calls(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_TELEGRAM_EGRESS_DENY_IDS", "700000321")
    media_path = tmp_path / "blocked.txt"
    media_path.write_text("blocked", encoding="utf-8")
    bot = _bot_with_successful_senders()
    bot_factory = MagicMock(return_value=bot)
    monkeypatch.setattr(telegram, "Bot", bot_factory)

    result = asyncio.run(
        _send_telegram(
            "token",
            "700000321",
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

    with _bound_business_runtime(chat_id):
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

    with _bound_business_runtime(chat_id, thread_id="17585"):
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
            "invalid_route_envelope",
        ),
        (
            {
                "version": 1,
                "platform": "telegram",
                "runtime_profile": "default",
                "transport_profile": "default",
                "chat_id": "123456789",
                "thread_id": None,
                "user_id": "owner-1",
                "business_connection_id": None,
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


def test_forged_valid_route_without_runtime_binding_fails_before_bot(monkeypatch) -> None:
    bot_factory = MagicMock(return_value=_bot_with_successful_senders())
    monkeypatch.setattr(telegram, "Bot", bot_factory)

    result = asyncio.run(
        _send_telegram(
            "token",
            "123456789",
            "forged route",
            route_envelope=_business_route("123456789"),
        )
    )

    assert result == {"error": "Telegram send failed: telegram_runtime_route_unbound"}
    bot_factory.assert_not_called()


def test_runtime_bound_origin_and_recipient_allow_exact_business_route(monkeypatch) -> None:
    bot = _bot_with_successful_senders()
    monkeypatch.setattr(telegram, "Bot", MagicMock(return_value=bot))
    tokens = set_session_vars(
        platform="telegram",
        chat_id="123456789",
        chat_type="dm",
        user_id="owner-1",
        profile="default",
    )
    try:
        result = asyncio.run(
            _send_telegram(
                "token",
                "123456789",
                "exact route",
                route_envelope=_business_route("123456789"),
            )
        )
    finally:
        clear_session_vars(tokens)

    assert result["success"] is True
    bot.send_message.assert_awaited_once()
