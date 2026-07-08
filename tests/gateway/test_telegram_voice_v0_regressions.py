import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.config import Platform
from gateway.platforms.base import MessageType
from gateway.platforms.telegram import TelegramAdapter
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _source():
    return SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm")


def _runner(adapter=None):
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        stt_enabled=True,
        group_sessions_per_user=True,
        thread_sessions_per_user=False,
    )
    runner.adapters = {Platform.TELEGRAM: adapter} if adapter else {}
    runner._consume_pending_native_image_paths = lambda _key: []
    runner._session_key_for_source = lambda _source: "telegram:dm:12345"
    runner._thread_metadata_for_source = lambda *_args, **_kwargs: {}
    runner._reply_anchor_for_event = lambda _event: None
    return runner


def test_telegram_native_voice_transcript_followup_is_suppressed_after_voice():
    adapter = object.__new__(TelegramAdapter)
    adapter._recent_voice_message_keys = {}

    user = SimpleNamespace(id=617744661)
    chat = SimpleNamespace(id=617744661)
    voice_msg = SimpleNamespace(chat=chat, from_user=user)
    transcript_msg = SimpleNamespace(
        chat=chat,
        from_user=user,
        text="🎙 Расшифровка голосового:\n\nДавай проверим.",
    )

    assert adapter._is_native_voice_transcript_followup(transcript_msg) is False
    adapter._remember_recent_voice_message(voice_msg)
    assert adapter._is_native_voice_transcript_followup(transcript_msg) is True


def test_telegram_native_voice_transcript_followup_matcher_stays_narrow():
    assert TelegramAdapter._voice_transcript_followup_text("🎙 Расшифровка голосового:\n\nДавай проверим.") is True
    assert TelegramAdapter._voice_transcript_followup_text("Давай проверим") is False
    assert TelegramAdapter._voice_transcript_followup_text("🎙 Транскрипт:\nДавай проверим") is False


@pytest.mark.asyncio
async def test_telegram_native_voice_transcript_followup_is_deleted_and_not_enqueued():
    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter._recent_voice_message_keys = {}
    adapter.delete_message = AsyncMock(return_value=True)
    adapter._enqueue_text_event = Mock()

    user = SimpleNamespace(id=617744661)
    chat = SimpleNamespace(id=617744661)
    voice_msg = SimpleNamespace(chat=chat, from_user=user)
    transcript_msg = SimpleNamespace(
        chat=chat,
        from_user=user,
        message_id=12762,
        text="🎙 Расшифровка голосового:\n\nПривет.",
    )
    update = SimpleNamespace(effective_message=transcript_msg, message=transcript_msg, update_id=1)

    adapter._remember_recent_voice_message(voice_msg)
    await TelegramAdapter._handle_text_message(adapter, update, SimpleNamespace())

    adapter.delete_message.assert_awaited_once_with("617744661", "12762")
    adapter._enqueue_text_event.assert_not_called()


def test_telegram_audio_size_gate_rejects_oversized_media_before_download():
    adapter = object.__new__(TelegramAdapter)
    adapter._max_doc_bytes = 1024

    allowed, note = adapter._telegram_media_size_allowed(
        SimpleNamespace(file_size=2048),
        "voice message",
    )

    assert allowed is False
    assert "exceeds" in note
    assert "voice message" in note


class _BoomingTelegramFile:
    async def get_file(self):  # pragma: no cover - test must not reach Bot API download
        raise AssertionError("oversized transcribe-route audio must use telegram-chip before get_file")


@pytest.mark.asyncio
async def test_transcribe_route_oversized_audio_recovers_via_telegram_chip_before_rejecting():
    adapter = object.__new__(TelegramAdapter)
    adapter._max_doc_bytes = 1024
    handled = []
    recovered = []

    event = SimpleNamespace(text="", media_urls=[], media_types=[], message_type=MessageType.AUDIO)
    msg = SimpleNamespace(
        chat=SimpleNamespace(id=-1003918810557),
        message_id=309,
        caption=None,
        photo=None,
        sticker=None,
        voice=None,
        audio=_BoomingTelegramFile(),
        video=None,
        document=None,
        media_group_id=None,
    )
    msg.audio.file_size = 67 * 1024 * 1024
    update = SimpleNamespace(message=msg, update_id=777)

    adapter._should_process_message = lambda _msg: True
    adapter._media_message_type = lambda _msg: MessageType.AUDIO
    adapter._build_message_event = lambda *_args, **_kwargs: event
    adapter._apply_telegram_group_observe_attribution = lambda ev: ev
    adapter._prepare_recent_visible_context = lambda ev: ev

    async def fake_recover(_msg, ev, **kwargs):
        recovered.append(kwargs)
        ev.media_urls = ["/var/tmp/recovered.mp3"]
        ev.media_types = [kwargs["mime_type"]]
        ev.message_type = MessageType.VOICE
        return True

    async def fake_handle(ev):
        handled.append(ev)

    adapter._recover_transcribe_route_media_via_telegram_chip = fake_recover
    adapter.handle_message = fake_handle

    await TelegramAdapter._handle_media_message(adapter, update, SimpleNamespace())

    assert recovered == [
        {
            "ext": ".mp3",
            "mime_type": "audio/mpeg",
            "message_type": MessageType.AUDIO,
            "reason": recovered[0]["reason"],
        }
    ]
    assert "exceeds" in recovered[0]["reason"]
    assert handled == [event]
    assert event.media_urls == ["/var/tmp/recovered.mp3"]
    assert "skipped" not in (event.text or "")


