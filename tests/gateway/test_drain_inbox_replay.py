"""Planned-restart Telegram inbox replay stays route-bound and FIFO."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import gateway.run as gateway_run
import hermes_constants
from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource
from hermes_state import AsyncSessionDB, SessionDB
from tests.gateway.test_42039_duplicate_user_message import _bootstrap


class _RecordingTelegramAdapter:
    platform = Platform.TELEGRAM
    _owner_profile = "default"

    def __init__(self):
        self.events = []

    async def handle_message(self, event):
        async def _consume():
            self.events.append(event)

        return asyncio.create_task(_consume())


class _BusyQueueTelegramAdapter(_RecordingTelegramAdapter):
    async def handle_message(self, event):
        self.events.append(event)
        event._hermes_busy_admitted = True
        return None


def _event(message_id, text, *, thread_id="12345", message_type=MessageType.TEXT):
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        thread_id=thread_id,
        user_id="42",
        message_id=str(message_id),
        transport_profile="default",
    )
    return MessageEvent(
        text=text,
        message_type=message_type,
        source=source,
        raw_message=SimpleNamespace(message_id=int(message_id)),
        message_id=str(message_id),
        user_id="42",
    )


def _runner(monkeypatch, tmp_path):
    runner = _bootstrap(monkeypatch, tmp_path)
    db = SessionDB(tmp_path / "state.db")
    runner._session_db = AsyncSessionDB(db)
    runner.session_store.peek_session_id = lambda _key: None
    runner.config.multiplex_profiles = False
    adapter = _RecordingTelegramAdapter()
    runner.adapters = {Platform.TELEGRAM: adapter}
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(
        gateway_run, "_profile_runtime_scope", lambda _home: nullcontext()
    )
    return runner, db, adapter


@pytest.mark.asyncio
async def test_drain_replay_is_fifo_exact_route_and_terminal(monkeypatch, tmp_path):
    runner, db, adapter = _runner(monkeypatch, tmp_path)
    session_key = "agent:main:telegram:group:-1001:12345"
    try:
        for message_id, text in (("700", "first"), ("701", "second")):
            response = await runner._admit_planned_restart_event(
                _event(message_id, text), session_key
            )
            assert response == "⏳ Gateway restarting — message safely queued."

        session_id = "drain-fifo-session"
        holder = "gateway-turn-holder"
        db.create_session(session_id, source="telegram")
        assert db.acquire_session_turn_lease(
            session_id, holder, wait_seconds=0.1
        )

        # Per-route ownership admits only the head.  The canonical durable
        # user-row handoff releases the next row; this is the same boundary
        # used by production before model dispatch.
        assert await runner._replay_gateway_drain_inbox() == 1
        await asyncio.sleep(0)
        assert [event.text for event in adapter.events] == ["first"]
        await runner._persist_gateway_triggering_user_row(
            adapter.events[0],
            SimpleNamespace(session_id=session_id),
            {"db": db, "holder": holder, "ttl_seconds": 300.0},
            platform_message_id="700",
            display_kind=None,
        )
        for _ in range(50):
            if len(adapter.events) == 2:
                break
            await asyncio.sleep(0.01)
        assert len(adapter.events) == 2
        await runner._persist_gateway_triggering_user_row(
            adapter.events[1],
            SimpleNamespace(session_id=session_id),
            {"db": db, "holder": holder, "ttl_seconds": 300.0},
            platform_message_id="701",
            display_kind=None,
        )
        assert [event.text for event in adapter.events] == ["first", "second"]
        assert [event.source.thread_id for event in adapter.events] == [
            "12345",
            "12345",
        ]
        assert [
            (event.source.message_id, event.message_id) for event in adapter.events
        ] == [("700", "700"), ("701", "701")]
        with db._read_ctx() as conn:
            rows = conn.execute(
                "SELECT state, payload_json FROM gateway_drain_inbox ORDER BY sequence"
            ).fetchall()
            assert [(row["state"], row["payload_json"]) for row in rows] == [
                ("TERMINAL", None),
                ("TERMINAL", None),
            ]
            assert conn.execute(
                "SELECT COUNT(*) FROM gateway_message_ledger WHERE status='completed'"
            ).fetchone()[0] == 2
    finally:
        db.close()


@pytest.mark.asyncio
async def test_drain_redelivery_deduplicates_before_replay(monkeypatch, tmp_path):
    runner, db, adapter = _runner(monkeypatch, tmp_path)
    session_key = "agent:main:telegram:group:-1001:12345"
    event = _event("702", "same delivery")
    try:
        assert "safely queued" in await runner._admit_planned_restart_event(
            event, session_key
        )
        assert "safely queued" in await runner._admit_planned_restart_event(
            _event("702", "same delivery"), session_key
        )

        assert await runner._replay_gateway_drain_inbox() == 1
        assert [item.message_id for item in adapter.events] == ["702"]
        with db._read_ctx() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM gateway_drain_inbox"
            ).fetchone()[0] == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_drain_digest_corruption_is_quarantined_not_retried(
    monkeypatch, tmp_path
):
    runner, db, adapter = _runner(monkeypatch, tmp_path)
    session_key = "agent:main:telegram:group:-1001:12345"
    try:
        assert "safely queued" in await runner._admit_planned_restart_event(
            _event("703", "corrupt me"), session_key
        )
        db._execute_write(
            lambda conn: conn.execute(
                "UPDATE gateway_drain_inbox SET payload_json='{}' WHERE message_id='703'"
            )
        )

        assert await runner._replay_gateway_drain_inbox() == 0
        assert adapter.events == []
        with db._read_ctx() as conn:
            row = conn.execute(
                "SELECT state, failure_reason FROM gateway_drain_inbox WHERE message_id='703'"
            ).fetchone()
            assert row["state"] == "CANCELLED"
            assert row["failure_reason"] == "invalid_envelope:ValueError"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_drain_replay_accepts_busy_queue_without_owner_task(
    monkeypatch, tmp_path
):
    runner, db, _adapter = _runner(monkeypatch, tmp_path)
    adapter = _BusyQueueTelegramAdapter()
    runner.adapters = {Platform.TELEGRAM: adapter}
    session_key = "agent:main:telegram:group:-1001:12345"
    try:
        assert "safely queued" in await runner._admit_planned_restart_event(
            _event("704", "queue behind resumed work"), session_key
        )

        assert await runner._replay_gateway_drain_inbox() == 1
        assert [item.message_id for item in adapter.events] == ["704"]
        row = next(iter(db.list_gateway_drain_inbox_ready()), None)
        assert row is None
        claimed = db.get_gateway_drain_inbox(
            adapter.events[0]._gateway_drain_inbox_claim["inbox_id"]
        )
        assert claimed["state"] == "LEASED"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_startup_drain_interrupt_is_priority_but_queue_waits_for_resume(
    monkeypatch, tmp_path
):
    runner, db, adapter = _runner(monkeypatch, tmp_path)
    interrupt_key = "agent:main:telegram:group:-1001:interrupt-topic"
    queue_key = "agent:main:telegram:group:-1001:queue-topic"
    runner._startup_restore_in_progress = True
    runner._startup_restore_queue = []
    runner._startup_restore_priority_session_keys = set()
    runner._effective_busy_input_mode = lambda source: (
        "queue" if source.thread_id == "queue-topic" else "interrupt"
    )
    try:
        assert "safely queued" in await runner._admit_planned_restart_event(
            _event("705", "correct resumed work", thread_id="interrupt-topic"),
            interrupt_key,
        )
        assert "safely queued" in await runner._admit_planned_restart_event(
            _event("706", "run after resumed work", thread_id="queue-topic"),
            queue_key,
        )

        assert await runner._replay_gateway_drain_inbox() == 2
        assert adapter.events == []
        assert [
            item.source.thread_id for item in runner._startup_restore_queue
        ] == ["interrupt-topic", "queue-topic"]
        assert runner._startup_restore_priority_session_keys == {interrupt_key}
    finally:
        db.close()
