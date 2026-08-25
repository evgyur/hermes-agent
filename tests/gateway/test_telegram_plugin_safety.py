"""Maintained Telegram plugin authority and private-context safety gates."""

from unittest.mock import MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

PROFILE = "hermesdev"
CHAT_ID = "-1003971448755"
THREAD_ID = "24901"
OWNER_ID = "12345"
PRIVATE_LINK = "https://t.me/c/3971448755/26452/47266"


def _adapter():
    cls = load_plugin_adapter("telegram").TelegramAdapter
    adapter = object.__new__(cls)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(
        enabled=True,
        token="fake",
        extra={
            "telegram_chip_context_routes": [{
                "profile": PROFILE,
                "chat_id": CHAT_ID,
                "thread_id": THREAD_ID,
                "user_id": OWNER_ID,
            }]
        },
    )
    return adapter


def _event(text, *, reply_to_message_id=None, reply_to_text=None):
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            profile=PROFILE,
            chat_id=CHAT_ID,
            thread_id=THREAD_ID,
            chat_type="group",
            user_id=OWNER_ID,
        ),
        message_id="1",
        reply_to_message_id=reply_to_message_id,
        reply_to_text=reply_to_text,
    )


@pytest.mark.asyncio
async def test_topic_root_is_routing_only_and_all_private_links_are_resolved():
    adapter = _adapter()
    root = _event("Го", reply_to_message_id=THREAD_ID, reply_to_text=None)
    adapter._telegram_chip_fetch_message_sync = MagicMock(return_value={"text": "root"})
    assert await adapter._resolve_telegram_chip_context(root) is False
    adapter._telegram_chip_fetch_message_sync.assert_not_called()

    second = "https://t.me/c/3971448755/26452/47267"
    event = _event(f"Compare {PRIVATE_LINK} and {second}")
    adapter._telegram_chip_fetch_message_sync = MagicMock(
        side_effect=lambda _chat, message: {"text": f"context-{message}"}
    )
    assert await adapter._resolve_telegram_chip_context(event) is True
    assert adapter._telegram_chip_fetch_message_sync.call_count == 2
    assert len(event.metadata["telegram_chip_resolution"]["sources"]) == 2


def test_telegram_chip_rejects_non_loopback_origin_before_network(monkeypatch):
    adapter = _adapter()
    adapter.config.extra["telegram_chip_base_url"] = "http://example.test:8080"
    urlopen = MagicMock()
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(ValueError):
        adapter._telegram_chip_fetch_message_sync(CHAT_ID, 1)
    urlopen.assert_not_called()


@pytest.mark.asyncio
async def test_transcribe_multilink_recovery_is_atomic_and_ordered(tmp_path):
    adapter = _adapter()
    adapter.config.extra["transcribe_routes"] = [
        {
            "enabled": True,
            "profile": PROFILE,
            "chat_id": CHAT_ID,
            "thread_id": THREAD_ID,
        }
    ]
    second = "https://t.me/c/3971448755/26452/47267"
    event = _event(f"Transcribe {PRIVATE_LINK} and {second}")
    event.message_type = MessageType.TEXT
    adapter._telegram_chip_fetch_message_sync = MagicMock(
        return_value={"has_media": True}
    )
    paths = []

    def _download(_chat_id, message_id):
        if message_id == 47267:
            raise RuntimeError("second download failed")
        path = tmp_path / f"{message_id}.mp3"
        path.write_bytes(b"audio")
        paths.append(path)
        return str(path)

    adapter._telegram_chip_media_download_sync = _download
    with pytest.raises(RuntimeError, match="second download"):
        await adapter._recover_transcribe_route_tme_link_via_telegram_chip(
            event, CHAT_ID
        )

    assert adapter._telegram_chip_fetch_message_sync.call_count == 2
    assert event.media_urls == []
    assert event.message_type is MessageType.TEXT
    assert all(not path.exists() for path in paths)
