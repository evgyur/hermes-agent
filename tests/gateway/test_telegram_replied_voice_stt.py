from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform
from gateway.platforms.base import CachedMedia, MessageEvent, MessageType
from gateway.session import SessionSource
from plugins.platforms.telegram.adapter import TelegramAdapter


@pytest.mark.asyncio
async def test_replied_to_voice_keeps_voice_semantics_for_stt() -> None:
    adapter = object.__new__(TelegramAdapter)
    voice = SimpleNamespace(file_size=123)
    file_obj = SimpleNamespace(
        file_path="voice.ogg",
        download_as_bytearray=AsyncMock(return_value=bytearray(b"ogg")),
    )
    voice.get_file = AsyncMock(return_value=file_obj)
    replied = SimpleNamespace(voice=voice)
    message = SimpleNamespace(reply_to_message=replied)
    event = MessageEvent(
        text=".",
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm"),
    )
    cached = CachedMedia(
        path="/tmp/replied-voice.ogg",
        media_type="audio/ogg",
        kind="audio",
        display_name="voice.ogg",
    )

    adapter._observed_media_source = lambda _msg: (voice, "voice.ogg", "audio/ogg", "audio")
    with patch("gateway.platforms.base.cache_media_bytes", return_value=cached):
        await adapter._cache_replied_media(message, event)

    assert event.message_type == MessageType.VOICE
    assert event.media_urls == ["/tmp/replied-voice.ogg"]


@pytest.mark.asyncio
async def test_replied_to_audio_file_stays_audio_attachment() -> None:
    adapter = object.__new__(TelegramAdapter)
    audio = SimpleNamespace(file_size=123)
    file_obj = SimpleNamespace(
        file_path="song.mp3",
        download_as_bytearray=AsyncMock(return_value=bytearray(b"mp3")),
    )
    audio.get_file = AsyncMock(return_value=file_obj)
    replied = SimpleNamespace(voice=None, audio=audio)
    message = SimpleNamespace(reply_to_message=replied)
    event = MessageEvent(
        text=".",
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm"),
    )
    cached = CachedMedia(
        path="/tmp/replied-song.mp3",
        media_type="audio/mpeg",
        kind="audio",
        display_name="song.mp3",
    )

    adapter._observed_media_source = lambda _msg: (audio, "song.mp3", "audio/mpeg", "audio")
    with patch("gateway.platforms.base.cache_media_bytes", return_value=cached):
        await adapter._cache_replied_media(message, event)

    assert event.message_type == MessageType.AUDIO