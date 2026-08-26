"""Tests for the resume_pending session continuity path.

Covers the behaviour introduced to fix the ``Gateway shutting down ...
task will be interrupted`` follow-up bug (spec: PR #11852, builds on
PRs #9850, #9934, #7536):

1. When a gateway restart drain times out and agents are force-interrupted,
   the affected sessions are flagged ``resume_pending=True`` — not
   ``suspended`` — so the next user message on the same session_key
   auto-resumes from the existing transcript instead of getting routed
   through ``suspend_recently_active()`` and converted into a fresh
   session.

2. ``suspended=True`` (from ``/stop`` or stuck-loop escalation) still
   wins over ``resume_pending`` — the forced-wipe path is preserved.

3. The restart-resume system note injected into the next user message is
   a superset of the existing tool-tail auto-continue note (from
   PR #9934), using session-entry metadata rather than just transcript
   shape so it fires even when the interrupted transcript does NOT end
   with a ``tool`` role.

4. The existing ``.restart_failure_counts`` stuck-loop counter from
   PR #7536 remains the single source of escalation — no parallel
   counter is added on ``SessionEntry``.
"""

import asyncio
import json
import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, HomeChannel, Platform
from gateway.platforms.base import MessageEvent, MessageType, SendResult
from gateway.run import (
    _AGENT_PENDING_SENTINEL,
    _auto_continue_freshness_window,
    _coerce_gateway_timestamp,
    _is_fresh_gateway_interruption,
    _last_transcript_timestamp,
    _prepare_resume_pending_message,
    _should_follow_telegram_topic_binding,
    _should_clear_resume_pending_after_turn,
    build_resume_recovery_note,
)
from gateway.session import SessionEntry, SessionSource, SessionStore
from tests.gateway.restart_test_helpers import (
    bind_restart_origin_snapshot,
    make_restart_runner,
    make_restart_source,
)
from tests.gateway.test_gateway_silence_tokens import _runner as make_agent_runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_resume_pending_is_cleared_only_after_successful_turn():
    """Interrupted/failed drain results must keep the restart recovery marker.

    Regression for dogfood failure: during gateway restart the interrupted run
    returned an empty final response and was normalized into a user-facing
    fallback, but the gateway cleared ``resume_pending`` before startup could
    auto-resume it.
    """
    assert _should_clear_resume_pending_after_turn({"final_response": "done"}) is True
    assert _should_clear_resume_pending_after_turn({"completed": True}) is True
    assert _should_clear_resume_pending_after_turn({"interrupted": True}) is False
    assert _should_clear_resume_pending_after_turn({"completed": False}) is False
    assert _should_clear_resume_pending_after_turn({"failed": True}) is False
    assert _should_clear_resume_pending_after_turn({"partial": True}) is False
    assert _should_clear_resume_pending_after_turn({"error": "boom"}) is False


def test_startup_resume_keeps_exact_session_instead_of_stale_topic_binding():
    startup = MagicMock(startup_resume=True)
    ordinary = MagicMock(startup_resume=False)

    assert _should_follow_telegram_topic_binding(startup) is False
    assert _should_follow_telegram_topic_binding(ordinary) is True


def test_startup_resume_event_pins_exact_interrupted_session():
    """A synthetic resume must not re-resolve to another session in the lane."""
    runner, _adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="-1003971448755",
        thread_id="1751",
        message_id="48266",
    )
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key="agent:hermesdev:telegram:group:-1003971448755:1751",
            session_id="20260826_143448_0768b70d",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="group",
            resume_pending=True,
            resume_reason="shutdown_timeout",
            last_resume_marked_at=datetime.now(),
            resume_task_id="task-resume-1751",
        )
    )

    event = runner._build_startup_resume_event(entry, source)

    assert event.metadata["gateway_session_strict"] is True
    assert event.metadata["gateway_session_key"] == entry.session_key
    assert event.metadata["gateway_session_id"] == entry.session_id


def _make_source(platform=Platform.TELEGRAM, chat_id="123", user_id="u1"):
    return SessionSource(platform=platform, chat_id=chat_id, user_id=user_id)


def _make_store(tmp_path):
    return SessionStore(sessions_dir=tmp_path, config=GatewayConfig())


def _build_agent_history(history: list) -> list:
    """Mirror gateway/run.py's ``history → agent_history`` conversion.

    This is the transformation that strips ``timestamp`` off tool/tool_call
    rows before the agent sees them.  Tests that check the freshness gate
    must go through this conversion so they exercise the *real* data the
    note-injection code sees.
    """
    agent_history: list = []
    for msg in history:
        role = msg.get("role")
        if not role or role in {"session_meta", "system"}:
            continue
        has_tool_calls = "tool_calls" in msg
        has_tool_call_id = "tool_call_id" in msg
        is_tool_message = role == "tool"
        if has_tool_calls or has_tool_call_id or is_tool_message:
            agent_history.append({k: v for k, v in msg.items() if k != "timestamp"})
        else:
            content = msg.get("content")
            if content:
                agent_history.append({"role": role, "content": content})
    return agent_history


def _simulate_note_injection(
    history: list,
    user_message: str,
    resume_entry: SessionEntry | None,
    *,
    agent_history: list | None = None,
    window_secs: float | None = None,
) -> str:
    """Mirror the note-injection logic in gateway/run.py _run_agent().

    The freshness signal reads ``history[-1].timestamp`` (the raw transcript
    row), NOT ``agent_history[-1].timestamp`` (which has been stripped).
    Tests pass the raw ``history`` — ``agent_history`` is derived from it
    via the real conversion if not supplied explicitly.
    """
    if agent_history is None:
        agent_history = _build_agent_history(history)

    window = (
        float(window_secs)
        if window_secs is not None
        else _auto_continue_freshness_window()
    )
    interruption_is_fresh = _is_fresh_gateway_interruption(
        _last_transcript_timestamp(history),
        window_secs=window,
    )

    message = user_message
    resume_mark_is_fresh = False
    if resume_entry is not None and getattr(resume_entry, "resume_pending", False):
        resume_mark_is_fresh = _is_fresh_gateway_interruption(
            getattr(resume_entry, "last_resume_marked_at", None),
            window_secs=window,
        )
    is_resume_pending = bool(
        resume_entry is not None
        and getattr(resume_entry, "resume_pending", False)
        and (interruption_is_fresh or resume_mark_is_fresh)
    )
    has_fresh_tool_tail = bool(
        agent_history
        and agent_history[-1].get("role") == "tool"
        and interruption_is_fresh
    )

    if is_resume_pending:
        reason = getattr(resume_entry, "resume_reason", None) or "restart_timeout"
        # Real production note builder — extracted to module scope in
        # gateway/run.py so tests exercise the actual strings.
        message = build_resume_recovery_note(reason, message)
    elif has_fresh_tool_tail:
        message = (
            "[System note: A new message has arrived. The conversation "
            "history contains pending tool outputs from an interrupted turn. "
            "IGNORE those pending results. Address the user's NEW message "
            "below FIRST. Do NOT re-execute old tool calls from the history.]\n\n"
            + message
        )

    # Empty-turn safety net: mirrors gateway/run.py — a blank
    # auto-resume turn on a resume_pending session must never reach the model.
    if (
        isinstance(message, str)
        and not message.strip()
        and resume_entry is not None
        and getattr(resume_entry, "resume_pending", False)
    ):
        sn_reason = getattr(resume_entry, "resume_reason", None) or "restart_timeout"
        message = build_resume_recovery_note(sn_reason, "")
    return message


# ---------------------------------------------------------------------------
# SessionEntry field + serialization
# ---------------------------------------------------------------------------


class TestSessionEntryResumeFields:
    def test_defaults(self):
        now = datetime.now()
        entry = SessionEntry(
            session_key="agent:main:telegram:dm:1",
            session_id="sid",
            created_at=now,
            updated_at=now,
        )
        assert entry.resume_pending is False
        assert entry.resume_reason is None
        assert entry.last_resume_marked_at is None


# ---------------------------------------------------------------------------
# SessionStore.mark_resume_pending / clear_resume_pending
# ---------------------------------------------------------------------------


