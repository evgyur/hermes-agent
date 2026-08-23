from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.deferred_event_spool import (
    discard_deferred_events_for_session,
    load_replayable_deferred_events,
    persist_deferred_event,
    persist_preledger_event,
)
from gateway.platforms.base import MessageEvent, MessageType, merge_pending_message_event
from gateway.run import GatewayRunner, ParentBarrierDeferralError
from gateway.slash_commands import GatewaySlashCommandsMixin
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
    runner._is_user_authorized = lambda source: True
    return runner


def _record(runner: GatewayRunner, event: MessageEvent, session_key: str = "sk") -> int:
    ledger_id = runner._record_gateway_ledger_received(event, session_key=session_key)
    assert ledger_id is not None
    return ledger_id


@pytest.mark.asyncio
async def test_busy_startup_replay_preserves_its_original_ledger_identity(ledger_db):
    """A busy startup replay must not be admitted/finalized as fresh ingress."""
    runner = _runner(ledger_db)
    event = _event("busy-owned-identity")
    _record(runner, event, "busy-key")
    event._hermes_startup_restore_replay = True
    runner._record_gateway_ledger_received = MagicMock(
        side_effect=AssertionError("startup replay minted a second ledger identity")
    )
    runner._finalize_gateway_ledger_after_handler = MagicMock(
        side_effect=AssertionError("busy RAM handoff terminalized the replay owner")
    )
    runner._handle_active_session_busy_message_impl = AsyncMock(return_value=True)

    assert await runner._handle_active_session_busy_message(event, "busy-key") is True
    runner._record_gateway_ledger_received.assert_not_called()
    runner._finalize_gateway_ledger_after_handler.assert_not_called()


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
async def test_preledger_event_is_admitted_once_after_storage_recovers(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event("preledger")
    path = persist_preledger_event(event, session_key="sk")
    assert path is not None
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    runner = _runner(ledger_db)
    runner._startup_restore_queue = [load_replayable_deferred_events(ledger_db)[0].event]
    seen = []

    class Adapter:
        async def handle_message(self, queued):
            seen.append(queued.message_id)

    runner._adapter_for_source = lambda source: Adapter()
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 1
    assert seen == ["preledger"]
    assert path.exists()  # dispatch claim is not terminal ownership
    row = ledger_db.find_gateway_message_ledger(
        platform="telegram",
        chat_id="chat-1",
        thread_id="topic-1",
        message_id="preledger",
    )
    assert row["status"] == "in_progress"
    assert row["dispatch_started_at"] is not None


@pytest.mark.asyncio
async def test_unauthorized_preledger_replay_is_dropped_before_ledger(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event("unauthorized-preledger")
    path = persist_preledger_event(event, session_key="sk")
    runner = _runner(ledger_db)
    runner._is_user_authorized = lambda source: False
    restored = load_replayable_deferred_events(ledger_db)[0].event
    runner._startup_restore_queue = [restored]

    class Adapter:
        async def handle_message(self, queued):
            raise AssertionError("unauthorized event reached adapter")

    runner._adapter_for_source = lambda source: Adapter()
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 0
    assert runner._startup_restore_queue == []
    assert path is not None and not path.exists()
    assert ledger_db.find_gateway_message_ledger(
        platform="telegram",
        chat_id="chat-1",
        thread_id="topic-1",
        message_id="unauthorized-preledger",
    ) is None


def test_preledger_reset_cutoff_preserves_newer_ingress(monkeypatch, tmp_path, ledger_db):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    before = _event("preledger-before")
    after = _event("preledger-after")
    before_path = persist_preledger_event(before, session_key="sk")
    cutoff = int(getattr(before, "_hermes_preledger_created_ns")) + 1
    setattr(after, "_hermes_preledger_created_ns", cutoff + 1)
    after_path = persist_preledger_event(after, session_key="sk")

    assert discard_deferred_events_for_session(
        ledger_db,
        "sk",
        reason="session-reset",
        max_created_ns=cutoff,
    ) == 1
    assert before_path is not None and not before_path.exists()
    assert after_path is not None and after_path.exists()


@pytest.mark.asyncio
async def test_failed_reset_preserves_preledger_ingress(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    queued = _event("queued-before-failed-reset")
    path = persist_preledger_event(queued, session_key="sk")
    assert path is not None

    class ResetRunner(GatewaySlashCommandsMixin):
        pass

    class BrokenResetStore:
        async def reset_session(self, session_key):
            raise RuntimeError("sqlite reset outage")

    runner = ResetRunner()
    runner._session_key_for_source = lambda source: "sk"
    runner._invalidate_session_run_generation = lambda *args, **kwargs: None
    runner._release_running_agent_state = lambda *args, **kwargs: None
    runner.session_store = SimpleNamespace(_entries={})
    runner._agent_cache_lock = None
    runner._evict_cached_agent = lambda *args, **kwargs: None
    runner._queued_events = {}
    runner._session_db = ledger_db
    runner.async_session_store = BrokenResetStore()
    runner._discard_deferred_event_spool = lambda session_key, **kwargs: (
        discard_deferred_events_for_session(ledger_db, session_key, **kwargs)
    )

    with pytest.raises(RuntimeError, match="sqlite reset outage"):
        await runner._handle_reset_command(_event("reset-command"))
    assert path.exists()


@pytest.mark.parametrize(
    "trust_attr",
    ["role_authorized", "delivered_via_upstream_relay", "is_bot"],
)
def test_preledger_transport_trust_is_never_persisted(
    monkeypatch, tmp_path, trust_attr
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event(f"preledger-trusted-{trust_attr}")
    setattr(event.source, trust_attr, True)
    assert persist_preledger_event(event, session_key="sk") is None


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
    assert len(list(spool_path.glob("*.json"))) == 1
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


@pytest.mark.asyncio
async def test_scheduled_startup_handoff_timeout_restores_replay_owner(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    runner._startup_event_handoff_timeout_secs = 0.01
    event = _event("scheduled-timeout")
    ledger_id = _record(runner, event, "busy-key")
    assert runner._set_gateway_ledger_deferred(event)
    runner._startup_restore_queue = [event]

    class Adapter:
        async def handle_message(self, queued):
            queued._hermes_adapter_handoff = "scheduled"

    runner._adapter_for_source = lambda source: Adapter()
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 0
    assert runner._startup_restore_queue == [event]
    row = ledger_db.get_gateway_message_ledger(ledger_id)
    assert row["status"] == "requeued"
    assert row["reason"] == "startup-handoff-timeout"


@pytest.mark.asyncio
async def test_busy_startup_claim_release_failure_remains_restart_recoverable(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    event = _event("busy-release-failure")
    ledger_id = _record(runner, event, "busy-key")
    assert runner._set_gateway_ledger_deferred(event)
    runner._startup_restore_queue = [event]
    runner._release_deferred_event_dispatch_claim = lambda _event: False

    class BusyAdapter:
        async def handle_message(self, queued):
            queued._hermes_adapter_handoff = "queued"

    runner._adapter_for_source = lambda source: BusyAdapter()
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 0
    row = ledger_db.get_gateway_message_ledger(ledger_id)
    assert row["status"] == "in_progress"
    assert row["reason"] == "startup-replay-claimed"
    replayable = load_replayable_deferred_events(ledger_db)
    assert len(replayable) == 1
    assert replayable[0].event._hermes_startup_claim_release_pending is True


def test_started_dispatch_is_never_replayed(monkeypatch, tmp_path, ledger_db):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    event = _event()
    runner = _runner(ledger_db)
    _record(runner, event, "sk")
    path = persist_deferred_event(event, session_key="sk")
    assert path is not None
    runner._update_gateway_ledger(event, "in_progress")

    assert load_replayable_deferred_events(ledger_db) == []
    assert path.exists(), "dispatch claim is not terminal ownership"

    assert runner._update_gateway_ledger(event, "completed")
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
    assert len(list(spool_dir.glob("*.json"))) == 2


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
async def test_failure_after_agent_dispatch_is_never_replayed(
    monkeypatch, tmp_path, ledger_db
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runner = _runner(ledger_db)
    event = _event("ambiguous-agent-failure")
    ledger_id = _record(runner, event, "sk")
    runner._set_gateway_ledger_deferred(event)
    runner._startup_restore_queue = [event]

    class Adapter:
        async def handle_message(self, queued):
            setattr(queued, "_hermes_agent_dispatch_started", True)
            ledger_db.update_gateway_message_ledger(
                ledger_id,
                status="failed",
                reason="handler-error",
            )
            raise RuntimeError("ambiguous provider/tool outcome")

    runner._adapter_for_source = lambda source: Adapter()
    assert await runner._drain_startup_restore_queue(schedule_retry=False) == 0
    assert runner._startup_restore_queue == []
    assert ledger_db.get_gateway_message_ledger(ledger_id)["status"] == "failed"
    assert not list((tmp_path / "hermes-home" / "deferred_events").glob("*.json"))


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
