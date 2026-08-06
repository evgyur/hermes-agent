"""Gateway STT config tests — honor stt.enabled: false from config.yaml."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from gateway.config import GatewayConfig, Platform, load_gateway_config
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


def test_gateway_config_stt_disabled_from_dict_nested():
    config = GatewayConfig.from_dict({"stt": {"enabled": False}})
    assert config.stt_enabled is False


def test_load_gateway_config_bridges_stt_enabled_from_config_yaml(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.dump({"stt": {"enabled": False}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    config = load_gateway_config()

    assert config.stt_enabled is False


def test_gateway_config_stt_echo_transcripts_default_on():
    config = GatewayConfig.from_dict({})

    assert config.stt_enabled is True
    assert config.stt_echo_transcripts is True


def test_gateway_config_stt_echo_transcripts_can_be_disabled_nested():
    config = GatewayConfig.from_dict({"stt": {"echo_transcripts": False}})

    assert config.stt_enabled is True
    assert config.stt_echo_transcripts is False


def test_gateway_config_stt_echo_transcripts_can_be_explicitly_enabled_nested():
    config = GatewayConfig.from_dict({"stt": {"echo_transcripts": True}})

    assert config.stt_enabled is True
    assert config.stt_echo_transcripts is True


@pytest.mark.asyncio
async def test_enrich_message_with_transcription_returns_tuple_for_empty_content_placeholder():
    """A successful transcription whose caption is the empty-content placeholder
    must still return the ``(text, transcripts)`` tuple.

    The Discord adapter delivers a captionless voice note as the literal
    ``"(The user sent a message with no text content)"`` placeholder. When STT
    succeeds we strip that redundant placeholder and return just the transcript
    prefix — but the method's contract (and every caller, which unpacks the
    result as ``text, transcripts = ...``) requires a 2-tuple. Returning a bare
    string here raised ``ValueError: too many values to unpack`` and dropped the
    whole voice message on the floor.
    """
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(stt_enabled=True)
    runner._has_setup_skill = lambda: False

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={
            "success": True,
            "transcript": "hello from a captionless voice note",
            "provider": "local_command",
        },
    ):
        result, transcripts = await runner._enrich_message_with_transcription(
            "(The user sent a message with no text content)",
            ["/tmp/voice.ogg"],
        )

    # The redundant placeholder is stripped, leaving only the transcript prefix.
    assert "hello from a captionless voice note" in result
    assert "(The user sent a message with no text content)" not in result
    # Crucially, the transcripts are still surfaced so callers can echo them.
    assert transcripts == ["hello from a captionless voice note"]


@pytest.mark.asyncio
async def test_enrich_message_with_transcription_guards_empty_transcript():
    """success=True with an empty/whitespace transcript must not emit empty
    quotes — it gets a sentinel note and is excluded from transcripts (#41603)."""
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(stt_enabled=True)
    runner._has_setup_skill = lambda: False

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={"success": True, "transcript": "   \n\t", "provider": "local_command"},
    ):
        result, transcripts = await runner._enrich_message_with_transcription(
            "caption",
            ["/tmp/voice.ogg"],
        )

    assert result is not None
    # Success path: the transcript passes through as a plain quoted line, with
    # no "voice message" meta-commentary that the LLM would echo back.
    assert "queued voice transcript" in result


@pytest.mark.asyncio
async def test_dequeued_voice_echoes_by_default():
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(stt_enabled=True)
    runner._has_setup_skill = lambda: False
    runner._thread_metadata_for_source = lambda *_args, **_kwargs: {}
    runner._reply_anchor_for_event = lambda _event: None
    runner._claim_transcript_echo_once = lambda *_args, **_kwargs: True

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        chat_type="dm",
    )
    event = MessageEvent(
        text="",
        message_type=MessageType.VOICE,
        source=source,
        media_urls=["/tmp/queued-voice.ogg"],
        media_types=["audio/ogg"],
        message_id="42",
    )

    class Adapter:
        send = AsyncMock()

        def get_pending_message(self, session_key):
            return event

        def _is_transcribe_route_chat(self, chat_id):
            return False

    adapter = Adapter()
    runner.adapters = {Platform.TELEGRAM: adapter}

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={
            "success": True,
            "transcript": "default should echo this transcript",
            "provider": "local_command",
        },
    ):
        result = await runner._dequeue_pending_with_transcription(
            adapter,
            "telegram:dm:123",
            source,
        )

    assert "default should echo this transcript" in result
    adapter.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_dequeued_voice_does_not_echo_when_stt_echo_transcripts_disabled():
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(stt_enabled=True, stt_echo_transcripts=False)
    runner._has_setup_skill = lambda: False

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        chat_type="dm",
    )
    event = MessageEvent(
        text="",
        message_type=MessageType.VOICE,
        source=source,
        media_urls=["/tmp/queued-voice.ogg"],
        media_types=["audio/ogg"],
        message_id="42",
    )

    class Adapter:
        send = AsyncMock()

        def get_pending_message(self, session_key):
            return event

        def _is_transcribe_route_chat(self, chat_id):
            return False

    adapter = Adapter()
    runner.adapters = {Platform.TELEGRAM: adapter}

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={
            "success": True,
            "transcript": "do not echo this transcript",
            "provider": "local_command",
        },
    ):
        result = await runner._dequeue_pending_with_transcription(
            adapter,
            "telegram:dm:123",
            source,
        )

    assert "do not echo this transcript" in result
    adapter.send.assert_not_awaited()