class TestMarkResumePending:
    def test_marks_existing_session(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        assert store.mark_turn_active(entry.session_key, source)

        assert store.mark_resume_pending(entry.session_key) is True
        refreshed = store._entries[entry.session_key]
        assert refreshed.resume_pending is True
        assert refreshed.resume_reason == "restart_timeout"
        assert refreshed.last_resume_marked_at is not None

    def test_custom_reason_persists(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        assert store.mark_turn_active(entry.session_key, source)

        store.mark_resume_pending(entry.session_key, reason="shutdown_timeout")
        assert store._entries[entry.session_key].resume_reason == "shutdown_timeout"


class TestClearResumePending:

    def test_returns_false_when_not_pending(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        # Not marked
        assert store.clear_resume_pending(entry.session_key) is False


def test_resume_projection_mirrors_only_committed_db_obligation(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source(chat_id="db-obligation", user_id="db-obligation")
    entry = store.get_or_create_session(source)
    task_id = store.mark_turn_active(entry.session_key, source)
    assert task_id

    assert store.mark_resume_pending(entry.session_key) is True
    row = store._db.get_gateway_resume_obligation(entry.session_key)
    assert row is not None
    assert row["state"] == "PENDING"
    assert row["resume_task_id"] == task_id == entry.resume_task_id
    assert row["generation"] == entry.continuation_generation

    assert store._db.claim_gateway_resume_obligation(
        session_key=entry.session_key,
        resume_task_id=entry.resume_task_id,
        expected_generation=entry.continuation_generation,
        claim_owner=entry.continuation_claim_owner,
        claim_token=entry.continuation_claim_token,
    )
    identity = {
        "resume_task_id": entry.resume_task_id,
        "continuation_generation": entry.continuation_generation,
        "continuation_claim_owner": entry.continuation_claim_owner,
        "continuation_claim_token": entry.continuation_claim_token,
    }
    assert store.clear_resume_pending_exact(entry.session_key, **identity) is True
    assert store._db.get_gateway_resume_obligation(entry.session_key)["state"] == "TERMINAL"
    assert entry.resume_pending is False


def test_duplicate_premark_projects_authoritative_pending_or_claimed_row(
    tmp_path,
    monkeypatch,
):
    store = _make_store(tmp_path)
    source = _make_source(chat_id="duplicate-premark", user_id="owner")
    entry = store.get_or_create_session(source)
    store.mark_turn_active(entry.session_key, source)
    first = store.mark_resume_pending_with_receipt(
        entry.session_key,
        "restart_timeout",
    )
    assert first
    first_row = store._db.get_gateway_resume_obligation(entry.session_key)
    first_marked_at = entry.last_resume_marked_at
    monkeypatch.setattr(
        "gateway.session._now",
        lambda: first_marked_at + timedelta(minutes=10),
    )

    pending_repeat = store.mark_resume_pending_with_receipt(
        entry.session_key,
        "restart_timeout",
    )
    assert pending_repeat == first
    assert entry.last_resume_marked_at == first_marked_at
    assert store._db.get_gateway_resume_obligation(entry.session_key) == first_row

    assert store._db.claim_gateway_resume_obligation(
        session_key=entry.session_key,
        resume_task_id=first["resume_task_id"],
        expected_generation=first["continuation_generation"],
        claim_owner=first["continuation_claim_owner"],
        claim_token=first["continuation_claim_token"],
    )
    claimed_repeat = store.mark_resume_pending_with_receipt(
        entry.session_key,
        "restart_timeout",
    )
    assert claimed_repeat == first
    assert entry.last_resume_marked_at == first_marked_at


def test_startup_reconciles_orphaned_claim_before_next_restart_generation(tmp_path):
    """A reset/new task must not inherit a claimed obligation from an old boot."""
    store = _make_store(tmp_path)
    source = _make_source(chat_id="incident-1751", user_id=None)
    entry = store.get_or_create_session(source)
    first_task = store.mark_turn_active(entry.session_key, source)
    assert first_task
    first_receipt = store.mark_resume_pending_with_receipt(
        entry.session_key,
        "shutdown_timeout",
    )
    assert first_receipt
    assert store._db.claim_gateway_resume_obligation(
        session_key=entry.session_key,
        resume_task_id=first_receipt["resume_task_id"],
        expected_generation=first_receipt["continuation_generation"],
        claim_owner=first_receipt["continuation_claim_owner"],
        claim_token=first_receipt["continuation_claim_token"],
    )

    # Mirrors /new or a completed newer human task: routing no longer carries
    # the old continuation identity, while the DB claim survived the boot.
    entry.resume_pending = False
    entry.resume_reason = None
    entry.last_resume_marked_at = None
    entry.resume_task_id = ""
    # Production startup can reconstruct the routing projection without the
    # terminal DB generation.  The DB row remains authoritative and the next
    # independently authorized active turn must advance from it, not deadlock
    # forever on the projection's stale generation zero.
    entry.continuation_generation = 0
    entry.continuation_claim_owner = ""
    entry.continuation_claim_token = ""
    entry.resume_origin_snapshot = None
    store._save()

    reconcile = getattr(store, "reconcile_orphaned_resume_obligations", None)
    assert callable(reconcile), "startup needs a canonical orphan-claim reconciler"
    receipt = reconcile(max_age_seconds=3600)
    assert receipt == {
        "cancelled_pending": 0,
        "abandoned_claimed": 1,
        "cleared_terminal_projection": 0,
        "kept": 0,
    }
    old_row = store._db.get_gateway_resume_obligation(entry.session_key)
    assert old_row["state"] == "CANCELLED"
    assert old_row["reason"] == "orphaned_claim"

    second_task = store.mark_turn_active(entry.session_key, source)
    assert second_task and second_task != first_task
    second_receipt = store.mark_resume_pending_with_receipt(
        entry.session_key,
        "shutdown_timeout",
    )
    assert second_receipt
    assert second_receipt["continuation_generation"] == 2
    assert store._db.get_gateway_resume_obligation(entry.session_key)["state"] == "PENDING"

    reopened = _make_store(tmp_path)
    assert reopened.reconcile_orphaned_resume_obligations(max_age_seconds=3600) == {
        "cancelled_pending": 0,
        "abandoned_claimed": 0,
        "cleared_terminal_projection": 0,
        "kept": 1,
    }
    assert reopened._db.get_gateway_resume_obligation(entry.session_key)["state"] == "PENDING"


def test_startup_reconciles_stale_exact_claim_and_clears_only_its_marker(
    tmp_path,
    monkeypatch,
):
    store = _make_store(tmp_path)
    source = _make_source(chat_id="incident-26452", user_id="owner")
    entry = store.get_or_create_session(source)
    store.mark_turn_active(entry.session_key, source)
    receipt = store.mark_resume_pending_with_receipt(
        entry.session_key,
        "shutdown_timeout",
    )
    assert receipt
    assert store._db.claim_gateway_resume_obligation(
        session_key=entry.session_key,
        resume_task_id=receipt["resume_task_id"],
        expected_generation=receipt["continuation_generation"],
        claim_owner=receipt["continuation_claim_owner"],
        claim_token=receipt["continuation_claim_token"],
    )
    monkeypatch.setattr(
        "gateway.session._now",
        lambda: entry.last_resume_marked_at + timedelta(hours=2),
    )

    original_save_entry = store._save_entry
    persisted_keys = []

    def _assert_locked_save_entry(session_key, *, entry_data=None, lock_held=False):
        acquired = store._lock.acquire(blocking=False)
        if acquired:
            store._lock.release()
        assert not acquired
        assert lock_held is True
        persisted_keys.append(session_key)
        original_save_entry(
            session_key,
            entry_data=entry_data,
            lock_held=lock_held,
        )

    store._save_entry = _assert_locked_save_entry
    result = store.reconcile_orphaned_resume_obligations(max_age_seconds=3600)

    assert result == {
        "cancelled_pending": 0,
        "abandoned_claimed": 1,
        "cleared_terminal_projection": 0,
        "kept": 0,
    }
    row = store._db.get_gateway_resume_obligation(entry.session_key)
    assert row["state"] == "CANCELLED"
    assert row["reason"] == "stale_claim"
    assert entry.resume_pending is False
    assert persisted_keys == [entry.session_key]


def test_startup_reconciliation_does_not_publish_unrelated_fallback_routes(
    tmp_path,
    monkeypatch,
):
    """Settling one claim must not turn an in-memory fallback into a DB row."""
    store = _make_store(tmp_path)
    source = _make_source(chat_id="incident-exact-save", user_id="owner")
    entry = store.get_or_create_session(source)
    store.mark_turn_active(entry.session_key, source)
    receipt = store.mark_resume_pending_with_receipt(
        entry.session_key,
        "shutdown_timeout",
    )
    assert receipt
    assert store._db.claim_gateway_resume_obligation(
        session_key=entry.session_key,
        resume_task_id=receipt["resume_task_id"],
        expected_generation=receipt["continuation_generation"],
        claim_owner=receipt["continuation_claim_owner"],
        claim_token=receipt["continuation_claim_token"],
    )
    monkeypatch.setattr(
        "gateway.session._now",
        lambda: entry.last_resume_marked_at + timedelta(hours=2),
    )

    unrelated_key = entry.session_key + ":fallback-only"
    unrelated = replace(
        entry,
        session_key=unrelated_key,
        session_id=entry.session_id + "-fallback-only",
        resume_pending=False,
    )
    with store._lock:
        store._entries[unrelated_key] = unrelated
    before = store._db.load_gateway_routing_entries(scope=store._routing_scope())
    assert unrelated_key not in before

    result = store.reconcile_orphaned_resume_obligations(max_age_seconds=3600)

    assert result["abandoned_claimed"] == 1
    after = store._db.load_gateway_routing_entries(scope=store._routing_scope())
    assert unrelated_key not in after


@pytest.mark.parametrize("terminal_state", ["TERMINAL", "CANCELLED"])
def test_startup_clears_exact_routing_projection_after_db_settlement_crash(
    tmp_path,
    terminal_state,
):
    """A crash between DB CAS and routing save must heal on the next boot."""
    store = _make_store(tmp_path)
    source = _make_source(chat_id=f"crash-window-{terminal_state}", user_id="owner")
    entry = store.get_or_create_session(source)
    store.mark_turn_active(entry.session_key, source)
    receipt = store.mark_resume_pending_with_receipt(
        entry.session_key,
        "shutdown_timeout",
    )
    assert receipt
    assert store._db.claim_gateway_resume_obligation(
        session_key=entry.session_key,
        resume_task_id=receipt["resume_task_id"],
        expected_generation=receipt["continuation_generation"],
        claim_owner=receipt["continuation_claim_owner"],
        claim_token=receipt["continuation_claim_token"],
    )
    if terminal_state == "TERMINAL":
        assert store._db.clear_gateway_resume_obligation(
            session_key=entry.session_key,
            resume_task_id=receipt["resume_task_id"],
            expected_generation=receipt["continuation_generation"],
            claim_token=receipt["continuation_claim_token"],
        )
    else:
        assert store._db.abandon_gateway_resume_obligation(
            session_key=entry.session_key,
            resume_task_id=receipt["resume_task_id"],
            expected_generation=receipt["continuation_generation"],
            claim_owner=receipt["continuation_claim_owner"],
            claim_token=receipt["continuation_claim_token"],
            reason="quarantined_unsafe_unknown",
        )
    assert entry.resume_pending is True

    restarted = _make_store(tmp_path)
    healed = restarted.reconcile_orphaned_resume_obligations(
        max_age_seconds=3600
    )

    assert healed == {
        "cancelled_pending": 0,
        "abandoned_claimed": 0,
        "cleared_terminal_projection": 1,
        "kept": 0,
    }
    healed_entry = restarted._entries[entry.session_key]
    assert healed_entry.resume_pending is False


def test_startup_quarantines_claim_with_corrupt_route_envelope(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source(chat_id="route-origin", user_id="owner")
    entry = store.get_or_create_session(source)
    store.mark_turn_active(entry.session_key, source)
    receipt = store.mark_resume_pending_with_receipt(
        entry.session_key,
        "shutdown_timeout",
    )
    assert receipt
    assert store._db.claim_gateway_resume_obligation(
        session_key=entry.session_key,
        resume_task_id=receipt["resume_task_id"],
        expected_generation=receipt["continuation_generation"],
        claim_owner=receipt["continuation_claim_owner"],
        claim_token=receipt["continuation_claim_token"],
    )
    entry.resume_origin_snapshot["source"]["chat_id"] = "wrong-recipient"
    store._save()

    result = store.reconcile_orphaned_resume_obligations(max_age_seconds=3600)

    assert result == {
        "cancelled_pending": 0,
        "abandoned_claimed": 1,
        "cleared_terminal_projection": 0,
        "kept": 0,
    }
    row = store._db.get_gateway_resume_obligation(entry.session_key)
    assert row["state"] == "CANCELLED"
    assert row["reason"] == "quarantined_invalid_envelope"
    assert entry.resume_pending is False


def test_startup_quarantines_fresh_projection_with_mismatched_marked_at(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source(chat_id="timestamp-mismatch", user_id="owner")
    entry = store.get_or_create_session(source)
    store.mark_turn_active(entry.session_key, source)
    receipt = store.mark_resume_pending_with_receipt(
        entry.session_key,
        "shutdown_timeout",
    )
    assert receipt
    assert store._db.claim_gateway_resume_obligation(
        session_key=entry.session_key,
        resume_task_id=receipt["resume_task_id"],
        expected_generation=receipt["continuation_generation"],
        claim_owner=receipt["continuation_claim_owner"],
        claim_token=receipt["continuation_claim_token"],
    )
    entry.last_resume_marked_at = entry.last_resume_marked_at + timedelta(
        seconds=30
    )
    store._save()

    result = store.reconcile_orphaned_resume_obligations(max_age_seconds=3600)

    assert result["abandoned_claimed"] == 1
    row = store._db.get_gateway_resume_obligation(entry.session_key)
    assert row["state"] == "CANCELLED"
    assert row["reason"] == "quarantined_invalid_envelope"
    assert entry.resume_pending is False


def test_reconciliation_isolates_topics_and_profiles(tmp_path):
    def _assert_one_lane_does_not_mutate_the_other(store, sources):
        entries = []
        for source in sources:
            entry = store.get_or_create_session(source)
            store.mark_turn_active(entry.session_key, source)
            receipt = store.mark_resume_pending_with_receipt(
                entry.session_key,
                "shutdown_timeout",
            )
            assert receipt
            assert store._db.claim_gateway_resume_obligation(
                session_key=entry.session_key,
                resume_task_id=receipt["resume_task_id"],
                expected_generation=receipt["continuation_generation"],
                claim_owner=receipt["continuation_claim_owner"],
                claim_token=receipt["continuation_claim_token"],
            )
            entries.append(entry)
        assert entries[0].session_key != entries[1].session_key
        entries[0].resume_origin_snapshot["source"]["chat_id"] = "wrong-route"
        store._save()

        result = store.reconcile_orphaned_resume_obligations(
            max_age_seconds=3600
        )

        assert result["abandoned_claimed"] == 1
        assert result["kept"] == 1
        first_row = store._db.get_gateway_resume_obligation(
            entries[0].session_key
        )
        second_row = store._db.get_gateway_resume_obligation(
            entries[1].session_key
        )
        assert first_row["state"] == "CANCELLED"
        assert entries[0].resume_pending is False
        assert second_row["state"] == "CLAIMED"
        assert entries[1].resume_pending is True
        return entries

    topic_store = _make_store(tmp_path / "topics")
    topic_entries = _assert_one_lane_does_not_mutate_the_other(
        topic_store,
        [
            SessionSource(
                platform=Platform.TELEGRAM,
                chat_id="-1003971448755",
                chat_type="group",
                user_id="owner",
                thread_id=thread_id,
            )
            for thread_id in ("1751", "26452")
        ],
    )

    profile_store = SessionStore(
        sessions_dir=tmp_path / "profiles",
        config=GatewayConfig(multiplex_profiles=True),
    )
    _assert_one_lane_does_not_mutate_the_other(
        profile_store,
        [
            SessionSource(
                platform=Platform.TELEGRAM,
                chat_id="same-chat",
                chat_type="group",
                user_id="owner",
                thread_id="same-thread",
                profile=profile,
            )
            for profile in ("hermesdev", "main")
        ],
    )
    assert topic_store._db.get_gateway_resume_obligation(
        topic_entries[1].session_key
    )["state"] == "CLAIMED"


def test_abandon_resume_pending_rejects_empty_claim_token_before_db(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source(chat_id="invalid-token", user_id="owner")
    entry = store.get_or_create_session(source)
    entry.resume_pending = True
    entry.resume_task_id = "task-1"
    entry.continuation_generation = 1
    entry.continuation_claim_owner = "gateway:1"
    entry.continuation_claim_token = ""
    abandon = MagicMock(return_value=True)
    store._db.abandon_gateway_resume_obligation = abandon

    assert not store.abandon_resume_pending_exact(
        entry.session_key,
        resume_task_id="task-1",
        continuation_generation=1,
        continuation_claim_owner="gateway:1",
        continuation_claim_token="",
        reason="quarantined_invalid_envelope",
    )
    abandon.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_runs_resume_obligation_reconciler_before_scheduling():
    runner, _adapter = make_restart_runner()
    reconcile = AsyncMock(
        return_value={
            "cancelled_pending": 1,
            "abandoned_claimed": 2,
            "cleared_terminal_projection": 4,
            "kept": 3,
        }
    )
    runner.async_session_store.reconcile_orphaned_resume_obligations = reconcile

    run_reconciler = getattr(runner, "_reconcile_startup_resume_obligations", None)
    assert callable(run_reconciler), "gateway startup needs an obligation reconciler"
    receipt = await run_reconciler()

    reconcile.assert_awaited_once_with(
        max_age_seconds=_auto_continue_freshness_window()
    )
    assert receipt == {
        "cancelled_pending": 1,
        "abandoned_claimed": 2,
        "cleared_terminal_projection": 4,
        "kept": 3,
    }


def test_crash_recovery_admits_before_clearing_active_marker(tmp_path):
    store = _make_store(tmp_path)
    source = _make_source(chat_id="crash-a", user_id="crash-a")
    entry = store.get_or_create_session(source)
    task_id = store.mark_turn_active(entry.session_key, source)
    assert task_id

    admit = store._db.admit_gateway_resume_obligation
    with patch.object(store._db, "admit_gateway_resume_obligation", return_value=None):
        assert store.recover_interrupted_turns() == 0
    assert entry.active_turn_token == task_id
    assert entry.resume_pending is False
    assert store._db.get_gateway_resume_obligation(entry.session_key) is None

    with patch.object(store._db, "admit_gateway_resume_obligation", side_effect=admit):
        assert store.recover_interrupted_turns() == 1
    row = store._db.get_gateway_resume_obligation(entry.session_key)
    assert row is not None
    assert row["state"] == "PENDING"
    assert row["resume_task_id"] == task_id == entry.resume_task_id
    assert row["generation"] == entry.continuation_generation
    assert entry.active_turn_token is None


# ---------------------------------------------------------------------------
# SessionStore.get_or_create_session resume_pending behaviour
# ---------------------------------------------------------------------------


class TestGetOrCreateResumePending:

    def test_resume_pending_follows_compression_tip(self, tmp_path):
        """Interrupted platform mappings must not stay pinned to compressed roots."""
        store = _make_store(tmp_path)
        source = _make_source(
            platform=Platform.WEIXIN,
            chat_id="wx-chat",
            user_id="wx-user",
        )
        first = store.get_or_create_session(source)
        original_sid = first.session_id
        assert store.mark_turn_active(first.session_key, source)
        store.mark_resume_pending(first.session_key)

        with patch.object(
            store, "_compression_tip_for_session_id", return_value="child-session"
        ) as mock_tip:
            second = store.get_or_create_session(source)

        assert second.session_id == "child-session"
        assert second.resume_pending is True
        mock_tip.assert_called_with(original_sid)


# ---------------------------------------------------------------------------
# SessionStore.suspend_recently_active skip behaviour
# ---------------------------------------------------------------------------


class TestSuspendRecentlyActiveSkipsResumePending:
    def test_resume_pending_entries_not_suspended(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        assert store.mark_turn_active(entry.session_key, source)
        store.mark_resume_pending(entry.session_key)

        count = store.suspend_recently_active()
        assert count == 0
        e = store._entries[entry.session_key]
        assert e.suspended is False
        assert e.resume_pending is True


# ---------------------------------------------------------------------------
# Restart-resume system-note injection
# ---------------------------------------------------------------------------


class TestResumePendingSystemNote:
    def _pending_entry(self, reason="restart_timeout") -> SessionEntry:
        now = datetime.now()
        return SessionEntry(
            session_key="agent:main:telegram:dm:1",
            session_id="sid",
            created_at=now,
            updated_at=now,
            resume_pending=True,
            resume_reason=reason,
            last_resume_marked_at=now,
        )


    def test_empty_message_noninteractive_note_continues_task(self):
        """Non-interactive platforms (webhook, API server): nobody can answer
        'what next?', so the resumed turn must complete the interrupted work
        instead of acknowledging (#57056)."""
        note = build_resume_recovery_note("restart_timeout", "", interactive=False)
        assert "CONTINUE the interrupted task" in note
        assert "session was restored" not in note
        assert "ask what they would like to do next" not in note
        # Must not tell the model to skip the unfinished work it should finish.
        assert "skip any unfinished work" not in note
        # But still guards against re-running already-recorded tool calls.
        assert "already appear in the history" in note


    def test_resume_note_is_persisted_instead_of_original_empty_message(self):
        """The auto-resume note must not leave an empty row in state.db."""
        message, persisted = _prepare_resume_pending_message(
            "restart_timeout", "", interactive=False
        )

        assert message
        assert "CONTINUE the interrupted task" in message
        assert persisted == message
        assert persisted != ""

    def test_interactive_startup_resume_continues_instead_of_asking(self):
        message, persisted = _prepare_resume_pending_message(
            "restart_timeout",
            "",
            interactive=True,
            startup_resume=True,
        )

        assert "synthetic startup continuation" in message
        assert "ask what they would like to do next" not in message
        assert "skip any unfinished work" not in message
        assert persisted != message
        assert persisted == "[Internal continuation marker: startup recovery turn.]"
        assert "synthetic startup continuation" not in persisted

    def test_startup_resume_allows_safe_postcondition_check_without_replay(self):
        note = build_resume_recovery_note(
            "restart_timeout", "", interactive=True, startup_resume=True
        )

        assert "do NOT re-execute or verify it" not in note
        assert "do not re-run or re-execute" in note.lower()
        assert "establish its outcome" in note.lower()

    def test_unknown_effect_resume_is_reconciliation_only(self):
        note = build_resume_recovery_note(
            "restart_timeout",
            "",
            interactive=True,
            startup_resume=True,
            reconciliation_only=True,
        )

        assert "RECONCILIATION-ONLY" in note
        assert "Read back their external postconditions" in note
        assert "Never replay, retry, or re-execute an UNKNOWN call" in note
        assert "do not perform a new external effect" in note

    def test_whitespace_only_message_also_persists_the_note(self):
        """A whitespace-only startup event is as blank as an empty one —
        persisting it verbatim would recreate the sanitizer loop (#86580)."""
        message, persisted = _prepare_resume_pending_message(
            "shutdown_timeout", "   ", interactive=True
        )

        assert persisted == message
        assert persisted.strip()

    def test_real_user_text_persists_clean_not_the_scaffolded_note(self):
        """When the user typed real text while resume was pending, the durable
        transcript keeps their clean words; only the MODEL sees the wrapped
        recovery note (transcript stays scaffold-free)."""
        message, persisted = _prepare_resume_pending_message(
            "restart_timeout", "what were we doing?", interactive=True
        )

        assert persisted == "what were we doing?"
        assert "[System note:" not in persisted
        assert message != persisted
        assert "what were we doing?" in message
        assert "[System note:" in message


    def test_resume_pending_fires_without_tool_tail(self):
        """Key improvement over PR #9934: the restart-resume note fires
        even when the transcript's last role is NOT ``tool``."""
        entry = self._pending_entry()
        history = [
            {"role": "user", "content": "run a long thing", "timestamp": time.time() - 10},
            {"role": "assistant", "content": "ok, starting...", "timestamp": time.time()},
        ]
        result = _simulate_note_injection(history, "ping", resume_entry=entry)
        assert "[System note:" in result
        assert "gateway restart" in result
        assert "NEW message" in result


    def test_no_resume_pending_preserves_tool_tail_note(self):
        """Regression: the old PR #9934 tool-tail behaviour is unchanged."""
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ], "timestamp": time.time() - 1},
            {"role": "tool", "tool_call_id": "c1", "content": "result",
             "timestamp": time.time()},
        ]
        result = _simulate_note_injection(history, "ping", resume_entry=None)
        assert "[System note:" in result
        assert "pending tool outputs" in result
        assert "Do NOT re-execute" in result

    def test_stale_resume_pending_does_not_inject_restart_note(self):
        """Old restart markers must not revive an unrelated stale task.

        The transcript's last row is from an hour ago — well outside the
        default 1h freshness window (fixture uses window=1800 to exercise
        the stale path without tying the test to the production default).
        """
        entry = self._pending_entry()
        entry.last_resume_marked_at = datetime.now() - timedelta(hours=1)

        history = [
            {"role": "assistant", "content": "old in progress",
             "timestamp": time.time() - 3600},
        ]
        result = _simulate_note_injection(
            history=history,
            user_message="start a new task",
            resume_entry=entry,
            window_secs=1800,
        )
        assert result == "start a new task"


    def test_stale_tool_tail_does_not_inject_auto_continue_note(self):
        """The core bug fix: stale tool-tail must not revive a dead task.

        Uses window_secs=1800 (30 min) to verify the gate fires at 1h —
        keeps the test stable regardless of the production default.
        """
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ], "timestamp": time.time() - 3601},
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "stale result",
                "timestamp": time.time() - 3600,
            },
        ]
        result = _simulate_note_injection(
            history,
            "start a new task",
            resume_entry=None,
            window_secs=1800,
        )
        assert result == "start a new task"

    def test_stale_tool_tail_with_production_data_shape(self):
        """Regression guard for #16802: exercise the REAL production path
        where ``agent_history`` has been stripped of timestamps.

        The original PR #16802 fix read ``agent_history[-1].get("timestamp")``
        — which is always ``None`` at runtime because the gateway strips
        ``timestamp`` off tool/tool_call rows in ``history → agent_history``.
        This test builds a stale history, runs it through the real
        ``_build_agent_history`` conversion, then asserts:

          1. The stripped ``agent_history`` carries NO timestamp (protects
             against someone "fixing" the original PR by re-adding the
             stripped field — which would break the API contract).
          2. The freshness gate still correctly classifies the transcript
             as stale because the signal is read from ``history`` BEFORE
             the strip.
          3. No auto-continue note is injected.
        """
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ], "timestamp": time.time() - 7201},
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "stale result",
                "timestamp": time.time() - 7200,  # 2 hours old
            },
        ]
        agent_history = _build_agent_history(history)

        # Invariant 1: strip contract preserved
        assert agent_history[-1]["role"] == "tool"
        assert "timestamp" not in agent_history[-1], (
            "agent_history tool rows must NOT carry a timestamp — the "
            "freshness gate must read from raw history, not agent_history"
        )

        # Invariant 2+3: stale classification, no note injection
        result = _simulate_note_injection(
            history,
            "start a new task",
            resume_entry=None,
            agent_history=agent_history,
        )
        assert result == "start a new task"

    def test_freshness_gate_disabled_via_zero_window(self):
        """window_secs=0 restores pre-fix behaviour (always inject)."""
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ], "timestamp": time.time() - 86400},
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "day-old result",
                "timestamp": time.time() - 86400,  # 24 hours old
            },
        ]
        result = _simulate_note_injection(
            history, "ping", resume_entry=None, window_secs=0,
        )
        assert "[System note:" in result
        assert "pending tool outputs" in result
        assert "Do NOT re-execute" in result

    def test_legacy_history_without_timestamps_fails_closed(self):
        """Unknown freshness cannot authorize synthetic continuation."""
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]
        result = _simulate_note_injection(history, "ping", resume_entry=None)
        assert result == "ping"


