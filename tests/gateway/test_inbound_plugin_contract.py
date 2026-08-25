"""Maintained tests for the platform-neutral inbound plugin contract."""

from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import (
    MAX_INBOUND_CONTEXT_NOTE_BYTES,
    BasePlatformAdapter,
    InboundContextNote,
    MessageEvent,
)
from gateway.run import GatewayRunner
from gateway.session import SessionSource


@pytest.mark.asyncio
async def test_base_adapter_inbound_hooks_are_safe_noops():
    class MinimalAdapter(BasePlatformAdapter):
        async def send(self, chat_id, content, **kwargs):
            raise NotImplementedError

        async def connect(self, **kwargs):
            return True

        async def disconnect(self):
            return None

        async def get_chat_info(self, chat_id):
            return {}

    adapter = object.__new__(MinimalAdapter)
    source = SessionSource(platform=Platform.SLACK, chat_id="C1")
    event = MessageEvent(text="original", source=source)

    assert await adapter.prepare_inbound_message_text(event, "original") == (
        "original",
        set(),
    )
    assert adapter.build_ephemeral_context_note(event) is None


def _prepare_runner(adapter):
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(stt_enabled=False)
    runner.adapters = {Platform.SLACK: adapter}
    runner._model = "test"
    runner._base_url = None
    return runner


@pytest.mark.asyncio
async def test_malformed_installed_prepare_hook_fails_closed():
    adapter = type(
        "MalformedAdapter",
        (),
        {"prepare_inbound_message_text": AsyncMock(return_value=None)},
    )()
    runner = _prepare_runner(adapter)
    source = SessionSource(platform=Platform.SLACK, chat_id="C1")

    with pytest.raises(TypeError, match="Inbound plugin prepare"):
        await runner._prepare_inbound_message_text(
            event=MessageEvent(text="hello", source=source),
            source=source,
            history=[],
        )


@pytest.mark.asyncio
async def test_prepare_hook_cannot_claim_media_outside_event():
    class Adapter:
        async def prepare_inbound_message_text(self, _event, text):
            return text, {"C:/not-attached/secret.wav"}

    runner = _prepare_runner(Adapter())
    source = SessionSource(platform=Platform.SLACK, chat_id="C1")

    with pytest.raises(ValueError, match="outside the event"):
        await runner._prepare_inbound_message_text(
            event=MessageEvent(text="hello", source=source),
            source=source,
            history=[],
        )


def test_inbound_context_note_rejects_oversize_instead_of_truncating():
    exact = "x" * MAX_INBOUND_CONTEXT_NOTE_BYTES
    assert InboundContextNote(text=exact).text == exact

    with pytest.raises(ValueError, match="must not be truncated"):
        InboundContextNote(text=f"{exact}x")
