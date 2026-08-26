"""Synthetic watcher turns must not impersonate an inbound platform message."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource, build_session_key
from tools.async_delegation import TrustedRestartEvent
from tools.parent_task_barrier import TrustedParentTaskContinuation


class _CaptureAdapter:
    supports_async_delivery = True

    def __init__(self) -> None:
        self.handle_message = AsyncMock()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "marker_name"),
    [
        (TrustedRestartEvent, "_hermes_trusted_restart_event"),
        (
            TrustedParentTaskContinuation,
            "_hermes_parent_task_continuation",
        ),
    ],
)
async def test_trusted_watch_turn_does_not_reuse_platform_message_identity(
    event_type,
    marker_name,
) -> None:
    """A restart/continuation wake gets its own synthetic authority row."""
    origin = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003971448755",
        chat_type="group",
        thread_id="30162",
        user_id="617744661",
        message_id="46142",
    )
    event = event_type(
        {
            "type": "restart" if event_type is TrustedRestartEvent else "parent",
            "session_key": build_session_key(origin),
            "message_id": "46142",
        }
    )
    adapter = _CaptureAdapter()
    runner = GatewayRunner(GatewayConfig())
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._build_process_event_source = lambda _event: origin

    accepted = await runner._inject_watch_notification(
        "[INTERNAL CONTINUATION] continue the interrupted task",
        event,
    )

    assert accepted is True
    adapter.handle_message.assert_awaited_once()
    synthetic = adapter.handle_message.await_args.args[0]
    assert synthetic.internal is True
    assert synthetic.message_id is None
    assert synthetic.source.message_id is None
    assert runner._turn_platform_message_id(synthetic) is None
    assert getattr(synthetic, marker_name) is event
    assert origin.message_id == "46142"