# ---------------------------------------------------------------------------
# Freshness helpers
# ---------------------------------------------------------------------------


class TestFreshnessHelpers:


    def test_coerce_iso_string(self):
        iso = "2026-04-18T12:00:00+00:00"
        expected = datetime.fromisoformat(iso).timestamp()
        assert _coerce_gateway_timestamp(iso) == pytest.approx(expected, abs=1e-3)


    def test_coerce_rejects_garbage(self):
        assert _coerce_gateway_timestamp(None) is None
        assert _coerce_gateway_timestamp("") is None
        assert _coerce_gateway_timestamp("not-a-timestamp") is None
        assert _coerce_gateway_timestamp(True) is None  # bool rejected
        assert _coerce_gateway_timestamp(False) is None
        assert _coerce_gateway_timestamp([1, 2, 3]) is None


    def test_is_fresh_window_bounds(self):
        now = 1_700_000_000.0
        # 1h window, 30min old → fresh
        assert _is_fresh_gateway_interruption(
            now - 1800, now=now, window_secs=3600,
        ) is True
        # 1h window, 2h old → stale
        assert _is_fresh_gateway_interruption(
            now - 7200, now=now, window_secs=3600,
        ) is False
        # 1h window, exactly at boundary → fresh (<=)
        assert _is_fresh_gateway_interruption(
            now - 3600, now=now, window_secs=3600,
        ) is True


    def test_last_transcript_timestamp_skips_meta(self):
        history = [
            {"role": "user", "content": "hi", "timestamp": 100.0},
            {"role": "assistant", "content": "hey", "timestamp": 200.0},
            {"role": "session_meta", "content": "tools:{}", "timestamp": 999.0},
            {"role": "system", "content": "ignore", "timestamp": 999.0},
        ]
        assert _last_transcript_timestamp(history) == 200.0


    def test_auto_continue_freshness_window_reads_env(self, monkeypatch):
        monkeypatch.setenv("HERMES_AUTO_CONTINUE_FRESHNESS", "7200")
        assert _auto_continue_freshness_window() == 7200.0

    def test_auto_continue_freshness_window_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("HERMES_AUTO_CONTINUE_FRESHNESS", raising=False)
        # Default is 1 hour
        assert _auto_continue_freshness_window() == 3600.0


