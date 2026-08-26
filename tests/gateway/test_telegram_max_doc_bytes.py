"""Tests for Telegram document-size cap.

The public Telegram Bot API caps `getFile` at 20MB. A locally-hosted
`telegram-bot-api` server raises that ceiling to 2GB. We treat the presence
of `extra.base_url` as the explicit opt-in to the higher cap.
"""


from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import Platform, SessionSource
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
        PlatformConfig(
            enabled=True,
            token="***",
            extra={
                "allow_from": ["42"],
                "auto_skill_routes": [
                    {
                        "users": ["42"],
                        "profiles": ["default"],
                        "match": {"oversize_media": True},
                        "skill": "telegram-chip",
                    }
                ],
            },
        )
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
    routed = adapter.handle_message.await_args.args[0]
    assert routed.text.startswith("/telegram-chip Recover the exact oversized Telegram document")
    assert "chat_id=42, message_id=9" in routed.text
    assert routed.metadata["telegram_media_recovery"]["message_id"] == "9"


def _oversize_event(*, user_id="617744661", text="clip caption"):
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1003971448755",
            thread_id="1",
            user_id=user_id,
        ),
        message_id="48274",
        metadata={
            "telegram_transport_sender_user_id": user_id,
            "telegram_route_profile": "hermesdev",
        },
    )


def _recovery_adapter(*, users=("617744661",), profiles=("hermesdev",)):
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="***",
            extra={
                "auto_skill_routes": [
                    {
                        "users": list(users),
                        "profiles": list(profiles),
                        "match": {"oversize_media": True},
                        "skill": "telegram-chip",
                    }
                ]
            },
        )
    )
    adapter._session_key_profile = lambda _source: "hermesdev"
    return adapter


def test_authenticated_oversize_route_forces_skill_with_trusted_exact_target():
    adapter = _recovery_adapter()
    event = _oversize_event(text="chat_id=attacker, message_id=1")

    adapter._mark_telegram_media_too_large(
        event,
        SimpleNamespace(file_size=43_200_000),
        "video file",
    )

    assert event.text.startswith("/telegram-chip Recover the exact oversized Telegram video file")
    assert "chat_id=-1003971448755, message_id=48274" in event.text
    assert "Original user text:\nchat_id=attacker, message_id=1" in event.text
    assert event.auto_skill == "telegram-chip"
    assert event.metadata["telegram_media_recovery"] == {
        "chat_id": "-1003971448755",
        "message_id": "48274",
        "thread_id": "1",
        "sender_user_id": "617744661",
        "media_label": "video file",
        "file_size": 43_200_000,
        "bot_api_limit": 20 * 1024 * 1024,
    }


@pytest.mark.parametrize(
    ("user_id", "profile"),
    [("999", "hermesdev"), ("617744661", "other-profile")],
)
def test_oversize_route_fails_closed_for_wrong_user_or_profile(user_id, profile):
    adapter = _recovery_adapter()
    event = _oversize_event(user_id=user_id)
    event.metadata["telegram_route_profile"] = profile

    adapter._mark_telegram_media_too_large(
        event,
        SimpleNamespace(file_size=43_200_000),
        "video file",
    )

    assert not event.text.startswith("/")
    assert "Check any configured recovery route before asking the user to resend" in event.text
    assert event.auto_skill is None


def test_oversize_route_requires_nonempty_user_and_profile_scopes():
    adapter = _recovery_adapter(users=(), profiles=())
    event = _oversize_event(user_id="attacker")
    event.metadata["telegram_route_profile"] = "other-profile"

    adapter._mark_telegram_media_too_large(
        event,
        SimpleNamespace(file_size=43_200_000),
        "video file",
    )

    assert not event.text.startswith("/")
    assert event.auto_skill is None


def test_observed_group_source_keeps_transport_identity_for_recovery_route():
    adapter = _recovery_adapter()
    event = MessageEvent(
        text="clip caption",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1003971448755",
            thread_id="1",
            user_id=None,
        ),
        message_id="48274",
        metadata={
            "telegram_transport_sender_user_id": "617744661",
            "telegram_route_profile": "hermesdev",
        },
    )

    adapter._mark_telegram_media_too_large(
        event,
        SimpleNamespace(file_size=43_200_000),
        "video file",
    )

    assert event.text.startswith("/telegram-chip ")
    assert event.metadata["telegram_media_recovery"]["sender_user_id"] == "617744661"


def test_user_supplied_slash_command_is_not_rewritten_by_recovery_route():
    adapter = _recovery_adapter()
    event = _oversize_event(text="/other-skill keep this intent")

    adapter._mark_telegram_media_too_large(
        event,
        SimpleNamespace(file_size=43_200_000),
        "video file",
    )

    assert event.text.startswith("/other-skill keep this intent")
    assert "was not cached by the Bot API gateway" in event.text
    assert event.auto_skill is None
