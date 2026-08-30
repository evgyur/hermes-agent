"""Regression tests for exact durable active-turn restart recovery.

A long-running gateway turn can outlive the legacy 120-second
``updated_at`` crash heuristic.  These tests require an exact persisted
marker, compare-and-swap cleanup, and promotion into the existing
``resume_pending`` recovery path after an unclean exit.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner
from gateway.session import (
    SessionEntry,
    SessionSource,
    SessionStore,
    resume_origin_from_snapshot,
)


ACTIVE_TURN_MAX_AGE_SECONDS = 60 * 60


def _make_source(chat_id: str = "active-turn-chat") -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        chat_id=chat_id,
        user_id="user-1",
        chat_type="channel",
        thread_id="thread-1",
    )


def _make_store(tmp_path) -> SessionStore:
    store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    # Exercise the legacy JSON fallback deterministically.  ``_save_entry``
    # must still persist correctly when state.db is unavailable.
    store._db = None
    return store


def _make_db_store(tmp_path) -> SessionStore:
    from hermes_state import SessionDB

    sessions_dir = tmp_path / "sessions"
    store = SessionStore(sessions_dir=sessions_dir, config=GatewayConfig())
    if store._db is not None:
        store._db.close()
    store._db = SessionDB(db_path=tmp_path / "state.db")
    return store


def _close_store_db(store: SessionStore) -> None:
    db = store._db
    assert db is not None
    db.close()


def _entry_for(store: SessionStore, source: SessionSource) -> SessionEntry:
    key = store._generate_session_key(source)
    with store._lock:
        store._ensure_loaded_locked()
        return store._entries[key]


def test_active_turn_fields_round_trip_and_legacy_payload_defaults(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)

    token = store.mark_turn_active(entry.session_key, source)
    assert token

    payload = _entry_for(store, source).to_dict()
    assert payload["active_turn_token"] == token
    assert payload["active_turn_started_at"] is not None
    assert payload["active_turn_origin_snapshot"]["active_turn_token"] == token

    restored = SessionEntry.from_dict(payload)
    assert restored.active_turn_token == token
    assert restored.active_turn_started_at is not None
    assert restored.active_turn_origin_snapshot is not None

    payload.pop("active_turn_token")
    payload.pop("active_turn_started_at")
    payload.pop("active_turn_origin_snapshot")
    legacy = SessionEntry.from_dict(payload)
    assert legacy.active_turn_token is None
    assert legacy.active_turn_started_at is None
    assert legacy.active_turn_origin_snapshot is None

    payload["active_turn_token"] = {"invalid": "not-a-token"}
    payload["active_turn_started_at"] = datetime.now().isoformat()
    corrupt = SessionEntry.from_dict(payload)
    assert corrupt.active_turn_token is None
    assert corrupt.active_turn_started_at is None


def test_mark_refreshes_updated_at_for_legacy_upgrade_fallback(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)
    old_updated_at = datetime.now() - timedelta(hours=2)
    with store._lock:
        store._entries[entry.session_key].updated_at = old_updated_at

    token = store.mark_turn_active(entry.session_key, source)

    assert token is not None
    assert _entry_for(store, source).updated_at > old_updated_at


def test_active_turn_clear_is_compare_and_swap(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)

    first = store.mark_turn_active(entry.session_key, source)
    second = store.mark_turn_active(entry.session_key, source)
    assert first is not None
    assert second is not None
    assert first != second

    assert store.clear_turn_active(entry.session_key, first) is False
    assert _entry_for(store, source).active_turn_token == second

    assert store.clear_turn_active(entry.session_key, second) is True
    current = _entry_for(store, source)
    assert current.active_turn_token is None
    assert current.active_turn_started_at is None
    assert current.active_turn_origin_snapshot is None


def test_active_turn_snapshot_binds_exact_live_source_to_resume(tmp_path):
    store = _make_db_store(tmp_path)
    live = SessionSource(
        platform=Platform.DISCORD,
        chat_id="same-route",
        user_id="user-2",
        chat_type="channel",
        thread_id="thread-1",
        message_id="message-2",
        scope_id="guild-2",
        profile="runtime-2",
        transport_profile="transport-2",
        external_safe_mode=True,
    )
    entry = store.get_or_create_session(live)

    token = store.mark_turn_active(entry.session_key, live)
    assert token is not None
    current = _entry_for(store, live)
    assert current.origin == live

    assert store.mark_resume_pending(entry.session_key, "restart_timeout") is True
    resumed = _entry_for(store, live)
    assert resume_origin_from_snapshot(resumed) == live
    obligation = store._db.get_gateway_resume_obligation(entry.session_key)
    assert obligation is not None
    assert obligation["state"] == "PENDING"
    assert obligation["resume_task_id"] == token == resumed.resume_task_id
    assert obligation["generation"] == resumed.continuation_generation


def test_resume_origin_snapshot_ignores_stale_entry_origin_mutation(tmp_path):
    store = _make_db_store(tmp_path)
    original = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="recipient-A",
        user_id="recipient-A",
        chat_type="dm",
        transport_profile="default",
    )
    entry = store.get_or_create_session(original)
    assert store.mark_turn_active(entry.session_key, original)
    assert store.mark_resume_pending(entry.session_key, "restart_timeout")
    resumed = _entry_for(store, original)
    obligation = store._db.get_gateway_resume_obligation(entry.session_key)
    assert obligation is not None
    assert obligation["state"] == "PENDING"
    assert obligation["resume_task_id"] == resumed.resume_task_id
    assert obligation["generation"] == resumed.continuation_generation

    with store._lock:
        store._entries[entry.session_key].origin = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="recipient-A",
            user_id="recipient-A",
            chat_type="dm",
            transport_profile="transport-B",
        )

    assert resume_origin_from_snapshot(_entry_for(store, original)) == original


def test_resume_origin_snapshot_is_bound_to_continuation_cas(tmp_path):
    store = _make_db_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)
    assert store.mark_turn_active(entry.session_key, source)
    assert store.mark_resume_pending(entry.session_key, "restart_timeout")
    resumed = _entry_for(store, source)
    assert resume_origin_from_snapshot(resumed) == source
    obligation = store._db.get_gateway_resume_obligation(entry.session_key)
    assert obligation is not None
    assert obligation["state"] == "PENDING"
    assert obligation["resume_task_id"] == resumed.resume_task_id
    assert obligation["generation"] == resumed.continuation_generation

    with store._lock:
        store._entries[entry.session_key].continuation_claim_token = "new-claim"

    assert resume_origin_from_snapshot(_entry_for(store, source)) is None


def test_mark_and_clear_use_single_entry_persistence(tmp_path):
    store = _make_store(tmp_path)
    entry = store.get_or_create_session(_make_source())
    real_save_entry = store._save_entry
    store._save_entry = MagicMock(wraps=real_save_entry)

    token = store.mark_turn_active(entry.session_key, _make_source())
    assert token is not None
    store._save_entry.assert_called_once_with(
        entry.session_key,
        entry_data=store._entries[entry.session_key].to_dict(),
        lock_held=True,
    )

    store._save_entry.reset_mock()
    assert store.clear_turn_active(entry.session_key, token) is True
    store._save_entry.assert_called_once_with(
        entry.session_key,
        entry_data=store._entries[entry.session_key].to_dict(),
        lock_held=True,
    )


def test_failed_mark_persistence_does_not_leak_marker_into_later_save(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)
    real_save_entry = store._save_entry
    store._save_entry = MagicMock(side_effect=OSError("disk unavailable"))

    with pytest.raises(OSError, match="disk unavailable"):
        store.mark_turn_active(entry.session_key, source)

    current = _entry_for(store, source)
    assert current.active_turn_token is None
    assert current.active_turn_started_at is None

    # A later unrelated save must not make the failed marker durable.
    store._save_entry = real_save_entry
    with store._lock:
        store._entries[entry.session_key].updated_at = datetime.now()
        store._save()

    reloaded = _make_store(tmp_path)
    assert reloaded.recover_interrupted_turns() == 0
    assert _entry_for(reloaded, source).active_turn_token is None


def test_failed_clear_persistence_keeps_token_retryable_and_durable_clear_wins(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)
    token = store.mark_turn_active(entry.session_key, source)
    assert token is not None

    real_save_entry = store._save_entry
    store._save_entry = MagicMock(side_effect=OSError("disk unavailable"))

    with pytest.raises(OSError, match="disk unavailable"):
        store.clear_turn_active(entry.session_key, token)

    current = _entry_for(store, source)
    assert current.active_turn_token == token
    assert current.active_turn_started_at is not None

    store._save_entry = real_save_entry
    assert store.clear_turn_active(entry.session_key, token) is True

    reloaded = _make_store(tmp_path)
    persisted = _entry_for(reloaded, source)
    assert persisted.active_turn_token is None
    assert persisted.active_turn_started_at is None


def test_state_db_failure_atomic_marker_round_trip(tmp_path):
    store = _make_db_store(tmp_path)
    source = _make_source("state-db-active-turn")
    entry = store.get_or_create_session(source)
    real_save_entry = store._save_entry

    store._save_entry = MagicMock(side_effect=OSError("state.db unavailable"))
    with pytest.raises(OSError, match="state.db unavailable"):
        store.mark_turn_active(entry.session_key, source)
    assert _entry_for(store, source).active_turn_token is None

    store._save_entry = real_save_entry
    token = store.mark_turn_active(entry.session_key, source)
    assert token is not None

    store._save_entry = MagicMock(side_effect=OSError("state.db unavailable"))
    with pytest.raises(OSError, match="state.db unavailable"):
        store.clear_turn_active(entry.session_key, token)
    assert _entry_for(store, source).active_turn_token == token

    store._save_entry = real_save_entry
    assert store.clear_turn_active(entry.session_key, token) is True
    _close_store_db(store)

    reloaded = _make_db_store(tmp_path)
    assert reloaded.recover_interrupted_turns() == 0
    persisted = _entry_for(reloaded, source)
    assert persisted.active_turn_token is None
    assert persisted.active_turn_started_at is None
    _close_store_db(reloaded)


def test_state_db_commit_survives_legacy_mirror_failure(tmp_path):
    store = _make_db_store(tmp_path)
    source = _make_source("state-db-mirror-failure")
    entry = store.get_or_create_session(source)
    db = store._db
    assert db is not None
    db.save_gateway_routing_entry = MagicMock(
        side_effect=OSError("fast upsert unavailable")
    )
    store._save_sessions_json = MagicMock(
        side_effect=OSError("legacy mirror unavailable")
    )

    token = store.mark_turn_active(entry.session_key, source)
    assert token is not None
    _close_store_db(store)

    reloaded = _make_db_store(tmp_path)
    recovered = _entry_for(reloaded, source)
    assert recovered.active_turn_token == token
    _close_store_db(reloaded)


def test_exact_old_active_turn_recovers_even_when_updated_at_is_stale(tmp_path):
    store = _make_db_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)
    token = store.mark_turn_active(entry.session_key, source)

    with store._lock:
        current = store._entries[entry.session_key]
        current.updated_at = datetime.now() - timedelta(hours=2)
        current.active_turn_started_at = datetime.now() - timedelta(minutes=10)
        store._save()
    _close_store_db(store)

    # Prove the marker survives a fresh SessionStore and is not relying on the
    # in-memory object that wrote it.
    reloaded = _make_db_store(tmp_path)
    assert reloaded.recover_interrupted_turns(
        max_age_seconds=ACTIVE_TURN_MAX_AGE_SECONDS
    ) == 1

    recovered = _entry_for(reloaded, source)
    assert recovered.resume_pending is True
    assert recovered.resume_reason == "restart_interrupted"
    assert recovered.last_resume_marked_at is not None
    assert recovered.last_resume_marked_at > datetime.now() - timedelta(seconds=5)
    assert recovered.active_turn_token is None
    assert recovered.active_turn_started_at is None
    assert recovered.active_turn_origin_snapshot is None
    assert resume_origin_from_snapshot(recovered) == source
    obligation = reloaded._db.get_gateway_resume_obligation(recovered.session_key)
    assert obligation is not None
    assert obligation["state"] == "PENDING"
    assert obligation["resume_task_id"] == token == recovered.resume_task_id
    assert obligation["generation"] == recovered.continuation_generation


def test_suspended_active_turn_is_cleared_without_resume(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)
    store.mark_turn_active(entry.session_key, source)

    with store._lock:
        store._entries[entry.session_key].suspended = True

    assert store.recover_interrupted_turns() == 0
    recovered = _entry_for(store, source)
    assert recovered.suspended is True
    assert recovered.resume_pending is False
    assert recovered.active_turn_token is None
    assert recovered.active_turn_started_at is None


def test_unsealed_json_resume_hint_is_replaced_by_exact_active_turn(tmp_path):
    store = _make_db_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)
    store.mark_turn_active(entry.session_key, source)
    original_mark = datetime.now() - timedelta(minutes=2)

    with store._lock:
        current = store._entries[entry.session_key]
        current.resume_pending = True
        current.resume_reason = "shutdown_timeout"
        current.last_resume_marked_at = original_mark
        store._save()

    claim = store._db.claim_gateway_resume_obligation
    with patch.object(
        store._db, "claim_gateway_resume_obligation", wraps=claim
    ) as claim_spy:
        assert store.recover_interrupted_turns() == 0
    claim_spy.assert_not_called()
    recovered = _entry_for(store, source)
    assert recovered.resume_pending is True
    assert recovered.resume_reason == "shutdown_timeout"
    assert recovered.last_resume_marked_at > original_mark
    assert recovered.active_turn_token is None
    assert recovered.active_turn_started_at is None
    assert resume_origin_from_snapshot(recovered) == source
    obligation = store._db.get_gateway_resume_obligation(entry.session_key)
    assert obligation is not None
    assert obligation["state"] == "PENDING"
    assert obligation["resume_task_id"] == recovered.resume_task_id
    assert obligation["generation"] == recovered.continuation_generation


def test_ancient_active_marker_is_cleared_without_auto_resume(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)
    store.mark_turn_active(entry.session_key, source)

    with store._lock:
        current = store._entries[entry.session_key]
        current.active_turn_started_at = datetime.now() - timedelta(hours=2)

    assert store.recover_interrupted_turns(
        max_age_seconds=ACTIVE_TURN_MAX_AGE_SECONDS
    ) == 0
    recovered = _entry_for(store, source)
    assert recovered.resume_pending is False
    assert recovered.active_turn_token is None
    assert recovered.active_turn_started_at is None


def test_clean_startup_discards_orphan_markers_without_resuming(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source()
    entry = store.get_or_create_session(source)
    store.mark_turn_active(entry.session_key, source)

    assert store.discard_active_turn_markers() == 1

    recovered = _entry_for(store, source)
    assert recovered.resume_pending is False
    assert recovered.active_turn_token is None
    assert recovered.active_turn_started_at is None


@pytest.mark.asyncio
async def test_clean_shutdown_marker_is_not_consumed_when_discard_fails(tmp_path):
    marker = tmp_path / ".clean_shutdown"
    marker.write_text("clean", encoding="utf-8")
    runner = object.__new__(GatewayRunner)
    async_store = MagicMock()
    async_store.discard_active_turn_markers = AsyncMock(
        side_effect=OSError("state store unavailable")
    )

    with patch.object(
        GatewayRunner,
        "async_session_store",
        new_callable=PropertyMock,
        return_value=async_store,
    ):
        with pytest.raises(OSError, match="state store unavailable"):
            await runner._consume_clean_shutdown_marker(marker)

    assert marker.exists()


@pytest.mark.asyncio
async def test_clean_shutdown_marker_is_unlinked_after_durable_discard(tmp_path):
    marker = tmp_path / ".clean_shutdown"
    marker.write_text("clean", encoding="utf-8")
    runner = object.__new__(GatewayRunner)
    async_store = MagicMock()
    async_store.discard_active_turn_markers = AsyncMock(return_value=2)

    with patch.object(
        GatewayRunner,
        "async_session_store",
        new_callable=PropertyMock,
        return_value=async_store,
    ):
        discarded = await runner._consume_clean_shutdown_marker(marker)

    assert discarded == 2
    assert not marker.exists()


@pytest.mark.asyncio
async def test_runner_active_turn_carrier_clears_the_exact_resolved_key():
    runner = object.__new__(GatewayRunner)
    runner.session_store = MagicMock()
    mark_active = AsyncMock(return_value="token-1")
    clear_active = AsyncMock(return_value=True)
    setattr(
        runner,
        "_async_session_store",
        SimpleNamespace(
            _store=runner.session_store,
            mark_turn_active=mark_active,
            clear_turn_active=clear_active,
        ),
    )
    event = SimpleNamespace(source=_make_source())

    await runner._mark_durable_active_turn(
        cast(Any, event), "resolved-session-key"
    )

    assert event._gateway_active_turn_session_key == "resolved-session-key"
    assert event._gateway_active_turn_token == "token-1"
    mark_active.assert_awaited_once_with(
        "resolved-session-key", event.source
    )

    await runner._clear_durable_active_turn(cast(Any, event))

    clear_active.assert_awaited_once_with("resolved-session-key", "token-1")
    assert not hasattr(event, "_gateway_active_turn_session_key")
    assert not hasattr(event, "_gateway_active_turn_token")


@pytest.mark.asyncio
async def test_runner_active_turn_rejects_conflicting_platform_message_ids():
    runner = object.__new__(GatewayRunner)
    runner.session_store = MagicMock()
    mark_active = AsyncMock(return_value="token-1")
    setattr(
        runner,
        "_async_session_store",
        SimpleNamespace(
            _store=runner.session_store,
            mark_turn_active=mark_active,
        ),
    )
    source = _make_source()
    source.message_id = "source-message"
    event = SimpleNamespace(source=source, message_id="event-message")

    assert (
        await runner._mark_durable_active_turn(
            cast(Any, event), "resolved-session-key"
        )
        is False
    )

    mark_active.assert_not_awaited()
    assert not hasattr(event, "_gateway_active_turn_session_key")
    assert not hasattr(event, "_gateway_active_turn_token")


@pytest.mark.asyncio
async def test_runner_active_turn_clear_is_best_effort():
    runner = object.__new__(GatewayRunner)
    runner.session_store = MagicMock()
    clear_active = AsyncMock(
        side_effect=[OSError("disk unavailable"), True]
    )
    setattr(
        runner,
        "_async_session_store",
        SimpleNamespace(
            _store=runner.session_store,
            clear_turn_active=clear_active,
        ),
    )
    event = SimpleNamespace(
        _gateway_active_turn_session_key="resolved-session-key",
        _gateway_active_turn_token="token-1",
    )

    await runner._clear_durable_active_turn(cast(Any, event))

    assert clear_active.await_count == 2
    assert not hasattr(event, "_gateway_active_turn_session_key")
    assert not hasattr(event, "_gateway_active_turn_token")


@pytest.mark.asyncio
async def test_runner_active_turn_clear_stops_after_bounded_retries():
    runner = object.__new__(GatewayRunner)
    runner.session_store = MagicMock()
    clear_active = AsyncMock(side_effect=OSError("disk unavailable"))
    setattr(
        runner,
        "_async_session_store",
        SimpleNamespace(
            _store=runner.session_store,
            clear_turn_active=clear_active,
        ),
    )
    event = SimpleNamespace(
        _gateway_active_turn_session_key="resolved-session-key",
        _gateway_active_turn_token="token-1",
    )

    assert await runner._clear_durable_active_turn(cast(Any, event)) is False

    assert clear_active.await_count == 3
    assert not hasattr(event, "_gateway_active_turn_session_key")
    assert not hasattr(event, "_gateway_active_turn_token")


@pytest.mark.asyncio
async def test_unclean_recovery_promotes_exact_markers_before_legacy_fallback(
    monkeypatch,
):
    runner = object.__new__(GatewayRunner)
    calls: list[str] = []

    monkeypatch.delenv("HERMES_AGENT_TIMEOUT", raising=False)

    async def _recover(*, max_age_seconds):
        assert max_age_seconds == ACTIVE_TURN_MAX_AGE_SECONDS
        calls.append("exact")
        return 1

    async def _fallback(*, max_age_seconds):
        assert max_age_seconds == 120
        calls.append("fallback")
        return 2

    runner.session_store = MagicMock()
    setattr(
        runner,
        "_async_session_store",
        SimpleNamespace(
            _store=runner.session_store,
            recover_interrupted_turns=_recover,
            suspend_recently_active=_fallback,
        ),
    )

    assert await runner._recover_unclean_sessions() == (1, 2)
    assert calls == ["exact", "fallback"]