# ---------------------------------------------------------------------------
# Drain-timeout path marks sessions resume_pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_timeout_marks_resume_pending(caplog, tmp_path, monkeypatch):
    """End-to-end: a drain timeout during gateway stop should flag every
    active session as resume_pending BEFORE the interrupt fires, so the
    next startup's suspend_recently_active() does not destroy them."""
    runner, adapter = make_restart_runner()
    monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
    adapter.disconnect = AsyncMock()
    runner._restart_drain_timeout = 0.05

    running_agent = MagicMock()
    session_key_one = "agent:main:telegram:dm:A"
    session_key_two = "agent:main:telegram:dm:B"
    runner._running_agents = {
        session_key_one: running_agent,
        session_key_two: MagicMock(),
    }

    # Plug a mock session_store that records marks.
    session_store = MagicMock()
    session_store.mark_resume_pending_with_receipt = MagicMock(return_value=None)
    session_store.mark_resume_pending = MagicMock(return_value=True)
    runner.session_store = session_store

    caplog.set_level(logging.ERROR, logger="gateway.run")
    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await runner.stop()

    # Both active sessions were marked with the shutdown_timeout reason.
    calls = session_store.mark_resume_pending.call_args_list
    marked = {args[0][0] for args in calls}
    assert marked == {session_key_one, session_key_two}
    for args in calls:
        assert args[0][1] == "shutdown_timeout"
    assert sum(
        "could not persist exact pre-drain resume marker" in record.message
        for record in caplog.records
    ) == 2
    assert not (tmp_path / ".clean_shutdown").exists()


@pytest.mark.asyncio
async def test_shutdown_persists_resume_marker_before_notification_await(
    tmp_path, monkeypatch
):
    """A completing turn must not escape the durable pre-drain fence.

    Production regression: the shutdown notification awaited Telegram before
    persisting the continuation.  A turn that finished during that await
    cleared its active-turn marker while it was still present in the gateway's
    running-agent map, so the later premark failed and restart abandoned the
    authorized task.
    """
    runner, adapter = make_restart_runner()
    monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
    adapter.disconnect = AsyncMock()
    runner._restart_drain_timeout = 0.01
    session_key = "agent:hermesdev:telegram:group:-1003971448755:1751"
    runner._running_agents = {session_key: MagicMock()}

    order: list[str] = []
    receipt = {
        "resume_task_id": "turn-token",
        "continuation_generation": 1,
        "continuation_claim_owner": "gateway:test",
        "continuation_claim_token": "claim-token",
    }

    def mark_before_notify(*_args, **_kwargs):
        order.append("mark")
        return receipt

    async def notification_await():
        order.append("notify")

    runner.session_store.mark_resume_pending_with_receipt = MagicMock(
        side_effect=mark_before_notify
    )
    runner._notify_active_sessions_of_shutdown = AsyncMock(
        side_effect=notification_await
    )

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await runner.stop()

    assert order[:2] == ["mark", "notify"]


@pytest.mark.asyncio
async def test_user_restart_aborts_before_stop_when_active_claim_is_unprotected():
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="restart-owner")
    runner._restart_command_source = source
    runner._restart_after_turn_timeout = 0
    runner._running_agents = {
        "agent:main:telegram:dm:protected": MagicMock(),
        "agent:main:telegram:dm:restart-owner": MagicMock(),
    }
    protected_receipt = {
        "resume_task_id": "protected-task",
        "continuation_generation": 2,
        "continuation_claim_owner": "gateway:test",
        "continuation_claim_token": "protected-token",
    }
    runner.session_store.mark_resume_pending_with_receipt = MagicMock(
        side_effect=[protected_receipt, None]
    )
    clear = AsyncMock(return_value=True)
    runner.async_session_store.clear_resume_pending_exact = clear
    runner.stop = AsyncMock()

    assert runner.request_restart(via_service=True)
    await runner._restart_task

    runner.stop.assert_not_awaited()
    assert runner._restart_requested is False
    assert runner._restart_task_started is False
    assert runner._draining is False
    clear.assert_awaited_once_with(
        "agent:main:telegram:dm:protected",
        **protected_receipt,
    )
    assert any(
        "restart cancelled" in message.lower()
        and "recovery marker" in message.lower()
        for message in adapter.sent
    )


@pytest.mark.asyncio
async def test_user_restart_abort_stays_drained_if_marker_cleanup_fails():
    runner, adapter = make_restart_runner()
    runner._restart_command_source = make_restart_source(chat_id="restart-owner")
    runner._restart_after_turn_timeout = 0
    runner._running_agents = {
        "agent:main:telegram:dm:protected": MagicMock(),
        "agent:main:telegram:dm:unprotected": MagicMock(),
    }
    protected_receipt = {
        "resume_task_id": "protected-task",
        "continuation_generation": 2,
        "continuation_claim_owner": "gateway:test",
        "continuation_claim_token": "protected-token",
    }
    runner.session_store.mark_resume_pending_with_receipt = MagicMock(
        side_effect=[protected_receipt, None]
    )
    clear = AsyncMock(return_value=False)
    runner.async_session_store.clear_resume_pending_exact = clear
    runner.stop = AsyncMock()

    assert runner.request_restart(via_service=True)
    await runner._restart_task

    runner.stop.assert_not_awaited()
    assert clear.await_count >= 2
    assert runner._draining is True
    assert any("remains drained" in message.lower() for message in adapter.sent)


# ---------------------------------------------------------------------------
# Gateway startup auto-resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_scheduler_is_admission_only_and_never_reads_transcript(
    monkeypatch,
):
    """The scheduler may claim a route, but cannot mint replay capability."""
    from gateway import restart_loop_guard

    monkeypatch.setattr(restart_loop_guard, "check_and_record", lambda *a, **k: False)
    runner, _adapter = make_restart_runner()
    source = make_restart_source(message_id="restart-message-1")
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key=runner._session_key_for_source(source),
            session_id="sid-admission-only",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
            resume_pending=True,
            resume_reason="restart_interrupted",
            last_resume_marked_at=datetime.now(),
            resume_task_id="task-admission-only",
        )
    )
    runner.session_store._entries = {entry.session_key: entry}
    runner._startup_resume_history_rows = MagicMock(
        side_effect=AssertionError("scheduler read transcript")
    )
    runner._analyze_startup_resume_rows = MagicMock(
        side_effect=AssertionError("scheduler analyzed transcript")
    )
    runner._run_startup_resume_event = AsyncMock(return_value=None)

    assert runner._schedule_resume_pending_sessions() == 1
    await asyncio.sleep(0)

    runner._startup_resume_history_rows.assert_not_called()
    runner._analyze_startup_resume_rows.assert_not_called()
    event = runner._run_startup_resume_event.await_args.args[1]
    assert event.metadata["startup_safe_dangling_calls"] == []
    assert event.metadata["startup_resume_effect_fence"] == {}


def _leased_startup_agent_runner(monkeypatch, tmp_path, history):
    runner = make_agent_runner(monkeypatch, tmp_path)
    source = replace(
        SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1001",
            chat_type="group",
            user_id="12345",
        ),
        message_id="msg-42",
    )
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key="agent:main:telegram:group:-1001:12345",
            session_id="sess-startup-lease",
            created_at=datetime.now(),
            updated_at=datetime.now() + timedelta(microseconds=1),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="group",
            resume_pending=True,
            resume_reason="restart_interrupted",
            last_resume_marked_at=datetime.now(),
            resume_task_id="task-startup-lease",
        )
    )
    runner.session_store.get_or_create_session.return_value = entry
    runner.session_store.lookup_by_session_key.return_value = entry
    runner.session_store.load_transcript.return_value = history
    event = runner._build_startup_resume_event(entry, source)
    return runner, event, source, entry


