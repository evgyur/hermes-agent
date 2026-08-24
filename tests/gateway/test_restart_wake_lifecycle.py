from __future__ import annotations

import json
import queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform
from gateway.run import GatewayRunner
from tools import async_delegation as ad
from tools.process_registry import ProcessRegistry


def test_cold_registry_restores_typed_restart_wake(tmp_path, monkeypatch):
    monkeypatch.setattr(ad, "get_hermes_home", lambda: tmp_path)
    ad._reset_for_tests()
    with ad._transaction() as conn:
        conn.execute(
            """INSERT INTO async_delegations(
                   delegation_id, origin_session, state, dispatched_at,
                   updated_at, heartbeat_at, delivery_state, task_json,
                   restart_policy, restart_nonce, restart_budget,
                   child_session_ids_json, child_capability_names_json)
               VALUES ('cold', 'telegram:origin', 'restart_pending', 1, 1, 1,
                       'pending', '{}', 'gateway_owned_v1', 'nonce', 3,
                       ?, '[[]]')""",
            (json.dumps(["child"]),),
        )
    registry = ProcessRegistry()
    wake = registry.completion_queue.get_nowait()
    assert isinstance(wake, ad.TrustedRestartEvent)
    assert wake["delegation_id"] == "cold"
    assert registry.completion_queue.empty()


@pytest.mark.asyncio
async def test_watcher_accepts_restart_type_and_retains_failed_delivery():
    events: queue.Queue = queue.Queue()
    wake = ad.TrustedRestartEvent(
        type="async_delegation_restart",
        delegation_id="watch",
        session_key="telegram:origin",
        restart_nonce="nonce",
    )
    events.put(wake)
    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._inject_watch_notification = AsyncMock(return_value=False)
    sleeps = 0

    async def stop_after_tick(_delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            runner._running = False

    with (
        patch(
            "tools.process_registry.process_registry",
            SimpleNamespace(completion_queue=events),
        ),
        patch(
            "tools.parent_task_barrier.claim_next_ready_continuation",
            return_value=None,
        ),
        patch("gateway.run.asyncio.sleep", side_effect=stop_after_tick),
    ):
        await runner._async_delegation_watcher(interval=0)

    runner._inject_watch_notification.assert_awaited_once()
    assert events.get_nowait() is wake


@pytest.mark.asyncio
async def test_non_push_route_never_downgrades_restart_capability():
    wake = ad.TrustedRestartEvent(
        type="async_delegation_restart",
        delegation_id="api",
        session_key="raw-session",
        restart_nonce="nonce",
    )
    runner = object.__new__(GatewayRunner)
    runner.adapters = {
        Platform.API_SERVER: SimpleNamespace(supports_async_delivery=False)
    }
    runner._build_process_event_source = lambda _evt: None
    assert await runner._inject_watch_notification("recovery", wake) is False
