from __future__ import annotations

import json

import pytest

from gateway.deferred_event_spool import (
    discard_deferred_events_for_session,
    load_replayable_deferred_events,
    persist_deferred_event,
)
from gateway.platforms.base import MessageEvent, MessageType, merge_pending_message_event
from gateway.run import GatewayRunner, ParentBarrierDeferralError
from hermes_state import SessionDB
from tests.gateway.restart_test_helpers import make_restart_source


@pytest.fixture
def ledger_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        yield db
    finally:
        db.close()


def _event(message_id: str = "m-deferred") -> MessageEvent:
    source = make_restart_source(chat_id="chat-1")
    source.thread_id = "topic-1"
    source.message_id = message_id
    return MessageEvent(
        text="continue numbering",
        message_type=MessageType.TEXT,
        source=source,
        user_id="user-1",
        user_name="Owner",
        message_id=message_id,
        platform_update_id=987,
        reply_to_message_id="parent-1",
        reply_to_text="previous list",
        channel_prompt="current channel policy",
        channel_context="current channel context",
        metadata={"safe": True},
    )


def _runner(db) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner._session_db = db
    runner._session_key_for_source = lambda source: "agent:main:telegram:group:chat-1:topic-1"
    return runner


def _record(runner: GatewayRunner, event: MessageEvent, session_key: str = "sk") -> int:
    ledger_id = runner._record_gateway_ledger_received(event, session_key=session_key)
    assert ledger_id is not None
    return ledger_id