@pytest.mark.asyncio
async def test_leased_startup_keeps_claim_when_profile_projection_has_no_cas(
    monkeypatch,
    tmp_path,
):
    """Multiplex profile lookup must not erase the root-store continuation."""
    history = [
        {
            "role": "user",
            "content": "continue exact work",
            "platform_message_id": "msg-42",
            "display_metadata": {
                "gateway_raw_semantic_v1": {
                    "version": 1,
                    "message_type": "text",
                    "reply": None,
                    "media": [],
                }
            },
        }
    ]
    runner, event, source, root_entry = _leased_startup_agent_runner(
        monkeypatch,
        tmp_path,
        history,
    )
    profile_entry = replace(
        root_entry,
        resume_pending=False,
        resume_reason=None,
        last_resume_marked_at=None,
        resume_task_id="",
        continuation_claim_owner="",
        continuation_claim_token="",
        resume_origin_snapshot=None,
    )

    assert await runner._validate_and_seal_startup_resume(
        event,
        source,
        profile_entry,
        history,
    )


@pytest.mark.asyncio
async def test_leased_startup_rejects_legacy_text_without_raw_semantic_envelope(
    monkeypatch,
    tmp_path,
):
    history = [
        {
            "role": "user",
            "content": "Fix",
            "platform_message_id": "msg-42",
            "display_metadata": None,
        }
    ]
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, history
    )
    abandon = AsyncMock(return_value=True)
    runner.async_session_store.abandon_resume_pending_exact = abandon

    assert not runner._restore_startup_raw_semantic_envelope(
        event,
        history,
        source_message_id="msg-42",
    )
    assert not await runner._validate_and_seal_startup_resume(
        event,
        source,
        entry,
        history,
    )
    abandon.assert_awaited_once_with(
        entry.session_key,
        resume_task_id=entry.resume_task_id,
        continuation_generation=entry.continuation_generation,
        continuation_claim_owner=entry.continuation_claim_owner,
        continuation_claim_token=entry.continuation_claim_token,
        reason="quarantined_invalid_envelope",
    )


@pytest.mark.asyncio
async def test_leased_startup_restores_direct_reply_semantics_before_preprocessing(
    monkeypatch,
    tmp_path,
):
    history = [
        {
            "role": "user",
            "content": "Fix",
            "platform_message_id": "msg-42",
            "display_metadata": {
                "gateway_raw_semantic_v1": {
                    "version": 1,
                    "message_type": "text",
                    "reply": {
                        "message_id": "guardian-report",
                        "is_own": True,
                        "quote": "Guardian report: candidate transport is stale.",
                    },
                    "media": [],
                }
            },
        }
    ]
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, history
    )

    assert await runner._validate_and_seal_startup_resume(
        event,
        source,
        entry,
        history,
    )
    assert event.reply_to_message_id == "guardian-report"
    assert event.reply_to_is_own_message is True
    assert event.reply_to_text == "Guardian report: candidate transport is stale."


@pytest.mark.asyncio
async def test_leased_startup_restores_archived_authoritative_raw_semantics(
    monkeypatch,
    tmp_path,
):
    """Compaction may archive the trigger row without revoking its authority."""
    active_history = [
        {
            "role": "user",
            "content": "Fix",
            "platform_message_id": "msg-42",
            "display_metadata": None,
        }
    ]
    archived_rows = [
        {
            "role": "user",
            "content": "Fix",
            "platform_message_id": "msg-42",
            "active": 0,
            "display_metadata": {
                "gateway_raw_semantic_v1": {
                    "version": 1,
                    "message_type": "text",
                    "reply": {
                        "message_id": "guardian-report",
                        "is_own": True,
                        "quote": "Use the sealed production reply context.",
                    },
                    "media": [],
                }
            },
        },
        {
            "role": "user",
            "content": "Fix",
            "platform_message_id": "msg-42",
            "active": 0,
            "display_metadata": None,
        },
    ]
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, active_history
    )

    class ArchivedAuthorityDB:
        def get_messages(self, session_id, *, include_inactive=False):
            assert session_id == entry.session_id
            assert include_inactive is True
            return archived_rows

    runner._session_db = ArchivedAuthorityDB()

    assert await runner._validate_and_seal_startup_resume(
        event,
        source,
        entry,
        active_history,
    )
    assert event.reply_to_message_id == "guardian-report"
    assert event.reply_to_text == "Use the sealed production reply context."


@pytest.mark.asyncio
async def test_leased_startup_restores_existing_media_and_rejects_missing_ref(
    monkeypatch,
    tmp_path,
):
    voice = tmp_path / "voice.ogg"
    voice.write_bytes(b"recoverable-voice")

    def _history(ref, *, message_type="voice", media_type="audio/ogg"):
        return [
            {
                "role": "user",
                "content": "",
                "platform_message_id": "msg-42",
                "display_metadata": {
                    "gateway_raw_semantic_v1": {
                        "version": 1,
                        "message_type": message_type,
                        "reply": None,
                        "media": [{"ref": str(ref), "type": media_type}],
                    }
                },
            }
        ]

    existing = _history(voice)
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, existing
    )
    assert await runner._validate_and_seal_startup_resume(
        event,
        source,
        entry,
        existing,
    )
    assert event.media_urls == [str(voice)]
    assert event.media_types == ["audio/ogg"]
    assert event.message_type.value == "voice"

    unknown_mime = _history(
        voice,
        message_type="voice",
        media_type=None,
    )
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, unknown_mime
    )
    assert await runner._validate_and_seal_startup_resume(
        event,
        source,
        entry,
        unknown_mime,
    )
    assert event.media_types == []
    assert event.message_type.value == "voice"

    audio = _history(
        voice,
        message_type="audio",
        media_type="audio/mpeg",
    )
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, audio
    )
    assert await runner._validate_and_seal_startup_resume(
        event,
        source,
        entry,
        audio,
    )
    assert event.media_types == ["audio/mpeg"]
    assert event.message_type.value == "audio"

    missing = _history(tmp_path / "missing.ogg")
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, missing
    )
    assert not await runner._validate_and_seal_startup_resume(
        event,
        source,
        entry,
        missing,
    )


@pytest.mark.asyncio
async def test_leased_startup_handler_analyzes_once_and_passes_exact_fence(
    monkeypatch,
    tmp_path,
):
    """Only the post-lease snapshot can authorize startup replay effects."""
    history = [
        {
            "role": "user",
            "content": "finish this",
            "platform_message_id": "msg-42",
            "display_metadata": {
                "gateway_raw_semantic_v1": {
                    "version": 1,
                    "message_type": "text",
                    "reply": None,
                    "media": [],
                }
            },
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"echo done"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "done",
            "effect_disposition": "completed",
        },
    ]
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, history
    )
    analyzer = MagicMock(wraps=runner._analyze_startup_resume_rows)
    runner._analyze_startup_resume_rows = analyzer
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "done",
            "messages": history + [{"role": "assistant", "content": "done"}],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
            "api_calls": 1,
            "failed": False,
        }
    )

    await runner._handle_message_with_agent(event, source, entry.session_key, 1)

    analyzer.assert_called_once_with(history, source_message_id="msg-42")
    fence = runner._run_agent.await_args.kwargs["startup_resume_effect_fence"]
    assert fence == {
        ("terminal", '{"command":"echo done"}'): "completed_effect_receipt"
    }
    assert event.metadata["startup_resume_effect_fence"] == fence


@pytest.mark.asyncio
async def test_leased_startup_unknown_effect_gets_one_reconciliation_only_turn(
    monkeypatch,
    tmp_path,
):
    """UNKNOWN pre-restart effects are read back, never silently abandoned/replayed."""
    raw_args = '{"command":"deploy candidate"}'
    history = [
        {
            "role": "user",
            "content": "deploy it",
            "platform_message_id": "msg-42",
            "display_metadata": {
                "gateway_raw_semantic_v1": {
                    "version": 1,
                    "message_type": "text",
                    "reply": None,
                    "media": [],
                }
            },
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-unknown",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": raw_args,
                    },
                }
            ],
        },
    ]
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, history
    )
    continuation_identity = (
        event.resume_task_id,
        event.continuation_generation,
        event.continuation_claim_owner,
        event.continuation_claim_token,
    )
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "readback required",
            "messages": history,
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
            "api_calls": 1,
            "failed": False,
        }
    )

    await runner._handle_message_with_agent(event, source, entry.session_key, 1)

    runner._run_agent.assert_awaited_once()
    assert (
        event.resume_task_id,
        event.continuation_generation,
        event.continuation_claim_owner,
        event.continuation_claim_token,
    ) == continuation_identity
    assert event.source == source
    assert event.message_id == "msg-42"
    assert event.metadata["startup_resume_reconciliation_only"] is True
    assert event.metadata["startup_resume_unknown_effects"] == [
        {
            "tool_call_id": "call-unknown",
            "tool_name": "terminal",
            "outcome": "missing_receipt",
            "replay_identity": ("terminal", raw_args),
        }
    ]
    fence = event.metadata["startup_resume_effect_fence"]
    assert fence == {("terminal", raw_args): "unknown_pre_restart_effect"}
    assert runner._run_agent.await_args.kwargs[
        "startup_resume_reconciliation_only"
    ] is True
    assert runner._run_agent.await_args.kwargs["startup_resume_effect_fence"] == fence


@pytest.mark.asyncio
async def test_interrupted_reconciliation_only_turn_abandons_exact_claim(
    monkeypatch,
    tmp_path,
):
    history = [
        {
            "role": "user",
            "content": "deploy it",
            "platform_message_id": "msg-42",
            "display_metadata": {
                "gateway_raw_semantic_v1": {
                    "version": 1,
                    "message_type": "text",
                    "reply": None,
                    "media": [],
                }
            },
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-unknown",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"deploy candidate"}',
                    },
                }
            ],
        },
    ]
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, history
    )
    abandon = AsyncMock(return_value=True)
    runner.async_session_store.abandon_resume_pending_exact = abandon
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "",
            "messages": history,
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
            "api_calls": 1,
            "interrupted": True,
        }
    )

    await runner._handle_message_with_agent(event, source, entry.session_key, 1)

    abandon.assert_awaited_once_with(
        entry.session_key,
        resume_task_id=entry.resume_task_id,
        continuation_generation=entry.continuation_generation,
        continuation_claim_owner=entry.continuation_claim_owner,
        continuation_claim_token=entry.continuation_claim_token,
        reason="quarantined_unsafe_unknown",
    )


@pytest.mark.asyncio
async def test_unknown_effect_does_not_bypass_raw_semantic_envelope_rejection(
    monkeypatch,
    tmp_path,
):
    history = [
        {
            "role": "user",
            "content": "deploy it",
            "platform_message_id": "msg-42",
            "display_metadata": None,
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-unknown",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"deploy candidate"}',
                    },
                }
            ],
        },
    ]
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, history
    )

    assert not await runner._validate_and_seal_startup_resume(
        event,
        source,
        entry,
        history,
    )
    assert "startup_resume_reconciliation_only" not in event.metadata


@pytest.mark.asyncio
async def test_leased_startup_handler_rejects_later_human_before_agent(
    monkeypatch,
    tmp_path,
):
    """A human message appended before lease acquisition owns the route."""
    history = [
        {
            "role": "user",
            "content": "old task",
            "platform_message_id": "msg-42",
        },
        {
            "role": "user",
            "content": "new task",
            "platform_message_id": "msg-43",
        },
    ]
    runner, event, source, entry = _leased_startup_agent_runner(
        monkeypatch, tmp_path, history
    )
    analyzer = MagicMock(wraps=runner._analyze_startup_resume_rows)
    runner._analyze_startup_resume_rows = analyzer
    runner._run_agent = AsyncMock()
    abandon = AsyncMock(return_value=True)
    runner.async_session_store.abandon_resume_pending_exact = abandon

    await runner._handle_message_with_agent(event, source, entry.session_key, 1)

    analyzer.assert_called_once_with(history, source_message_id="msg-42")
    runner._run_agent.assert_not_awaited()
    abandon.assert_awaited_once_with(
        entry.session_key,
        resume_task_id=entry.resume_task_id,
        continuation_generation=entry.continuation_generation,
        continuation_claim_owner=entry.continuation_claim_owner,
        continuation_claim_token=entry.continuation_claim_token,
        reason="superseded_by_human",
    )


