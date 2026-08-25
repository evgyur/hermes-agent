"""Telegram hidden ``text_link`` URLs remain visible to the agent."""

from types import SimpleNamespace

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType
from plugins.platforms.telegram.adapter import TelegramAdapter


def _message(*, text=None, caption=None, entities=None, caption_entities=None):
    return SimpleNamespace(
        text=text,
        caption=caption,
        entities=entities or [],
        caption_entities=caption_entities or [],
    )


def _text_link(offset, length, url):
    return SimpleNamespace(type="text_link", offset=offset, length=length, url=url)


def test_hidden_tme_link_after_emoji_uses_telegram_utf16_offsets():
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    message = _message(
        text="🔥 тут",
        entities=[_text_link(3, 3, "https://t.me/c/3971448755/26452/47266")],
    )

    assert adapter._expand_link_entities(message) == (
        "🔥 тут (https://t.me/c/3971448755/26452/47266)"
    )


def test_hidden_caption_link_is_expanded_without_changing_visible_text():
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    message = _message(
        caption="Смотри тут проект",
        caption_entities=[_text_link(7, 3, "https://example.test/context")],
    )

    assert adapter._expand_link_entities(message) == (
        "Смотри тут (https://example.test/context) проект"
    )


def test_invalid_utf16_span_is_ignored():
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    message = _message(
        text="🔥 link",
        entities=[_text_link(1, 1, "https://example.test/mid-surrogate")],
    )

    assert adapter._expand_link_entities(message) == "🔥 link"


def test_message_event_contains_the_expanded_hidden_url():
    adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="test", extra={"allow_from": ["42"]})
    )
    message = SimpleNamespace(
        text="🔥 тут",
        caption=None,
        entities=[_text_link(3, 3, "https://t.me/c/3971448755/26452/47266")],
        caption_entities=[],
        chat=SimpleNamespace(id=42, type="private", title=None, full_name="Owner"),
        from_user=SimpleNamespace(id=42, full_name="Owner", is_bot=False),
        message_thread_id=None,
        is_topic_message=False,
        forum_topic_created=None,
        reply_to_message=None,
        quote=None,
        message_id=7,
        date=None,
        business_connection_id=None,
        sender_business_bot=None,
    )

    event = adapter._build_message_event(message, MessageType.TEXT, update_id=11)

    assert event.text == "🔥 тут (https://t.me/c/3971448755/26452/47266)"
