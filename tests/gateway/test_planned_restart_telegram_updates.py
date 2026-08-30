"""Planned Telegram starts preserve server-side updates."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource
from hermes_state import AsyncSessionDB, SessionDB
from plugins.platforms.telegram.adapter import TelegramAdapter
from tests.gateway.test_42039_duplicate_user_message import _bootstrap


def test_routine_startup_preserves_pending_updates():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test"))

    assert adapter._drop_pending_updates_on_connect(is_reconnect=False) is False


def test_reconnect_preserves_pending_updates():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test"))

    assert adapter._drop_pending_updates_on_connect(is_reconnect=True) is False


def test_explicit_reset_signal_is_the_only_connect_drop_authority():
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="test",
            extra={"drop_pending_updates": True},
        )
    )

    assert adapter._drop_pending_updates_on_connect(is_reconnect=False) is True
    assert adapter._drop_pending_updates_on_connect(is_reconnect=True) is False


@pytest.mark.parametrize("marker", [".restart_notify.json", ".restart_pending.json"])
def test_planned_restart_marker_overrides_explicit_drop_signal(
    monkeypatch, tmp_path, marker
):
    """A planned lifecycle restart must preserve updates received while down."""

    (tmp_path / marker).write_text("{}", encoding="utf-8")
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="test",
            extra={"drop_pending_updates": True},
        )
    )

    assert adapter._drop_pending_updates_on_connect(is_reconnect=False) is False


@pytest.mark.asyncio
async def test_authorized_telegram_input_is_committed_during_planned_drain(
    monkeypatch, tmp_path
):
    runner = _bootstrap(monkeypatch, tmp_path)
    db = SessionDB(tmp_path / "state.db")
    runner._session_db = AsyncSessionDB(db)
    runner._draining = True
    runner._restart_requested = True
    runner._busy_input_mode = "interrupt"
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        thread_id="12345",
        user_id="42",
        message_id="700",
        profile="main",
        transport_profile="default",
    )
    event = MessageEvent(
        text="keep this through restart",
        message_type=MessageType.TEXT,
        source=source,
        raw_message=SimpleNamespace(message_id=700),
        message_id="700",
    )
    try:
        response = await runner._handle_message(event)
        assert response == "⏳ Gateway restarting — message safely queued."
        rows = await runner._session_db.list_gateway_drain_inbox_ready()
        assert [(row["message_id"], row["session_key"]) for row in rows] == [
            ("700", "agent:main:telegram:group:-1001:12345")
        ]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_authorized_telegram_input_is_committed_during_external_planned_restart(
    monkeypatch, tmp_path
):
    """Server-doctor maintenance must queue, not reject, a new Telegram turn."""

    runner = _bootstrap(monkeypatch, tmp_path)
    db = SessionDB(tmp_path / "state.db")
    runner._session_db = AsyncSessionDB(db)
    runner._external_drain_active = True
    runner._external_drain_restart_intent = True
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        thread_id="12345",
        user_id="42",
        message_id="701",
        profile="main",
        transport_profile="default",
    )
    event = MessageEvent(
        text="queue this maintenance correction",
        message_type=MessageType.TEXT,
        source=source,
        raw_message=SimpleNamespace(message_id=701),
        message_id="701",
    )
    try:
        response = await runner._handle_message(event)
        assert response == "⏳ Gateway restarting — message safely queued."
        rows = await runner._session_db.list_gateway_drain_inbox_ready()
        assert [(row["message_id"], row["session_key"]) for row in rows] == [
            ("701", "agent:main:telegram:group:-1001:12345")
        ]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_stop_bypasses_planned_drain_inbox_after_active_turn_clears(
    monkeypatch, tmp_path
):
    """A late /stop remains control traffic after the busy owner disappears."""

    runner = _bootstrap(monkeypatch, tmp_path)
    db = SessionDB(tmp_path / "state.db")
    runner._session_db = AsyncSessionDB(db)
    runner._draining = True
    runner._restart_requested = True
    runner._handle_stop_command = AsyncMock(return_value="No active task to stop.")
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        thread_id="12345",
        user_id="42",
        message_id="702",
        profile="main",
        transport_profile="default",
    )
    event = MessageEvent(
        text="/stop@hermes_test_bot",
        message_type=MessageType.TEXT,
        source=source,
        raw_message=SimpleNamespace(message_id=702),
        message_id="702",
    )
    try:
        response = await runner._handle_message(event)

        assert response == "No active task to stop."
        runner._handle_stop_command.assert_awaited_once_with(event)
        assert await runner._session_db.list_gateway_drain_inbox_ready() == []
    finally:
        db.close()


@pytest.mark.asyncio
async def test_planned_quiesce_waits_for_handler_created_batch():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test"))
    order = []

    async def _batch():
        await asyncio.sleep(0)
        order.append("batch")

    async def _stop_application():
        order.append("application")
        adapter._pending_text_batch_tasks["late"] = asyncio.create_task(_batch())

    updater = SimpleNamespace(running=True, stop=AsyncMock())
    app = SimpleNamespace(
        updater=updater,
        running=True,
        stop=_stop_application,
    )
    adapter._app = app

    await adapter.quiesce_inbound()

    updater.stop.assert_awaited_once()
    assert order == ["application", "batch"]
    assert adapter._planned_ingress_quiesced is True


@pytest.mark.asyncio
async def test_runner_planned_quiesce_uses_live_gateway_adapters(
    monkeypatch, tmp_path
):
    """Production shutdown must quiesce the adapters the runner actually owns."""

    runner = _bootstrap(monkeypatch, tmp_path)
    telegram = SimpleNamespace(
        platform=Platform.TELEGRAM,
        quiesce_inbound=AsyncMock(),
    )
    api = SimpleNamespace(
        platform=Platform.API_SERVER,
        quiesce_inbound=AsyncMock(),
    )
    runner.adapters = {Platform.TELEGRAM: telegram, Platform.API_SERVER: api}
    runner._profile_adapters = {
        "hermesdev": {Platform.TELEGRAM: telegram},
    }
    runner._restart_requested = True
    runner._drain_ingress_admission_tasks = set()

    await runner._quiesce_planned_restart_ingress()

    telegram.quiesce_inbound.assert_awaited_once()
    api.quiesce_inbound.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_external_planned_restart_quiesces_live_gateway_adapters(
    monkeypatch, tmp_path
):
    runner = _bootstrap(monkeypatch, tmp_path)
    telegram = SimpleNamespace(
        platform=Platform.TELEGRAM,
        quiesce_inbound=AsyncMock(),
    )
    runner.adapters = {Platform.TELEGRAM: telegram}
    runner._profile_adapters = {}
    runner._restart_requested = False
    runner._external_drain_restart_intent = True
    runner._drain_ingress_admission_tasks = set()

    await runner._quiesce_planned_restart_ingress()

    telegram.quiesce_inbound.assert_awaited_once()