@pytest.mark.asyncio
async def test_startup_auto_resume_skips_unauthorized_owner():
    """A resume-pending session whose owner is no longer authorized under the
    current allowlist must not receive a synthesized agent turn on restart.

    Auto-resume dispatches a full agent turn without going through the normal
    inbound-message auth gate, so it re-checks _is_user_authorized here
    (issue #23778).  An unauthorized owner is skipped WITHOUT claiming a
    _running_agents slot or persisting one — the slot claim happens only
    after this gate passes.
    """
    runner, adapter = make_restart_runner()
    runner._is_user_authorized = lambda _source: False
    runner._persist_active_agents = MagicMock()
    source = make_restart_source(chat_id="revoked-chat")
    pending_entry = SessionEntry(
        session_key="agent:main:telegram:dm:revoked-chat",
        session_id="sid",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=datetime.now(),
        resume_task_id="restart-timeout-task",
    )
    runner.session_store._entries = {pending_entry.session_key: pending_entry}
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 0
    adapter.handle_message.assert_not_called()
    # No slot was claimed and nothing was persisted for the skipped session.
    assert pending_entry.session_key not in runner._running_agents
    runner._persist_active_agents.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_reschedule_is_platform_scoped():
    """The platform filter limits the pass to that platform's sessions, so
    reconnecting one platform never resumes another's pending session."""
    runner, adapter = make_restart_runner()
    tg_source = make_restart_source(chat_id="tg-chat")
    discord_source = SessionSource(
        platform=Platform.DISCORD, chat_id="dc-chat", chat_type="dm", user_id="u1"
    )
    tg_entry = bind_restart_origin_snapshot(SessionEntry(
        session_key="agent:main:telegram:dm:tg-chat",
        session_id="sid-tg",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=tg_source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="telegram-restart-task",
    ))
    discord_entry = SessionEntry(
        session_key="agent:main:discord:dm:dc-chat",
        session_id="sid-dc",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=discord_source,
        platform=Platform.DISCORD,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="discord-restart-task",
    )
    runner.session_store._entries = {
        tg_entry.session_key: tg_entry,
        discord_entry.session_key: discord_entry,
    }
    runner._session_db = _ResumeObligationDB(tg_entry)
    adapter.handle_message = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}

    scheduled = runner._schedule_resume_pending_sessions(platform=Platform.TELEGRAM)
    await asyncio.sleep(0)

    # Only the telegram session is resumed; the discord session waits for its
    # own reconnect.
    assert scheduled == 1
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.source == tg_source


@pytest.mark.asyncio
async def test_startup_auto_resume_rejects_session_key_origin_mismatch(monkeypatch):
    """A persisted key for recipient A may never resume origin B."""
    from gateway import restart_loop_guard

    monkeypatch.setattr(restart_loop_guard, "check_and_record", lambda *a, **k: False)
    runner, adapter = make_restart_runner()
    origin_b = make_restart_source(chat_id="recipient-B")
    entry = bind_restart_origin_snapshot(SessionEntry(
        session_key="agent:main:telegram:dm:recipient-A",
        session_id="sid-origin-mismatch",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=origin_b,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="origin-mismatch-task",
    ))
    runner.session_store._entries = {entry.session_key: entry}
    runner._persist_active_agents = MagicMock()
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 0
    adapter.handle_message.assert_not_called()
    runner._persist_active_agents.assert_not_called()
    assert entry.session_key not in runner._running_agents
    assert entry.resume_pending is True


@pytest.mark.asyncio
async def test_startup_auto_resume_binds_business_transport_dimension(monkeypatch):
    from gateway import restart_loop_guard

    monkeypatch.setattr(restart_loop_guard, "check_and_record", lambda *a, **k: False)
    runner, adapter = make_restart_runner()
    original = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="recipient-A",
        chat_type="dm",
        user_id="owner-A",
        transport_profile="default",
        business_connection_id="business-A",
        external_safe_mode=True,
    )
    mutated = replace(original, transport_profile="transport-B")
    entry = bind_restart_origin_snapshot(SessionEntry(
        session_key=runner._session_key_for_source(original),
        session_id="sid-transport-mismatch",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=original,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="task-transport-mismatch",
    ))
    entry.origin = mutated
    runner.session_store._entries = {entry.session_key: entry}
    adapter.handle_message = AsyncMock()

    assert runner._schedule_resume_pending_sessions() == 0
    await asyncio.sleep(0)
    adapter.handle_message.assert_not_called()
    assert entry.resume_pending is True


@pytest.mark.asyncio
async def test_startup_auto_resume_rejects_registered_same_key_transport_swap(
    monkeypatch,
):
    from gateway import restart_loop_guard
    from tests.gateway.restart_test_helpers import RestartTestAdapter

    monkeypatch.setattr(restart_loop_guard, "check_and_record", lambda *a, **k: False)
    runner, _default_adapter = make_restart_runner()
    runner.config.multiplex_profiles = True
    transport_b = RestartTestAdapter()
    transport_b._owner_profile = "transport-B"
    runner._profile_adapters = {
        "transport-B": {Platform.TELEGRAM: transport_b},
    }
    original = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="recipient-A",
        chat_type="dm",
        user_id="recipient-A",
        transport_profile="default",
    )
    entry = bind_restart_origin_snapshot(SessionEntry(
        session_key=runner._session_key_for_source(original),
        session_id="sid-registered-transport-swap",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=original,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="task-registered-transport-swap",
    ))
    entry.origin = replace(original, transport_profile="transport-B")
    runner.session_store._entries = {entry.session_key: entry}
    runner._run_startup_resume_event = AsyncMock(return_value=None)

    assert runner._schedule_resume_pending_sessions() == 0
    await asyncio.sleep(0)
    runner._run_startup_resume_event.assert_not_awaited()
    assert entry.resume_pending is True


@pytest.mark.asyncio
async def test_startup_auto_resume_uses_snapshot_transport_adapter(monkeypatch):
    from gateway import restart_loop_guard
    from tests.gateway.restart_test_helpers import RestartTestAdapter

    monkeypatch.setattr(restart_loop_guard, "check_and_record", lambda *a, **k: False)
    runner, default_adapter = make_restart_runner()
    runner.config.multiplex_profiles = True
    transport_b = RestartTestAdapter()
    transport_b._owner_profile = "transport-B"
    runner._profile_adapters = {
        "transport-B": {Platform.TELEGRAM: transport_b},
    }
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="recipient-B",
        chat_type="dm",
        user_id="recipient-B",
        message_id="restart-message-1",
        transport_profile="transport-B",
    )
    entry = bind_restart_origin_snapshot(SessionEntry(
        session_key=runner._session_key_for_source(source),
        session_id="sid-transport-B",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="task-transport-B",
    ))
    runner.session_store._entries = {entry.session_key: entry}
    runner._run_startup_resume_event = AsyncMock(return_value=None)

    assert runner._schedule_resume_pending_sessions() == 1
    await asyncio.sleep(0)
    selected = runner._run_startup_resume_event.await_args.args[0]
    assert selected is transport_b
    assert selected is not default_adapter


@pytest.mark.asyncio
async def test_startup_auto_resume_rejects_legacy_entry_without_origin_snapshot(
    monkeypatch,
):
    from gateway import restart_loop_guard

    monkeypatch.setattr(restart_loop_guard, "check_and_record", lambda *a, **k: False)
    runner, _adapter = make_restart_runner()
    source = make_restart_source(chat_id="legacy-no-snapshot")
    entry = SessionEntry(
        session_key=runner._session_key_for_source(source),
        session_id="sid-legacy-no-snapshot",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="task-legacy-no-snapshot",
    )
    runner.session_store._entries = {entry.session_key: entry}
    runner._run_startup_resume_event = AsyncMock(return_value=None)

    assert runner._schedule_resume_pending_sessions() == 0
    await asyncio.sleep(0)
    runner._run_startup_resume_event.assert_not_awaited()
    assert entry.resume_pending is True


@pytest.mark.asyncio
async def test_multiplex_default_profile_origin_still_auto_resumes(monkeypatch):
    from gateway import restart_loop_guard

    monkeypatch.setattr(restart_loop_guard, "check_and_record", lambda *a, **k: False)
    runner, _adapter = make_restart_runner()
    runner.config.multiplex_profiles = True
    source = make_restart_source(
        chat_id="recipient-default",
        message_id="restart-message-1",
    )
    entry = bind_restart_origin_snapshot(SessionEntry(
        session_key="agent:main:telegram:dm:recipient-default",
        session_id="sid-default-profile",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="task-default-profile",
    ))
    runner.session_store._entries = {entry.session_key: entry}
    runner._run_startup_resume_event = AsyncMock(return_value=None)

    assert runner._schedule_resume_pending_sessions() == 1
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_startup_restore_waits_for_resume_before_draining_inbound():
    """Queued inbound turns replay only after startup resume tasks finish."""
    runner, adapter = make_restart_runner()
    runner._startup_restore_in_progress = True
    runner._startup_restore_queue = []
    runner._startup_restore_tasks = []

    source = make_restart_source(
        chat_id="restore-chat",
        message_id="restart-message-1",
    )
    pending_entry = bind_restart_origin_snapshot(SessionEntry(
        session_key="agent:main:telegram:dm:restore-chat",
        session_id="sid",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="startup-restore-task",
    ))
    runner.session_store._entries = {pending_entry.session_key: pending_entry}
    runner._session_db = _ResumeObligationDB(pending_entry)

    resume_done = asyncio.Event()
    seen: list[str] = []

    async def fake_handle_message(event: MessageEvent) -> None:
        if event.internal:
            seen.append("resume-start")
            task = asyncio.create_task(resume_done.wait())
            adapter._session_tasks[pending_entry.session_key] = task
            return
        seen.append(f"inbound:{event.text}")

    adapter.handle_message = fake_handle_message

    scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    inbound = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=source,
    )
    assert await runner._handle_message(inbound) is None
    assert scheduled == 1
    assert seen == ["resume-start"]
    assert runner._startup_restore_queue == [inbound]

    finish_task = asyncio.create_task(runner._finish_startup_restore())
    await asyncio.sleep(0)
    assert seen == ["resume-start"]

    resume_done.set()
    await finish_task

    assert seen == ["resume-start", "inbound:hello"]
    assert runner._startup_restore_queue == []
    assert runner._startup_restore_in_progress is False


# ---------------------------------------------------------------------------
# Shutdown banner wording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_notifies_home_channel_even_without_active_sessions():
    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="home-42",
        name="Ops Home",
    )

    await runner._notify_active_sessions_of_shutdown()

    assert adapter.sent == [
        "⚠️ Gateway restarting — Your current task will be interrupted. "
        "Send any message after restart and I'll try to resume where you left off."
    ]


@pytest.mark.asyncio
async def test_restart_home_channel_notification_not_deduped_across_threads():
    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    session_key = "agent:main:telegram:group:999"
    runner.session_store._entries[session_key] = MagicMock(
        origin=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="999",
            chat_type="group",
            user_id="u1",
            thread_id="topic-7",
        )
    )
    runner._running_agents[session_key] = MagicMock()
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="999",
        name="Ops Home",
    )

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 2
    assert adapter.sent_calls[0][2] == {"thread_id": "topic-7"}
    assert adapter.sent_calls[1][2] is None


# ---------------------------------------------------------------------------
# Stuck-loop escalation integration
# ---------------------------------------------------------------------------


