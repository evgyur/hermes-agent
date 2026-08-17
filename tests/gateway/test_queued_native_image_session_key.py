import base64
import importlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    merge_pending_message_event,
)
from gateway.session import SessionSource


_ONE_BY_ONE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6L2ioAAAAASUVORK5CYII="
)


class CaptureAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="***"), Platform.TELEGRAM)
        self.sent = []
        self.typing = []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.sent.append(
            {
                "chat_id": chat_id,
                "content": content,
                "reply_to": reply_to,
                "metadata": metadata,
            }
        )
        return SendResult(success=True, message_id="sent-1")

    async def send_typing(self, chat_id, metadata=None) -> None:
        self.typing.append({"chat_id": chat_id, "metadata": metadata})

    async def stop_typing(self, chat_id) -> None:
        return None

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


class CaptureQueuedNativeImageAgent:
    calls = []

    def __init__(self, **kwargs):
        self.tools = []
        self.tool_progress_callback = kwargs.get("tool_progress_callback")

    def run_conversation(self, message, conversation_history=None, task_id=None):
        type(self).calls.append(message)
        return {
            "final_response": f"done-{len(type(self).calls)}",
            "messages": [],
            "api_calls": 1,
        }


def _make_runner(adapter):
    gateway_run = importlib.import_module("gateway.run")
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {adapter.platform: adapter}
    runner._voice_mode = {}
    runner._prefill_messages = []
    runner._ephemeral_system_prompt = ""
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._session_db = None
    runner._running_agents = {}
    runner._session_run_generation = {}
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = SimpleNamespace(
        thread_sessions_per_user=False,
        group_sessions_per_user=False,
        stt_enabled=False,
    )
    runner._model = "openai/gpt-4.1-mini"
    runner._base_url = None
    runner._decide_image_input_mode = lambda **_kw: "native"
    return runner


@pytest.mark.asyncio
async def test_queued_followup_uses_pending_event_session_key_for_native_images(monkeypatch, tmp_path):
    CaptureQueuedNativeImageAgent.calls = []

    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = CaptureQueuedNativeImageAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"})

    adapter = CaptureAdapter()
    runner = _make_runner(adapter)
    from hermes_state import SessionDB

    ledger_db = SessionDB(tmp_path / "state.db")
    runner._session_db = ledger_db

    image_path = tmp_path / "queued-image.png"
    image_path.write_bytes(_ONE_BY_ONE_PNG)

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
    )
    pending_source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        thread_id="17585",
    )

    pending_event = MessageEvent(
        text="describe this",
        message_type=MessageType.PHOTO,
        source=pending_source,
        media_urls=[str(image_path)],
        media_types=["image/png"],
        message_id="queued-1",
    )
    adapter._pending_messages["agent:main:telegram:group:-1001"] = pending_event
    ledger_id = runner._record_gateway_ledger_received(
        pending_event,
        session_key="agent:main:telegram:group:-1001",
    )
    runner._set_gateway_ledger_deferred(pending_event)

    merged_event = MessageEvent(
        text="and this",
        message_type=MessageType.PHOTO,
        source=pending_source,
        media_urls=[str(image_path)],
        media_types=["image/png"],
        message_id="queued-2",
    )
    merged_ledger_id = runner._record_gateway_ledger_received(
        merged_event,
        session_key="agent:main:telegram:group:-1001",
    )
    runner._set_gateway_ledger_deferred(merged_event)
    merge_pending_message_event(
        adapter._pending_messages,
        "agent:main:telegram:group:-1001",
        merged_event,
    )

    result = await runner._run_agent(
        message="hello",
        context_prompt="",
        history=[],
        source=source,
        session_id="sess-native-image-followup",
        session_key="agent:main:telegram:group:-1001",
    )

    assert result["final_response"] == "done-2"
    assert len(CaptureQueuedNativeImageAgent.calls) == 2
    queued_message = CaptureQueuedNativeImageAgent.calls[1]
    assert isinstance(queued_message, list)
    assert queued_message[0]["type"] == "text"
    assert queued_message[0]["text"].startswith("describe this")
    assert any(part.get("type") == "image_url" for part in queued_message)
    ledger_row = ledger_db.get_gateway_message_ledger(ledger_id)
    assert ledger_row is not None
    assert ledger_row["status"] == "completed"
    assert ledger_row["session_id"] == "sess-native-image-followup"
    merged_row = ledger_db.get_gateway_message_ledger(merged_ledger_id)
    assert merged_row is not None
    assert merged_row["status"] == "completed"
    assert merged_row["session_id"] == "sess-native-image-followup"


@pytest.mark.asyncio
async def test_queued_followup_preparation_exception_terminalizes_ledger(
    monkeypatch,
    tmp_path,
):
    CaptureQueuedNativeImageAgent.calls = []
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = CaptureQueuedNativeImageAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    gateway_run = importlib.import_module("gateway.run")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"})

    from hermes_state import SessionDB

    adapter = CaptureAdapter()
    runner = _make_runner(adapter)
    ledger_db = SessionDB(tmp_path / "state.db")
    runner._session_db = ledger_db
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
    )
    pending_event = MessageEvent(
        text="queued text",
        message_type=MessageType.TEXT,
        source=source,
        message_id="queued-prep-error",
    )
    session_key = "agent:main:telegram:group:-1001"
    adapter._pending_messages[session_key] = pending_event
    ledger_id = runner._record_gateway_ledger_received(
        pending_event,
        session_key=session_key,
    )
    runner._set_gateway_ledger_deferred(pending_event)
    runner._prepare_profile_scoped_inbound_message_text = AsyncMock(
        side_effect=RuntimeError("prep exploded")
    )

    with pytest.raises(RuntimeError, match="prep exploded"):
        await runner._run_agent(
            message="hello",
            context_prompt="",
            history=[],
            source=source,
            session_id="sess-prep-error",
            session_key=session_key,
        )

    row = ledger_db.get_gateway_message_ledger(ledger_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["reason"] == "queued-followup-preparation-error"
    assert row["dispatch_started_at"] is not None
    assert row["failed_at"] is not None
