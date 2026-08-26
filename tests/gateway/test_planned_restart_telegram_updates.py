"""Planned Telegram starts preserve server-side updates."""

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def test_routine_startup_preserves_pending_updates():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test"))

    assert adapter._drop_pending_updates_on_connect(is_reconnect=False) is False


def test_reconnect_preserves_pending_updates():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test"))

    assert adapter._drop_pending_updates_on_connect(is_reconnect=True) is False


def test_explicit_reset_signal_is_the_only_connect_drop_authority():
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="test",
            extra={"drop_pending_updates": True},
        )
    )

    assert adapter._drop_pending_updates_on_connect(is_reconnect=False) is True
    assert adapter._drop_pending_updates_on_connect(is_reconnect=True) is False