class TestStuckLoopEscalation:
    """The existing .restart_failure_counts counter (PR #7536) remains the
    single source of terminal escalation — no parallel counter on
    SessionEntry was added.  After the configured threshold, the startup
    path flips suspended=True which overrides resume_pending."""

    def test_escalation_via_stuck_loop_counter_overrides_resume_pending(
        self, tmp_path, monkeypatch
    ):
        """Simulate a session that keeps getting restart-interrupted and
        hits the stuck-loop threshold: next startup should force it to
        fresh-session despite resume_pending being set."""
        import json

        from gateway.run import GatewayRunner

        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        store.mark_resume_pending(entry.session_key, reason="restart_timeout")

        # Simulate counter already at threshold (3 consecutive interrupted
        # restarts).  _suspend_stuck_loop_sessions will flip suspended=True.
        counts_file = tmp_path / ".restart_failure_counts"
        counts_file.write_text(json.dumps({entry.session_key: 3}))

        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
        runner = object.__new__(GatewayRunner)
        runner.session_store = store

        suspended_count = GatewayRunner._suspend_stuck_loop_sessions(runner)
        assert suspended_count == 1
        assert store._entries[entry.session_key].suspended is True
        # resume_pending is still set on the entry, but suspended wins in
        # get_or_create_session so the next message still gets a new sid.
        second = store.get_or_create_session(source)
        assert second.session_id != entry.session_id
        assert second.auto_reset_reason == "suspended"


@pytest.mark.asyncio
async def test_auto_resume_sets_sentinel_before_task_execution():
    """Auto-resume must claim the session slot before the task starts.

    Regression for #45456: between ``asyncio.create_task()`` and the task's
    first await (where ``_process_message_background`` sets the real
    sentinel), an inbound message could arrive and spin up a duplicate
    AIAgent.  The fix pre-claims the slot so the inbound path sees it as
    occupied.
    """
    runner, adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="race-chat",
        message_id="restart-message-1",
    )
    pending_entry = bind_restart_origin_snapshot(SessionEntry(
        session_key="agent:main:telegram:dm:race-chat",
        session_id="sid",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="race-restart-task",
    ))
    runner.session_store._entries = {pending_entry.session_key: pending_entry}

    # Slow mock: hold the task open so we can inspect _running_agents
    # while it's in-flight.
    gate = asyncio.Event()

    async def _slow_handle(event):
        await gate.wait()

    adapter.handle_message = _slow_handle

    scheduled = runner._schedule_resume_pending_sessions()

    assert scheduled == 1
    # The sentinel must be set immediately — before the task starts executing.
    assert pending_entry.session_key in runner._running_agents
    assert runner._running_agents[pending_entry.session_key] is _AGENT_PENDING_SENTINEL
    assert pending_entry.session_key in runner._running_agents_ts

    # Release the task and let it complete.
    gate.set()
    await asyncio.sleep(0.05)

    # After the task completes, the sentinel should be cleaned up.
    assert pending_entry.session_key not in runner._running_agents


@pytest.mark.asyncio
async def test_startup_adapter_failure_settles_preclaimed_obligation():
    runner, adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="adapter-failure",
        message_id="restart-message-1",
    )
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key="agent:main:telegram:dm:adapter-failure",
            session_id="sid-adapter-failure",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
            resume_pending=True,
            resume_reason="restart_interrupted",
            last_resume_marked_at=datetime.now(),
            resume_task_id="task-adapter-failure",
        )
    )
    runner.session_store._entries = {entry.session_key: entry}
    runner._session_state(entry.session_key).turn.agent = _AGENT_PENDING_SENTINEL
    event = runner._build_startup_resume_event(entry, source)
    event.metadata["startup_resume_after_priority_reply"] = True
    adapter.handle_message = AsyncMock(side_effect=RuntimeError("adapter failed"))
    abandon = AsyncMock(return_value=True)
    runner.async_session_store.abandon_resume_pending_exact = abandon

    with pytest.raises(RuntimeError, match="adapter failed"):
        await runner._run_startup_resume_event(adapter, event, entry.session_key)

    abandon.assert_awaited_once_with(
        entry.session_key,
        resume_task_id=entry.resume_task_id,
        continuation_generation=entry.continuation_generation,
        continuation_claim_owner=entry.continuation_claim_owner,
        continuation_claim_token=entry.continuation_claim_token,
        reason="quarantined_invalid_envelope",
    )
    assert entry.session_key not in runner._running_agents


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_started", "expected_reason"),
    [
        (False, "quarantined_invalid_envelope"),
        (True, "quarantined_unsafe_unknown"),
    ],
)
async def test_startup_wrapper_settles_claim_after_handler_releases_slot(
    agent_started,
    expected_reason,
):
    """Handler early-return/exception cannot strand a transferred claim."""
    runner, adapter = make_restart_runner()
    source = make_restart_source(
        chat_id=f"handler-failure-{agent_started}",
        message_id="restart-message-1",
    )
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key=f"agent:main:telegram:dm:handler-failure-{agent_started}",
            session_id="sid-handler-failure",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
            resume_pending=True,
            resume_reason="restart_interrupted",
            last_resume_marked_at=datetime.now(),
            resume_task_id="task-handler-failure",
        )
    )
    runner.session_store._entries = {entry.session_key: entry}
    runner._session_state(entry.session_key).turn.agent = _AGENT_PENDING_SENTINEL
    event = runner._build_startup_resume_event(entry, source)
    event.metadata["startup_resume_after_priority_reply"] = True
    if agent_started:
        event.metadata["startup_resume_agent_started"] = True

    async def _handler_released_slot(_event):
        runner._release_running_agent_state(entry.session_key)

    adapter.handle_message = _handler_released_slot
    abandon = AsyncMock(return_value=True)
    runner.async_session_store.abandon_resume_pending_exact = abandon

    await runner._run_startup_resume_event(adapter, event, entry.session_key)

    abandon.assert_awaited_once_with(
        entry.session_key,
        resume_task_id=entry.resume_task_id,
        continuation_generation=entry.continuation_generation,
        continuation_claim_owner=entry.continuation_claim_owner,
        continuation_claim_token=entry.continuation_claim_token,
        reason=expected_reason,
    )


@pytest.mark.asyncio
async def test_cancelled_startup_wrapper_keeps_claim_while_shielded_child_runs():
    runner, adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="live-shielded-child",
        message_id="restart-message-1",
    )
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key="agent:main:telegram:dm:live-shielded-child",
            session_id="sid-live-child",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
            resume_pending=True,
            resume_reason="restart_interrupted",
            last_resume_marked_at=datetime.now(),
            resume_task_id="task-live-child",
        )
    )
    runner.session_store._entries = {entry.session_key: entry}
    runner._session_state(entry.session_key).turn.agent = _AGENT_PENDING_SENTINEL
    event = runner._build_startup_resume_event(entry, source)
    event.metadata["startup_resume_after_priority_reply"] = True
    child_started = asyncio.Event()
    release_child = asyncio.Event()

    async def _child():
        child_started.set()
        await release_child.wait()

    async def _start_child(_event):
        adapter._session_tasks[entry.session_key] = asyncio.create_task(_child())

    adapter.handle_message = _start_child
    abandon = AsyncMock(return_value=True)
    runner.async_session_store.abandon_resume_pending_exact = abandon

    wrapper = asyncio.create_task(
        runner._run_startup_resume_event(adapter, event, entry.session_key)
    )
    await child_started.wait()
    wrapper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wrapper

    abandon.assert_not_awaited()
    assert not adapter._session_tasks[entry.session_key].done()

    release_child.set()
    await adapter._session_tasks[entry.session_key]
    runner._release_running_agent_state(entry.session_key)


@pytest.mark.asyncio
async def test_startup_wrapper_keeps_claim_for_runner_owned_multiplex_child():
    """A profile adapter may transfer work without exposing its child task map."""
    runner, adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="multiplex-runner-child",
        message_id="restart-message-1",
    )
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key="agent:main:telegram:dm:multiplex-runner-child",
            session_id="sid-multiplex-runner-child",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
            resume_pending=True,
            resume_reason="restart_interrupted",
            last_resume_marked_at=datetime.now(),
            resume_task_id="task-multiplex-runner-child",
        )
    )
    runner.session_store._entries = {entry.session_key: entry}
    runner._session_state(entry.session_key).turn.agent = _AGENT_PENDING_SENTINEL
    event = runner._build_startup_resume_event(entry, source)
    event.metadata["startup_resume_after_priority_reply"] = True

    async def _transfer_to_runner(_event):
        runner._session_state(entry.session_key).turn.agent = object()

    adapter.handle_message = _transfer_to_runner
    abandon = AsyncMock(return_value=True)
    runner.async_session_store.abandon_resume_pending_exact = abandon

    await runner._run_startup_resume_event(adapter, event, entry.session_key)

    abandon.assert_not_awaited()
    assert runner._is_session_running(entry.session_key)


@pytest.mark.asyncio
async def test_wrapper_retries_success_settlement_without_changing_disposition():
    runner, adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="transient-success-settlement",
        message_id="restart-message-1",
    )
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key="agent:main:telegram:dm:transient-success-settlement",
            session_id="sid-transient-success",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
            resume_pending=True,
            resume_reason="restart_interrupted",
            last_resume_marked_at=datetime.now(),
            resume_task_id="task-transient-success",
        )
    )
    runner.session_store._entries = {entry.session_key: entry}
    runner._session_state(entry.session_key).turn.agent = _AGENT_PENDING_SENTINEL
    event = runner._build_startup_resume_event(entry, source)
    event.metadata["startup_resume_after_priority_reply"] = True
    clear = AsyncMock(side_effect=[False, True])
    abandon = AsyncMock(return_value=True)
    runner.async_session_store.clear_resume_pending_exact = clear
    runner.async_session_store.abandon_resume_pending_exact = abandon

    identity = {
        "resume_task_id": entry.resume_task_id,
        "continuation_generation": entry.continuation_generation,
        "continuation_claim_owner": entry.continuation_claim_owner,
        "continuation_claim_token": entry.continuation_claim_token,
    }

    async def _handler_attempted_success(_event):
        _event.metadata["startup_resume_agent_started"] = True
        await runner._settle_startup_resume_claim(
            entry.session_key,
            identity,
            disposition="success",
            event=_event,
        )
        runner._release_running_agent_state(entry.session_key)

    adapter.handle_message = _handler_attempted_success

    await runner._run_startup_resume_event(adapter, event, entry.session_key)

    assert clear.await_count == 2
    abandon.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_resume_runs_agent_exactly_once_through_full_path():
    """Full-path regression: the pre-claim must NOT make auto-resume a no-op.

    The two tests above mock ``adapter.handle_message`` outright, so they
    only prove the sentinel is set/cleaned around a stub — they never
    exercise the real dispatch chain.  This drives the production path
    end to end:

        _schedule_resume_pending_sessions
          -> _guarded_handle_message
            -> adapter.handle_message            (real)
              -> _process_message_background      (real)
                -> _handle_message                (real)

    The risk the pre-claim introduces is a *self-bounce*: the resume
    turn's own ``_handle_message`` sees the sentinel it pre-claimed at
    the early running-agent guard, queues the event into
    ``_pending_messages`` and returns ``None`` without running the
    agent.  The adapter's late-arrival drain (in
    ``_process_message_background``'s ``finally``) re-dispatches the
    queued event, and because the guard wrapper's ``finally`` releases
    the pre-claim before the spawned drain task starts, the agent runs
    exactly once.  This test locks that invariant in: the resume agent
    must run once — never zero (regression) and never twice (the bug
    the fix targets).
    """
    runner, adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="full-path-chat",
        message_id="restart-message-1",
    )
    session_key = runner._session_key_for_source(source)
    pending_entry = bind_restart_origin_snapshot(SessionEntry(
        session_key=session_key,
        session_id="sid",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
        resume_task_id="full-path-restart-task",
    ))
    runner.session_store._entries = {session_key: pending_entry}
    runner._session_db = _ResumeObligationDB(pending_entry)

    # Wire the REAL runner pipeline that _handle_message depends on.
    from gateway.run import GatewayRunner

    runner._handle_message = GatewayRunner._handle_message.__get__(
        runner, GatewayRunner
    )
    runner._release_running_agent_state = (
        GatewayRunner._release_running_agent_state.__get__(runner, GatewayRunner)
    )
    runner._check_slash_access = lambda *a, **k: None
    runner._begin_session_run_generation = lambda session_key: 1
    runner._is_session_run_current = lambda session_key, generation: True
    runner._invalidate_session_run_generation = lambda *a, **k: 0
    runner._claim_active_session_slot = lambda session_key, source: (object(), None)
    runner._active_session_leases = {}
    runner._busy_ack_ts = {}
    runner._post_turn_goal_continuation = AsyncMock()
    runner.session_store.get_or_create_session.return_value = None

    # Count how many times an actual agent run is started for this session.
    agent_runs: list[str] = []

    async def _fake_run(event, source, _quick_key, run_generation):
        agent_runs.append(_quick_key)
        return "RESUMED OK"

    runner._handle_message_with_agent = _fake_run

    # Route the adapter's real background pipeline at the real handler,
    # and stub the leaf send/typing calls so delivery is a no-op.
    adapter.set_message_handler(runner._handle_message)
    adapter.send = AsyncMock()
    adapter._keep_typing = AsyncMock()
    adapter._stop_typing_refresh = AsyncMock()
    adapter._send_with_retry = AsyncMock(
        return_value=SendResult(success=True, message_id="1")
    )
    adapter._run_processing_hook = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()
    assert scheduled == 1
    # Pre-claim must be visible immediately.
    assert runner._running_agents.get(session_key) is _AGENT_PENDING_SENTINEL

    # Let the guarded task, the background task, and the late-arrival
    # drain task all settle.
    for _ in range(20):
        await asyncio.sleep(0.02)

    # Exactly one agent run for the resumed session — not zero (the
    # pre-claim did not swallow the resume) and not two (no duplicate).
    assert agent_runs == [session_key]
    # No leaked sentinel and no orphaned queued event.
    assert session_key not in runner._running_agents
    assert session_key not in getattr(adapter, "_pending_messages", {})


