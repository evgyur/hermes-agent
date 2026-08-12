"""Tests for Telegram recent visible context injection."""

import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from gateway.config import PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    telegram_mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    telegram_mod.constants.ChatType.GROUP = "group"
    telegram_mod.constants.ChatType.SUPERGROUP = "supergroup"
    telegram_mod.constants.ChatType.CHANNEL = "channel"
    telegram_mod.constants.ChatType.PRIVATE = "private"

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, telegram_mod)


_ensure_telegram_mock()

from gateway.platforms.base import MessageType  # noqa: E402
from gateway.platforms.telegram import TelegramAdapter  # noqa: E402
from gateway.run import _append_recent_context_prompt  # noqa: E402


def _make_adapter():
    return TelegramAdapter(PlatformConfig(enabled=True, token="***", extra={}))


def _make_message(text: str, *, message_id: int, thread_id=None):
    chat = SimpleNamespace(id=-100123, type="supergroup", title="Hermes // Marketing", full_name=None, is_forum=True)
    user = SimpleNamespace(id=123456789, full_name='Example User')
    return SimpleNamespace(
        chat=chat,
        from_user=user,
        text=text,
        caption=None,
        message_thread_id=thread_id,
        message_id=message_id,
        reply_to_message=None,
        quote=None,
        date=None,
        forum_topic_created=None,
    )


def test_recent_context_includes_prior_same_topic_messages_only():
    adapter = _make_adapter()

    first = adapter._build_message_event(
        _make_message("текст четыре сообщения выше", message_id=88, thread_id=107),
        MessageType.TEXT,
    )
    adapter._attach_recent_visible_context(first)
    adapter._record_recent_visible_message(first)
    assert first.recent_context is None

    other_topic = adapter._build_message_event(
        _make_message("это из другого топика", message_id=89, thread_id=999),
        MessageType.TEXT,
    )
    adapter._attach_recent_visible_context(other_topic)
    adapter._record_recent_visible_message(other_topic)

    second = adapter._build_message_event(
        _make_message("что дальше?", message_id=90, thread_id=107),
        MessageType.TEXT,
    )
    adapter._attach_recent_visible_context(second)

    assert second.recent_context is not None
    assert "Recent visible Telegram context" in second.recent_context
    assert "ID 88" in second.recent_context
    assert "текст четыре сообщения выше" in second.recent_context
    assert "другого топика" not in second.recent_context
    assert "что дальше" not in second.recent_context


def test_recent_context_prompt_appended_to_system_prompt_not_user_text():
    event = SimpleNamespace(recent_context="## Recent visible Telegram context\n\n- ID 1 | Example: prior")

    combined = _append_recent_context_prompt("## Current Session Context\n\nSource: Telegram", event)

    assert "## Current Session Context" in combined
    assert "## Recent visible Telegram context" in combined
    assert "ID 1 | Example: prior" in combined


def test_recent_context_prompt_ignores_empty_context():
    event = SimpleNamespace(recent_context="")

    assert _append_recent_context_prompt("base", event) == "base"


def test_recent_context_keeps_latest_ten_prior_messages():
    adapter = _make_adapter()
    for message_id in range(1, 13):
        event = adapter._build_message_event(
            _make_message(f"message {message_id}", message_id=message_id, thread_id=107),
            MessageType.TEXT,
        )
        adapter._record_recent_visible_message(event)

    current = adapter._build_message_event(
        _make_message("current", message_id=13, thread_id=107),
        MessageType.TEXT,
    )
    adapter._attach_recent_visible_context(current)

    assert current.recent_context is not None
    assert "ID 1 |" not in current.recent_context
    assert "ID 2 |" not in current.recent_context
    for message_id in range(3, 13):
        assert f"ID {message_id} |" in current.recent_context


def test_prepare_recent_context_wires_attach_before_record():
    """Production helper must attach prior context before recording current text."""
    adapter = _make_adapter()
    first = adapter._build_message_event(
        _make_message("prior", message_id=1, thread_id=107),
        MessageType.TEXT,
    )
    adapter._prepare_recent_visible_context(first)

    current = adapter._build_message_event(
        _make_message("current", message_id=2, thread_id=107),
        MessageType.TEXT,
    )
    prepared = adapter._prepare_recent_visible_context(current)

    assert prepared.recent_context is not None
    assert "ID 1 | prior" in prepared.recent_context
    assert "current" not in prepared.recent_context


def test_oversized_voice_fallback_keeps_recent_context():
    """Early oversized-media fallback must use the same context preparation."""
    async def _run():
        adapter = _make_adapter()
        adapter.handle_message = AsyncMock()
        adapter._should_process_message = MagicMock(return_value=True)
        adapter._handle_business_voice_transcription_hook = AsyncMock(return_value=False)
        adapter._telegram_media_size_allowed = MagicMock(
            return_value=(False, "voice message exceeds Bot API download limit")
        )
        adapter._recover_transcribe_route_media_via_telegram_chip = AsyncMock(return_value=False)

        prior = adapter._build_message_event(
            _make_message("prior", message_id=1, thread_id=107),
            MessageType.TEXT,
        )
        adapter._prepare_recent_visible_context(prior)

        msg = _make_message("", message_id=2, thread_id=107)
        msg.text = None
        msg.voice = SimpleNamespace(file_size=999_999_999)
        msg.audio = None
        msg.photo = None
        msg.video = None
        msg.document = None
        msg.sticker = None
        msg.location = None
        update = SimpleNamespace(message=msg, effective_message=None, update_id=22)

        await adapter._handle_media_message(update, SimpleNamespace())

        event = adapter.handle_message.await_args.args[0]
        assert event.recent_context is not None
        assert "ID 1 | prior" in event.recent_context
        assert "voice message exceeds Bot API download limit" in event.text

    asyncio.run(_run())
