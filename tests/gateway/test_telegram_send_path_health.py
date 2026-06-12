"""TelegramAdapter send-path health gating after reconnect storms.

After sustained Bad Gateway / TimedOut reconnect cycles, the PTB httpx client
can enter a wedged state where ``bot.send_message()`` returns a valid Message
but nothing reaches the recipient.  ``_send_path_degraded`` short-circuits
``send()`` so cron's live-adapter branch falls through to standalone HTTP.
"""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


def _make_adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._bot = MagicMock()
    adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    return adapter


@pytest.mark.asyncio
async def test_send_succeeds_when_path_healthy():
    """Healthy adapter delivers normally; send_message is called."""
    adapter = _make_adapter()
    assert adapter._send_path_degraded is False

    result = await adapter.send("123", "hello")

    assert result.success is True
    adapter._bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_send_short_circuits_when_path_degraded():
    """Degraded adapter returns failure WITHOUT calling send_message,
    so cron's live-adapter branch falls through to standalone HTTP."""
    adapter = _make_adapter()
    adapter._send_path_degraded = True

    result = await adapter.send("123", "hello")

    assert result.success is False
    assert result.error == "send_path_degraded"
    assert result.retryable is True
    adapter._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_retries_without_business_connection_id_on_business_peer_invalid():
    """Telegram can mark an inbound DM with a stale Business connection id.

    If Bot API rejects the reply with Business_peer_invalid, retry the same
    message as a normal bot DM instead of dropping the response.
    """
    adapter = _make_adapter()
    adapter._bot.send_message = AsyncMock(
        side_effect=[Exception("Business_peer_invalid"), MagicMock(message_id=99)]
    )

    result = await adapter.send(
        "6442556885",
        "ОК",
        metadata={"business_connection_id": "biz-123"},
    )

    assert result.success is True
    assert result.message_id == "99"
    assert adapter._bot.send_message.await_count == 2
    first_kwargs = adapter._bot.send_message.await_args_list[0].kwargs
    second_kwargs = adapter._bot.send_message.await_args_list[1].kwargs
    assert first_kwargs["business_connection_id"] == "biz-123"
    assert "business_connection_id" not in second_kwargs


@pytest.mark.asyncio
async def test_reconnect_storm_sets_and_heartbeat_clears_flag(monkeypatch):
    """_handle_polling_network_error sets the flag; a successful heartbeat
    probe in _verify_polling_after_reconnect clears it."""
    adapter = _make_adapter()
    adapter._app = MagicMock()
    adapter._app.updater = MagicMock()
    adapter._app.updater.running = True
    adapter._app.updater.stop = AsyncMock()
    adapter._app.updater.start_polling = AsyncMock()
    adapter._app.bot = MagicMock()
    adapter._app.bot.get_me = AsyncMock(return_value=MagicMock())
    adapter._polling_error_callback_ref = AsyncMock()
    monkeypatch.setattr(
        "gateway.platforms.telegram.Update", MagicMock(ALL_TYPES=[])
    )

    await adapter._handle_polling_network_error(OSError("Bad Gateway"))
    assert adapter._send_path_degraded is True

    with patch("gateway.platforms.telegram.asyncio.sleep", new_callable=AsyncMock):
        await adapter._verify_polling_after_reconnect()
    assert adapter._send_path_degraded is False
