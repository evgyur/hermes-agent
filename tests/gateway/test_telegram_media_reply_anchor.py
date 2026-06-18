from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType, _reply_anchor_for_event
from gateway.session import SessionSource


def _telegram_event(*, media_urls=None, thread_id=None, chat_type="dm"):
    return MessageEvent(
        text="look at this",
        message_type=MessageType.PHOTO if media_urls else MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="617744661",
            chat_type=chat_type,
            thread_id=thread_id,
        ),
        message_id="1740",
        reply_to_message_id="1739",
        media_urls=media_urls or [],
        media_types=["image/jpeg"] if media_urls else [],
    )


def test_telegram_text_dm_keeps_reply_anchor():
    event = _telegram_event()

    assert _reply_anchor_for_event(event) == "1740"


def test_telegram_media_dm_suppresses_reply_anchor_to_avoid_quoted_image_preview():
    event = _telegram_event(media_urls=["/home/hermes/.hermes/image_cache/img_old.jpg"])

    assert _reply_anchor_for_event(event) is None


def test_telegram_media_dm_topic_keeps_reply_anchor_for_required_topic_routing():
    event = _telegram_event(media_urls=["/cache/screenshot.jpg"], thread_id="617744661")

    assert _reply_anchor_for_event(event) == "1740"