@pytest.mark.asyncio
async def test_transcribe_route_oversized_voice_recovers_via_telegram_chip_before_rejecting():
    adapter = object.__new__(TelegramAdapter)
    adapter._max_doc_bytes = 1024
    handled = []
    recovered = []

    event = SimpleNamespace(text="", media_urls=[], media_types=[], message_type=MessageType.VOICE)
    msg = SimpleNamespace(
        chat=SimpleNamespace(id=-1003918810557),
        message_id=310,
        caption=None,
        photo=None,
        sticker=None,
        voice=_BoomingTelegramFile(),
        audio=None,
        video=None,
        document=None,
        media_group_id=None,
    )
    msg.voice.file_size = 67 * 1024 * 1024
    update = SimpleNamespace(message=msg, update_id=778)

    adapter._should_process_message = lambda _msg: True
    adapter._media_message_type = lambda _msg: MessageType.VOICE
    adapter._build_message_event = lambda *_args, **_kwargs: event
    adapter._apply_telegram_group_observe_attribution = lambda ev: ev
    adapter._prepare_recent_visible_context = lambda ev: ev

    async def fake_recover(_msg, ev, **kwargs):
        recovered.append(kwargs)
        ev.media_urls = ["/var/tmp/recovered.ogg"]
        ev.media_types = [kwargs["mime_type"]]
        return True

    async def fake_handle(ev):
        handled.append(ev)

    adapter._recover_transcribe_route_media_via_telegram_chip = fake_recover
    adapter.handle_message = fake_handle

    await TelegramAdapter._handle_media_message(adapter, update, SimpleNamespace())

    assert recovered[0]["ext"] == ".ogg"
    assert recovered[0]["mime_type"] == "audio/ogg"
    assert recovered[0]["message_type"] == MessageType.VOICE
    assert "exceeds" in recovered[0]["reason"]
    assert handled == [event]
    assert event.media_urls == ["/var/tmp/recovered.ogg"]
    assert "skipped" not in (event.text or "")


@pytest.mark.asyncio
async def test_telegram_video_size_gate_rejects_oversized_media_before_download():
    adapter = object.__new__(TelegramAdapter)
    adapter._max_doc_bytes = 1024
    adapter._should_process_message = lambda _message: True
    adapter._build_message_event = lambda _message, _type, update_id=None: SimpleNamespace(
        text="caption",
        media_urls=[],
        media_types=[],
    )
    adapter._apply_telegram_group_observe_attribution = lambda event: event

    handled = []

    async def handle_message(event):
        handled.append(event)

    adapter.handle_message = handle_message

    class OversizedVideo:
        file_size = 2048

        async def get_file(self):  # pragma: no cover - failure path assertion
            pytest.fail("oversized videos must not be downloaded")

    msg = SimpleNamespace(
        caption=None,
        sticker=None,
        photo=None,
        voice=None,
        audio=None,
        video=OversizedVideo(),
        document=None,
        media_group_id=None,
    )
    update = SimpleNamespace(message=msg, update_id=1)

    await TelegramAdapter._handle_media_message(adapter, update, SimpleNamespace())

    assert len(handled) == 1
    assert handled[0].media_urls == []
    assert handled[0].media_types == []
    assert "video file" in handled[0].text
    assert "exceeds" in handled[0].text


@pytest.mark.asyncio
async def test_voice_tts_is_explicit_audio_reply_opt_in():
    adapter = SimpleNamespace(
        _auto_tts_disabled_chats=set(),
        _auto_tts_enabled_chats=set(),
    )
    runner = _runner(adapter)
    runner._voice_mode = {}
    runner._voice_provider_mode = {}
    runner._save_voice_modes = lambda: None
    runner._save_voice_provider_modes = lambda: None

    event = SimpleNamespace(
        source=_source(),
        get_command_args=lambda: "tts",
    )
    result = await GatewayRunner._handle_voice_command(runner, event)

    assert runner._voice_mode["telegram:12345"] == "all"
    assert "12345" in adapter._auto_tts_enabled_chats
    assert result