@pytest.mark.asyncio
async def test_barrier_inspection_failure_defers_only_affected_route(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    runner._session_key_for_source = (
        lambda source: f"agent:main:telegram:group:chat-1:{source.thread_id}"
    )
    affected = _event("affected")
    affected.source.thread_id = "topic-a"
    unrelated = _event("unrelated")
    unrelated.source.thread_id = "topic-b"
    affected_id = _record(runner, affected, runner._session_key_for_source(affected.source))
    unrelated_id = _record(runner, unrelated, runner._session_key_for_source(unrelated.source))

    async def broken_inspection(**_kwargs):
        raise __import__("sqlite3").OperationalError("disk I/O error")

    runner._park_user_event_for_parent_barrier = broken_inspection
    assert await runner._park_user_event_or_defer_on_inspection_failure(
        event=affected,
        session_key=runner._session_key_for_source(affected.source),
        parent_session_id="parent-a",
    ) is True

    spool_files = list((tmp_path / "hermes-home" / "deferred_events").glob("*.json"))
    assert len(spool_files) == 1
    affected_row = ledger_db.get_gateway_message_ledger(affected_id)
    unrelated_row = ledger_db.get_gateway_message_ledger(unrelated_id)
    assert affected_row["status"] == "requeued"
    assert affected_row["dispatch_started_at"] is None
    assert unrelated_row["status"] == "received"
    assert unrelated_row["dispatch_started_at"] is None


@pytest.mark.asyncio
async def test_barrier_failure_without_ledger_is_not_reported_deferred(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    event = _event("missing-ledger")

    async def broken_inspection(**_kwargs):
        raise __import__("sqlite3").OperationalError("disk I/O error")

    runner._park_user_event_for_parent_barrier = broken_inspection
    with pytest.raises(ParentBarrierDeferralError):
        await runner._park_user_event_or_defer_on_inspection_failure(
            event=event,
            session_key="agent:main:telegram:group:chat-1:topic-1",
            parent_session_id="parent",
        )
    assert not list((tmp_path / "hermes-home" / "deferred_events").glob("*.json"))


def test_deferred_event_is_private_and_round_trips(monkeypatch, tmp_path, ledger_db):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event()
    runner = _runner(ledger_db)
    _record(runner, event, "agent:main:telegram:group:chat-1:topic-1")
    runner._update_gateway_ledger(event, "requeued")

    path = persist_deferred_event(
        event,
        session_key="agent:main:telegram:group:chat-1:topic-1",
    )
    entries = load_replayable_deferred_events(ledger_db)

    assert path is not None
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert len(entries) == 1
    restored = entries[0].event
    assert restored.text == event.text
    assert restored.message_id == event.message_id
    assert restored.reply_to_message_id == "parent-1"
    assert restored.reply_to_text == "previous list"
    assert restored.source.thread_id == "topic-1"
    assert restored.platform_update_id == 987
    assert restored.channel_prompt == "current channel policy"
    assert restored.channel_context == "current channel context"
    assert restored.metadata == {"safe": True}


@pytest.mark.asyncio
async def test_restart_replays_only_undispatched_deferred_event(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event()
    first = _runner(ledger_db)
    _record(first, event, "agent:main:telegram:group:chat-1:topic-1")
    first._set_gateway_ledger_deferred(event)
    spool_path = tmp_path / "hermes-home" / "deferred_events"
    assert len(list(spool_path.glob("*.json"))) == 1

    second = _runner(ledger_db)
    second._startup_restore_queue = []
    seen = []

    class Adapter:
        async def handle_message(self, restored_event):
            seen.append(restored_event.text)

    second._adapter_for_source = lambda source: Adapter()

    restored = second._restore_spooled_deferred_events()
    assert len(restored) == 1
    second._startup_restore_queue[0:0] = restored
    assert await second._drain_startup_restore_queue() == 1
    assert seen == [event.text]
    assert list(spool_path.glob("*.json")) == []
    row = ledger_db.find_gateway_message_ledger(
        platform="telegram",
        chat_id="chat-1",
        thread_id="topic-1",
        message_id=event.message_id,
    )
    assert row["status"] == "in_progress"
    assert row["dispatch_started_at"] is not None
    assert row["reason"] == "startup-replay-claimed"


@pytest.mark.asyncio
async def test_duplicate_startup_delivery_is_claimed_once(monkeypatch, tmp_path, ledger_db):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event("duplicate")
    runner = _runner(ledger_db)
    _record(runner, event, "sk")
    runner._set_gateway_ledger_deferred(event)

    restored = load_replayable_deferred_events(ledger_db)[0].event
    runner._startup_restore_queue = [restored, event]
    seen = []

    class Adapter:
        async def handle_message(self, claimed):
            seen.append(claimed.message_id)

    runner._adapter_for_source = lambda source: Adapter()
    assert await runner._drain_startup_restore_queue() == 1
    assert seen == ["duplicate"]


@pytest.mark.asyncio
async def test_busy_startup_handoff_keeps_replay_owner_until_real_dispatch(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    event = _event("busy-replay")
    ledger_id = _record(runner, event, "busy-key")
    assert runner._set_gateway_ledger_deferred(event)
    runner._startup_restore_queue = [event]

    class BusyAdapter:
        def __init__(self):
            self.pending = []

        async def handle_message(self, queued):
            self.pending.append(queued)
            queued._hermes_adapter_handoff = "queued"

    adapter = BusyAdapter()
    runner._adapter_for_source = lambda source: adapter

    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 1
    row = ledger_db.get_gateway_message_ledger(ledger_id)
    assert row["status"] == "requeued"
    assert row["dispatch_started_at"] is None
    assert len(adapter.pending) == 1
    assert len(list((tmp_path / "hermes-home" / "deferred_events").glob("*.json"))) == 1
    assert len(load_replayable_deferred_events(ledger_db)) == 1


def test_started_dispatch_is_never_replayed(monkeypatch, tmp_path, ledger_db):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event()
    runner = _runner(ledger_db)
    _record(runner, event, "sk")
    path = persist_deferred_event(event, session_key="sk")
    assert path is not None
    runner._update_gateway_ledger(event, "in_progress")

    assert load_replayable_deferred_events(ledger_db) == []
    assert not path.exists()


def test_missing_ledger_preserves_spool_without_replay(monkeypatch, tmp_path, ledger_db):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event("missing-ledger")
    setattr(event, "_hermes_gateway_ledger_id", 999)
    path = persist_deferred_event(event, session_key="sk")

    assert path is not None
    assert load_replayable_deferred_events(ledger_db) == []
    assert path.exists()


def test_merged_pending_events_replay_as_physical_messages_in_ledger_order(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    first = _event("part-1")
    second = _event("part-2")
    first.text = "line one"
    second.text = "line two"
    for event in (first, second):
        _record(runner, event, "sk")
        runner._set_gateway_ledger_deferred(event)

    pending = {"sk": first}
    merge_pending_message_event(pending, "sk", second, merge_text=True)

    entries = load_replayable_deferred_events(ledger_db)
    assert pending["sk"].text == "line one\nline two"
    assert [entry.event.text for entry in entries] == ["line one", "line two"]
    assert runner._update_gateway_ledger(pending["sk"], "in_progress")
    spool_dir = tmp_path / "hermes-home" / "deferred_events"
    assert list(spool_dir.glob("*.json")) == []


def test_internal_continuation_is_not_spooled(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event("internal")
    event.internal = True
    setattr(event, "_hermes_gateway_ledger_id", 1)
    assert persist_deferred_event(event, session_key="sk") is None


def test_durable_internal_goal_is_spooled_and_replayable(monkeypatch, tmp_path, ledger_db):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    event = _event("unused-platform-id")
    event.message_id = None
    event.internal = True
    event.metadata = {"durable_internal_goal": True}
    ledger_id = _record(runner, event, "sk-goal")

    assert runner._set_gateway_ledger_deferred(event)
    entries = load_replayable_deferred_events(ledger_db)

    assert len(entries) == 1
    assert entries[0].ledger_id == ledger_id
    assert entries[0].session_key == runner._session_key_for_source(event.source)
    assert entries[0].event.internal is True
    assert entries[0].event.metadata == {"durable_internal_goal": True}


@pytest.mark.parametrize(
    "trust_attr",
    ["role_authorized", "delivered_via_upstream_relay", "is_bot"],
)
def test_transport_trust_is_never_persisted(
    monkeypatch, tmp_path, trust_attr
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event(f"trusted-{trust_attr}")
    setattr(event.source, trust_attr, True)
    setattr(event, "_hermes_gateway_ledger_id", 1)
    assert persist_deferred_event(event, session_key="sk") is None


def test_session_reset_has_a_ledger_cutoff(monkeypatch, tmp_path, ledger_db):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    session_key = "agent:main:telegram:group:chat-1:topic-1"
    before = _event("before-reset")
    _record(runner, before, session_key)
    runner._set_gateway_ledger_deferred(before)
    reset_id = ledger_db.record_gateway_message_received(
        platform="telegram",
        chat_id="chat-1",
        thread_id="topic-1",
        message_id="reset-command",
        session_key=session_key,
    )
    after = _event("after-reset")
    _record(runner, after, session_key)
    runner._set_gateway_ledger_deferred(after)

    assert discard_deferred_events_for_session(
        ledger_db,
        session_key,
        reason="session-reset",
        max_ledger_id=reset_id,
    ) == 1
    before_row = ledger_db.find_gateway_message_ledger(
        platform="telegram", chat_id="chat-1", thread_id="topic-1", message_id="before-reset"
    )
    after_row = ledger_db.find_gateway_message_ledger(
        platform="telegram", chat_id="chat-1", thread_id="topic-1", message_id="after-reset"
    )
    assert before_row["status"] == "drained"
    assert after_row["status"] == "requeued"
    assert [entry.event.message_id for entry in load_replayable_deferred_events(ledger_db)] == [
        "after-reset"
    ]


@pytest.mark.asyncio
async def test_fresh_startup_event_without_ledger_still_dispatches(ledger_db):
    runner = _runner(ledger_db)
    event = _event("best-effort")
    runner._startup_restore_queue = [event]
    seen = []

    class Adapter:
        async def handle_message(self, queued):
            seen.append(queued.message_id)

    runner._adapter_for_source = lambda source: Adapter()
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 1
    assert seen == ["best-effort"]


@pytest.mark.asyncio
async def test_fresh_startup_event_survives_ledger_lookup_outage():
    class BrokenDB:
        def find_gateway_message_ledger(self, **kwargs):
            raise RuntimeError("ledger unavailable")

    runner = _runner(BrokenDB())
    event = _event("lookup-outage")
    runner._startup_restore_queue = [event]
    seen = []

    class Adapter:
        async def handle_message(self, queued):
            seen.append(queued.message_id)

    runner._adapter_for_source = lambda source: Adapter()
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 1
    assert seen == ["lookup-outage"]


@pytest.mark.asyncio
async def test_unavailable_adapter_does_not_block_available_tail(ledger_db):
    runner = _runner(ledger_db)
    blocked = _event("blocked")
    available = _event("available")
    runner._startup_restore_queue = [blocked, available]
    seen = []

    class Adapter:
        async def handle_message(self, queued):
            seen.append(queued.message_id)

    runner._adapter_for_source = lambda source: (
        None if getattr(source, "message_id", None) == "blocked" else Adapter()
    )
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 1
    assert seen == ["available"]
    assert runner._startup_restore_queue == [blocked]


@pytest.mark.asyncio
async def test_failed_adapter_handoff_releases_claim_and_keeps_spool(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    event = _event("handoff-failure")
    _record(runner, event, "sk")
    runner._set_gateway_ledger_deferred(event)
    runner._startup_restore_queue = [event]

    class Adapter:
        async def handle_message(self, queued):
            raise RuntimeError("not accepted")

    runner._adapter_for_source = lambda source: Adapter()
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 0
    assert runner._startup_restore_queue == [event]
    row = ledger_db.get_gateway_message_ledger(
        getattr(event, "_hermes_gateway_ledger_id")
    )
    assert row["status"] == "requeued"
    assert row["dispatch_started_at"] is None
    assert list((tmp_path / "hermes-home" / "deferred_events").glob("*.json"))


@pytest.mark.asyncio
async def test_claim_release_db_outage_keeps_event_for_retry(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    event = _event("release-outage")
    _record(runner, event, "sk")
    runner._set_gateway_ledger_deferred(event)
    runner._startup_restore_queue = [event]

    class FailingAdapter:
        async def handle_message(self, queued):
            raise RuntimeError("not accepted")

    original_release = runner._release_deferred_event_dispatch_claim

    def broken_release(*args, **kwargs):
        raise RuntimeError("release db outage")

    runner._release_deferred_event_dispatch_claim = broken_release
    runner._adapter_for_source = lambda source: FailingAdapter()
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 0
    assert runner._startup_restore_queue == [event]
    assert getattr(event, "_hermes_startup_claim_release_pending") is True

    seen = []

    class WorkingAdapter:
        async def handle_message(self, queued):
            seen.append(queued.message_id)

    runner._release_deferred_event_dispatch_claim = original_release
    runner._adapter_for_source = lambda source: WorkingAdapter()
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 1
    assert seen == ["release-outage"]
    assert runner._startup_restore_queue == []


def test_historical_internal_spool_is_rejected(monkeypatch, tmp_path, ledger_db):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    event = _event("historical-internal")
    _record(runner, event, "sk")
    runner._set_gateway_ledger_deferred(event)
    path = next((tmp_path / "hermes-home" / "deferred_events").glob("*.json"))
    payload = json.loads(path.read_text())
    payload["event"]["internal"] = True
    path.write_text(json.dumps(payload))

    assert load_replayable_deferred_events(ledger_db) == []
    assert not path.exists()
    row = ledger_db.get_gateway_message_ledger(
        getattr(event, "_hermes_gateway_ledger_id")
    )
    assert row["status"] == "drained"
