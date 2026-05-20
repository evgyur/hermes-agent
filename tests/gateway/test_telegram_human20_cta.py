"""Tests for the Human20 CTA inline button policy in Telegram replies."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure repo root is importable
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


def _ensure_telegram_mock():
    """Provide minimal Telegram classes when python-telegram-bot is absent."""
    existing = sys.modules.get("telegram")
    if existing is not None and not isinstance(existing, MagicMock) and hasattr(existing, "__file__"):
        return

    mod = MagicMock()

    class InlineKeyboardButton:
        def __init__(self, text, url=None, callback_data=None):
            self.text = text
            self.url = url
            self.callback_data = callback_data

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

    mod.InlineKeyboardButton = InlineKeyboardButton
    mod.InlineKeyboardMarkup = InlineKeyboardMarkup
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules[name] = mod
    sys.modules["telegram.error"] = mod.error


_ensure_telegram_mock()

from gateway.config import PlatformConfig
from gateway.platforms.telegram import TelegramAdapter, _HUMAN20_CTA_TEXT, _HUMAN20_CTA_URL


def _make_adapter():
    return TelegramAdapter(PlatformConfig(enabled=True, token="test-token", extra={}))


def _button_text_and_url(markup):
    button = markup.inline_keyboard[-1][0]
    return getattr(button, "text", None), getattr(button, "url", None)


def test_human20_cta_not_added_to_direct_bot_dm():
    adapter = _make_adapter()
    adapter._cache_observed_chat_type("123456789", "dm")

    assert adapter._human20_inline_markup("123456789", metadata=None) is None
    assert adapter._human20_inline_markup("123456789", metadata={"thread_id": "1"}) is None


def test_human20_cta_added_only_for_business_dm_reply():
    adapter = _make_adapter()
    adapter._cache_observed_chat_type("123456", "dm")

    markup = adapter._human20_inline_markup(
        "123456",
        metadata={"business_connection_id": "biz-123", "external_safe_mode": True},
    )

    assert markup is not None
    assert _button_text_and_url(markup) == (_HUMAN20_CTA_TEXT, _HUMAN20_CTA_URL)


def test_human20_cta_not_added_to_business_group_reply():
    adapter = _make_adapter()
    adapter._cache_observed_chat_type("-100123456", "group")

    assert adapter._human20_inline_markup(
        "-100123456",
        metadata={"business_connection_id": "biz-123"},
    ) is None
