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

    assert "empty or inaudible" in result
    assert '""' not in result
    assert transcripts == []


@pytest.mark.asyncio
async def test_pending_voice_transcription_uses_routed_profile_secret_scope(
    tmp_path,
):
    """Busy/queued voice STT must keep the exact multiplex profile scope.

    Fresh messages are preprocessed by ``_prepare_profile_scoped_inbound_message_text``,
    but interrupt and pending-drain paths call the pending-audio helper directly.
    The worker thread used by STT must inherit the routed profile's secrets there
    too; otherwise a multiplex deployment fails closed before the provider runs.
    """
    from agent import secret_scope
    from gateway.run import GatewayRunner

    profile_home = tmp_path / "profiles" / "hermesdev"
    profile_home.mkdir(parents=True)
    (profile_home / ".env").write_text(
        "H20_KEYS_BASE_URL=https://keys.example.test\n",
        encoding="utf-8",
    )

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1004295722668",
        chat_type="group",
        profile="hermesdev",
    )
    event = MessageEvent(
        text="",
        message_type=MessageType.VOICE,
        source=source,
        media_urls=["/tmp/pending-voice.ogg"],
        media_types=["audio/ogg"],
    )

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(stt_enabled=True, multiplex_profiles=True)
    runner._resolve_profile_home_for_source = lambda _source: profile_home

    def scoped_transcribe(_path, _model, _source):
        return {
            "success": True,
            "transcript": secret_scope.get_secret("H20_KEYS_BASE_URL"),
            "provider": "profile-scoped-test",
        }

    secret_scope.set_multiplex_active(True)
    try:
        with patch(
            "tools.transcription_tools.transcribe_audio",
            side_effect=scoped_transcribe,
        ):
            result, transcripts = await runner._transcribe_pending_audio_event_once(
                event,
                "",
            )
    finally:
        secret_scope.set_multiplex_active(False)

    assert result == "https://keys.example.test"
    assert transcripts == ["https://keys.example.test"]

