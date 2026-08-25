"""Tests for Telegram document-size cap.

The public Telegram Bot API caps `getFile` at 20MB. A locally-hosted
`telegram-bot-api` server raises that ceiling to 2GB. We treat the presence
of `extra.base_url` as the explicit opt-in to the higher cap.
"""


from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter  # noqa: E402


def test_max_doc_bytes_defaults_to_20mb_without_base_url():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***", extra={}))
    assert adapter._max_doc_bytes == 20 * 1024 * 1024


def test_max_doc_bytes_raised_to_2gb_when_base_url_set():
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="***",
            extra={"base_url": "http://localhost:8081/bot"},
        )
    )
    assert adapter._max_doc_bytes == 2 * 1024 * 1024 * 1024


def test_max_doc_bytes_empty_base_url_keeps_default():
    adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="***", extra={"base_url": ""})
    )
    assert adapter._max_doc_bytes == 20 * 1024 * 1024


def test_public_and_local_size_boundaries_are_exact():
    public = TelegramAdapter(PlatformConfig(enabled=True, token="***", extra={}))
    local = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="***",
            extra={"base_url": "http://127.0.0.1:8081/bot"},
        )
    )

    assert public._telegram_media_size_allowed(
        SimpleNamespace(file_size=20 * 1024 * 1024), "document"
    )[0] is True
    assert public._telegram_media_size_allowed(
        SimpleNamespace(file_size=20 * 1024 * 1024 + 1), "document"
    )[0] is False
    assert local._telegram_media_size_allowed(
        SimpleNamespace(file_size=20 * 1024 * 1024 + 1), "document"
    )[0] is True
    assert local._telegram_media_size_allowed(
        SimpleNamespace(file_size=2 * 1024 * 1024 * 1024 + 1), "document"
    )[0] is False


@pytest.mark.asyncio
async def test_public_oversize_document_is_rejected_before_get_file():
    adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="***", extra={"allow_from": ["42"]})
    )
    adapter.handle_message = AsyncMock()
    document = SimpleNamespace(
        file_name="large.txt",
        mime_type="text/plain",
        file_size=20 * 1024 * 1024 + 1,
        get_file=AsyncMock(),
    )
    message = SimpleNamespace(
        text=None,
        caption=None,
        entities=[],
        caption_entities=[],
        voice=None,
        audio=None,
        document=document,
        photo=None,
        video=None,
        video_note=None,
        sticker=None,
        animation=None,
        location=None,
        venue=None,
        contact=None,
        chat=SimpleNamespace(id=42, type="private", title=None, full_name="Owner"),
        from_user=SimpleNamespace(id=42, is_bot=False, full_name="Owner"),
        sender_business_bot=None,
        business_connection_id=None,
        message_thread_id=None,
        is_topic_message=False,
        forum_topic_created=None,
        reply_to_message=None,
        quote=None,
        media_group_id=None,
        message_id=9,
        date=None,
    )
    update = SimpleNamespace(update_id=10, effective_message=message, message=message)

    await adapter._handle_media_message(update, SimpleNamespace())

    document.get_file.assert_not_awaited()
    adapter.handle_message.assert_awaited_once()
    assert "Maximum: 20 MB" in adapter.handle_message.await_args.args[0].text