class _ResumeObligationDB:
    def __init__(self, entry):
        snapshot = entry.resume_origin_snapshot
        self.row = {
            "session_key": entry.session_key,
            "resume_task_id": entry.resume_task_id,
            "generation": entry.continuation_generation,
            "state": "PENDING",
            "origin_json": json.dumps(
                snapshot["source"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "origin_sha256": snapshot["source_sha256"],
            "reason": entry.resume_reason,
            "marked_at": entry.last_resume_marked_at.timestamp(),
        }
        self.claim_calls = 0

    def get_gateway_resume_obligation(self, session_key):
        return dict(self.row) if session_key == self.row["session_key"] else None

    def claim_gateway_resume_obligation(self, **kwargs):
        self.claim_calls += 1
        if self.row["state"] != "PENDING":
            return False
        if (
            kwargs["session_key"] != self.row["session_key"]
            or kwargs["resume_task_id"] != self.row["resume_task_id"]
            or kwargs["expected_generation"] != self.row["generation"]
        ):
            return False
        self.row["state"] = "CLAIMED"
        self.row["claim_owner"] = kwargs["claim_owner"]
        self.row["claim_token"] = kwargs["claim_token"]
        return True


@pytest.mark.asyncio
async def test_new_gateway_reclaims_exact_previously_claimed_startup_obligation():
    """A crash after CLAIMED must not strand the continuation forever."""
    runner, _adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="claimed-across-restart",
        message_id="message-a",
    )
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key=runner._session_key_for_source(source),
            session_id="sid-claimed-across-restart",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
            resume_pending=True,
            resume_reason="restart_interrupted",
            last_resume_marked_at=datetime.now(),
            resume_task_id="task-a",
        )
    )
    obligation_db = _ResumeObligationDB(entry)
    obligation_db.row.update(
        {
            "state": "CLAIMED",
            "claim_owner": entry.continuation_claim_owner,
            "claim_token": entry.continuation_claim_token,
        }
    )

    def claim_exact(**kwargs):
        obligation_db.claim_calls += 1
        return (
            obligation_db.row["state"] == "CLAIMED"
            and kwargs["claim_owner"] == obligation_db.row["claim_owner"]
            and kwargs["claim_token"] == obligation_db.row["claim_token"]
        )

    obligation_db.claim_gateway_resume_obligation = claim_exact
    runner._session_db = obligation_db

    assert await runner._claim_startup_resume_obligation(
        entry,
        allow_existing_claim=True,
    )
    assert obligation_db.claim_calls == 1


@pytest.mark.asyncio
async def test_newer_human_b_runs_before_exact_owed_a_once():
    runner, adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="b-before-a",
        message_id="message-a",
    )
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key=runner._session_key_for_source(source),
            session_id="sid-b-before-a",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
            resume_pending=True,
            resume_reason="restart_interrupted",
            last_resume_marked_at=datetime.now(),
            resume_task_id="task-a",
        )
    )
    runner.session_store._entries = {entry.session_key: entry}
    obligation_db = _ResumeObligationDB(entry)
    runner._session_db = obligation_db
    observed = ["B"]

    async def handle(event):
        observed.append("A")
        assert event.resume_task_id == "task-a"
        assert event.continuation_generation == entry.continuation_generation

    adapter.handle_message = handle

    assert await runner._continue_resume_pending_after_priority_reply(
        entry, source, adapter
    )
    assert not await runner._continue_resume_pending_after_priority_reply(
        entry, source, adapter
    )
    assert observed == ["B", "A"]
    assert obligation_db.claim_calls == 1
    assert entry.resume_pending is True


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [None, datetime.now() - timedelta(days=1)])
async def test_priority_continuation_fails_closed_for_unmarked_or_stale_a(marker):
    runner, adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="stale-a",
        message_id="message-a",
    )
    entry = bind_restart_origin_snapshot(
        SessionEntry(
            session_key=runner._session_key_for_source(source),
            session_id="sid-stale-a",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
            resume_pending=True,
            resume_reason="restart_interrupted",
            last_resume_marked_at=marker,
            resume_task_id="task-a",
        )
    )
    runner.session_store._entries = {entry.session_key: entry}
    runner._session_db = _ResumeObligationDB(entry) if marker is not None else MagicMock()
    adapter.handle_message = AsyncMock()

    assert not await runner._continue_resume_pending_after_priority_reply(
        entry, source, adapter
    )
    adapter.handle_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Startup-restore inbound gate must be BOUNDED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_restore_gate_releases_when_resume_turn_outlives_timeout(
    monkeypatch,
):
    """A single slow boot-resume turn must not hold the inbound gate shut.

    While ``_startup_restore_in_progress`` is set, every inbound message is
    QUEUED instead of answered.  The gate is opened by
    ``_finish_startup_restore``, which waits on the synthetic boot
    auto-resume turns.  Without a bound, one pathologically long resumed
    turn holds the gate — and therefore every channel's inbound queue —
    for the entire duration of that turn.
    """
    monkeypatch.setenv("HERMES_STARTUP_RESTORE_DRAIN_TIMEOUT", "0.05")

    runner, adapter = make_restart_runner()
    runner._startup_restore_in_progress = True
    runner._startup_restore_queue = []
    runner._background_tasks = set()

    seen: list[str] = []
    never_finishes = asyncio.Event()

    async def slow_resume_turn() -> None:
        await never_finishes.wait()

    async def fake_handle_message(event: MessageEvent) -> None:
        seen.append(f"inbound:{event.text}")

    adapter.handle_message = fake_handle_message

    slow_task = asyncio.create_task(slow_resume_turn())
    runner._startup_restore_tasks = [slow_task]

    inbound = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=make_restart_source(chat_id="restore-chat"),
    )
    assert await runner._handle_message(inbound) is None
    assert runner._startup_restore_queue == [inbound]

    # The gate must release on the bound even though the resume turn is
    # still running.
    await asyncio.wait_for(runner._finish_startup_restore(), timeout=5)

    assert seen == ["inbound:hello"], (
        "startup-restore gate never released: queued inbound was not drained "
        "while a slow boot-resume turn was still running"
    )
    assert runner._startup_restore_queue == []
    assert runner._startup_restore_in_progress is False
    # The slow turn is NOT cancelled — it finishes in the background.
    assert not slow_task.done()

    never_finishes.set()
    await slow_task


@pytest.mark.asyncio
async def test_startup_restore_gate_releases_when_boot_path_send_hangs(
    monkeypatch,
):
    """A hung restart notification / obligation redelivery must not freeze inbound.

    Those sends used to run *before* ``_finish_startup_restore`` released the
    gate. A Telegram flood-control sleep on either call queued inbound on
    every platform for the full ``retry_after``.
    """
    monkeypatch.setenv("HERMES_STARTUP_RESTORE_DRAIN_TIMEOUT", "0.05")

    runner, adapter = make_restart_runner()
    runner._startup_restore_in_progress = True
    runner._startup_restore_queue = []
    runner._startup_restore_tasks = []
    runner._background_tasks = set()

    hung = asyncio.Event()

    async def never_returns(*_args, **_kwargs):
        await hung.wait()
        return None

    runner._send_restart_notification = never_returns
    runner._claim_pending_obligations = AsyncMock(return_value=[])
    runner._redeliver_claimed_obligations = AsyncMock(return_value=0)

    seen: list[str] = []

    async def fake_handle_message(event: MessageEvent) -> None:
        seen.append(f"inbound:{event.text}")

    adapter.handle_message = fake_handle_message

    inbound = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=make_restart_source(chat_id="restore-chat"),
    )
    assert await runner._handle_message(inbound) is None
    assert runner._startup_restore_queue == [inbound]

    await asyncio.wait_for(
        runner._await_startup_boot_sends(
            planned_restart_notification_pending=False,
        ),
        timeout=5,
    )
    await asyncio.wait_for(runner._finish_startup_restore(), timeout=5)

    assert seen == ["inbound:hello"], (
        "startup-restore gate never released: queued inbound was not drained "
        "while a boot-path send was still sleeping"
    )
    assert runner._startup_restore_queue == []
    assert runner._startup_restore_in_progress is False
    # The DB half (claim + resume clear) runs inline BEFORE the abandonable
    # send task, so it must have completed even though the boot send hung;
    # the network half never ran because the hung notification precedes it.
    runner._claim_pending_obligations.assert_awaited_once()
    runner._redeliver_claimed_obligations.assert_not_awaited()

    hung.set()
    leftover = [t for t in list(runner._background_tasks) if not t.done()]
    if leftover:
        await asyncio.wait(leftover)


@pytest.mark.asyncio
async def test_startup_boot_sends_still_run_when_they_finish_quickly(monkeypatch):
    """The bound must not skip restart notification or redelivery on a fast path."""
    monkeypatch.setenv("HERMES_STARTUP_RESTORE_DRAIN_TIMEOUT", "2")

    runner, _adapter = make_restart_runner()
    runner._background_tasks = set()
    runner._send_restart_notification = AsyncMock(return_value=None)
    runner._claim_pending_obligations = AsyncMock(return_value=[])
    runner._redeliver_claimed_obligations = AsyncMock(return_value=0)

    await runner._await_startup_boot_sends(
        planned_restart_notification_pending=False,
    )

    runner._send_restart_notification.assert_awaited_once()
    runner._claim_pending_obligations.assert_awaited_once()
    runner._redeliver_claimed_obligations.assert_awaited_once()
