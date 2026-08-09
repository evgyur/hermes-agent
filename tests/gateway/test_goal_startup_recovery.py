"""Startup recovery tests for active /goal sessions.

Phase 4 implements the target behavior mapped in Phase 1:

- active GoalManager state is enough to recover a fresh standing goal at
  gateway startup, even when legacy generic startup auto-resume is disabled;
- recovery runs on the same session key/session id through the official
  GoalManager continuation prompt;
- paused/done/cleared goals fail closed;
- side-effectful uncheckpointed tool tails still auto-resume active goals,
  with an explicit recovery note telling the agent to inspect state before
  repeating side effects.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.session import SessionEntry
from hermes_cli.goals import GoalManager
from hermes_state import AsyncSessionDB
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    from gateway import restart_loop_guard
    from hermes_cli import goals

    restart_state = home / "gateway" / "restart_loop.json"
    monkeypatch.setattr(restart_loop_guard, "_state_path", lambda: restart_state)
    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


def _goal_entry(*, session_id="goal-sid", status="active", resume_pending=True):
    source = make_restart_source(chat_id="goal-chat")
    now = datetime.now()
    return SessionEntry(
        session_key="agent:main:telegram:dm:goal-chat",
        session_id=session_id,
        created_at=now,
        updated_at=now,
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=resume_pending,
        resume_reason="restart_timeout" if resume_pending else None,
        last_resume_marked_at=now if resume_pending else None,
    )


@pytest.mark.asyncio
async def test_active_goal_resume_pending_auto_resumes_without_global_flag(hermes_home):
    runner, adapter = make_restart_runner()
    entry = _goal_entry(session_id="active-goal-sid")
    runner.session_store._entries = {entry.session_key: entry}
    adapter.handle_message = AsyncMock()
    GoalManager(session_id=entry.session_id).set("continue this active goal after restart")

    with patch.dict("os.environ", {}, clear=True):
        scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 1
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.internal is True
    assert event.source == entry.origin
    assert event.text.startswith("[Continuing toward your standing goal]\nGoal:")
    assert "continue this active goal after restart" in event.text
    assert entry.session_id == "active-goal-sid"
    assert GoalManager(session_id=entry.session_id).is_active()


@pytest.mark.asyncio
async def test_legacy_generic_resume_still_requires_global_flag_when_no_goal(hermes_home):
    runner, adapter = make_restart_runner()
    entry = _goal_entry(session_id="same-session-id")
    runner.session_store._entries = {entry.session_key: entry}
    adapter.handle_message = AsyncMock()

    with patch.dict("os.environ", {"HERMES_GATEWAY_STARTUP_AUTO_RESUME": "1"}, clear=True):
        scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 1
    assert entry.session_id == "same-session-id"
    event = adapter.handle_message.await_args.args[0]
    assert event.internal is True
    assert event.source == entry.origin
    assert event.text == ""


@pytest.mark.asyncio
async def test_legacy_generic_resume_waits_without_flag_when_no_goal(hermes_home):
    runner, adapter = make_restart_runner()
    entry = _goal_entry(session_id="generic-no-flag-sid")
    runner.session_store._entries = {entry.session_key: entry}
    adapter.handle_message = AsyncMock()

    with patch.dict("os.environ", {}, clear=True):
        scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 0
    adapter.handle_message.assert_not_called()
    assert entry.resume_pending is True


@pytest.mark.asyncio
async def test_business_owner_dm_resume_pending_auto_resumes_without_global_flag(hermes_home):
    runner, adapter = make_restart_runner()
    now = datetime.now()
    source = make_restart_source(chat_id="customer-chat")
    source.user_id = "owner-id"
    entry = SessionEntry(
        session_key="agent:main:telegram:dm:customer-chat",
        session_id="business-owner-restart-sid",
        created_at=now,
        updated_at=now,
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=now,
    )
    runner.config.platforms[Platform.TELEGRAM].extra["business"] = {
        "enabled": True,
        "ignore_user_ids": ["owner-id"],
    }
    runner.session_store._entries = {entry.session_key: entry}
    adapter.handle_message = AsyncMock()

    with patch.dict("os.environ", {}, clear=True):
        scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 1
    event = adapter.handle_message.await_args.args[0]
    assert event.internal is True
    assert event.source == source
    assert event.text == ""


def test_customer_dm_is_not_misclassified_as_business_owner_dm(hermes_home):
    runner, _adapter = make_restart_runner()
    source = make_restart_source(chat_id="customer-id")
    source.user_id = "customer-id"
    runner.config.platforms[Platform.TELEGRAM].extra["business"] = {
        "enabled": True,
        "ignore_user_ids": ["owner-id"],
    }

    assert runner._startup_recovery_source_is_business_owner_dm(source) is False


@pytest.mark.asyncio
async def test_paused_goal_resume_pending_does_not_auto_resume_even_when_generic_flag_enabled(hermes_home):
    runner, adapter = make_restart_runner()
    entry = _goal_entry(session_id="paused-goal-sid")
    runner.session_store._entries = {entry.session_key: entry}
    adapter.handle_message = AsyncMock()
    mgr = GoalManager(session_id=entry.session_id)
    mgr.set("do not resume while paused")
    mgr.pause("user-paused")

    with patch.dict("os.environ", {"HERMES_GATEWAY_STARTUP_AUTO_RESUME": "1"}, clear=True):
        scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 0
    adapter.handle_message.assert_not_called()


def test_active_goal_without_resume_pending_uses_goalmanager_classifier(hermes_home):
    runner, _adapter = make_restart_runner()
    entry = _goal_entry(session_id="ledger-needed-sid", resume_pending=False)
    runner.session_store._entries = {entry.session_key: entry}
    GoalManager(session_id=entry.session_id).set("recover from a lost queued continuation")

    decision = runner._classify_startup_goal_recovery(entry)

    assert decision.status == "auto_resume"
    assert decision.session_id == entry.session_id
    assert decision.reason == "active-goal-startup-recovery"
    assert decision.prompt.startswith("[Continuing toward your standing goal]\nGoal:")
    assert "recover from a lost queued continuation" in decision.prompt


@pytest.mark.asyncio
async def test_active_goal_without_resume_pending_does_not_count_as_restart_boot(hermes_home):
    runner, adapter = make_restart_runner()
    entry = _goal_entry(session_id="goal-only-recovery-sid", resume_pending=False)
    runner.session_store._entries = {entry.session_key: entry}
    adapter.handle_message = AsyncMock()
    GoalManager(session_id=entry.session_id).set("continue durable goal after clean boot")

    with patch("gateway.restart_loop_guard.check_and_record") as guard:
        scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 1
    guard.assert_not_called()


def test_completed_supergoal_state_is_not_auto_resumed_at_startup(hermes_home, tmp_path):
    runner, _adapter = make_restart_runner()
    entry = _goal_entry(session_id="completed-supergoal-sid", resume_pending=False)
    runner.session_store._entries = {entry.session_key: entry}
    root = tmp_path / "done-root"
    sg = root / ".supergoal"
    sg.mkdir(parents=True)
    (sg / "STATE.md").write_text(
        "# STATE\n"
        "Current phase: DONE\n"
        "Status snapshot: DONE — all phases and final audit complete\n"
        "AUDIT_COMPLETE\n"
        "SUPERGOAL_RUN_COMPLETE\n",
        encoding="utf-8",
    )
    GoalManager(session_id=entry.session_id).set(
        f"From `{root}`, execute `{root}/.supergoal/STATE.md`; "
        "finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
    )

    decision = runner._classify_startup_goal_recovery(entry)

    assert decision.status == "skip"
    assert decision.reason == "goal-not-active"
    assert decision.goal_status == "done"
    state = GoalManager(session_id=entry.session_id).state
    assert state is not None
    assert state.status == "done"


def test_private_telegram_group_generic_resume_does_not_need_global_flag(hermes_home):
    runner, _adapter = make_restart_runner()
    source = make_restart_source(chat_id="-100private", chat_type="group", thread_id="1858")
    now = datetime.now()
    entry = SessionEntry(
        session_key="agent:main:telegram:group:-100private:1858",
        session_id="private-group-generic-sid",
        created_at=now,
        updated_at=now,
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="group",
        resume_pending=True,
        resume_reason="shutdown_timeout",
        last_resume_marked_at=now,
    )

    with patch.dict("os.environ", {"TELEGRAM_PRIVATE_CHATS": "-100private"}, clear=True):
        decision = runner._classify_startup_goal_recovery(entry)

    assert decision.status == "auto_resume"
    assert decision.reason == "generic-resume-pending"


def test_active_goal_private_group_uses_config_private_chats_without_env(hermes_home):
    runner, _adapter = make_restart_runner()
    runner.config.platforms[Platform.TELEGRAM] = PlatformConfig(
        enabled=True,
        token="***",
        extra={"private_chats": ["-100private"]},
    )
    source = make_restart_source(chat_id="-100private", chat_type="group", thread_id="1858")
    now = datetime.now()
    entry = SessionEntry(
        session_key="agent:main:telegram:group:-100private:1858",
        session_id="private-group-active-goal-sid",
        created_at=now,
        updated_at=now,
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="group",
        resume_pending=False,
        last_resume_marked_at=now,
    )
    GoalManager(session_id=entry.session_id).set("continue private workroom goal")

    with patch.dict("os.environ", {}, clear=True):
        decision = runner._classify_startup_goal_recovery(entry)

    assert decision.status == "auto_resume"
    assert decision.reason == "active-goal-startup-recovery"
    assert "continue private workroom goal" in decision.prompt


class _UnsafeChipHistoryResult:
    records_checked = 10

    @property
    def missed_by_gateway(self):
        return [type("C", (), {"status": "missed_by_gateway"})()]

    @property
    def requeue_candidates(self):
        return []

    @property
    def alert_only(self):
        return []


def test_active_goal_dm_skips_telegram_chip_history_id_mismatch(hermes_home, monkeypatch):
    runner, _adapter = make_restart_runner()
    source = make_restart_source(chat_id="617744661", chat_type="dm")
    now = datetime.now()
    entry = SessionEntry(
        session_key="agent:main:telegram:dm:617744661",
        session_id="chip-dm-active-goal-sid",
        created_at=now,
        updated_at=now,
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=False,
        last_resume_marked_at=now,
    )
    GoalManager(session_id=entry.session_id).set("continue DM goal despite telegram-chip bot/user id mismatch")

    def _should_not_run(_entry):
        raise AssertionError("telegram-chip reconciliation must not run for Telegram DMs")

    monkeypatch.setattr(runner, "_startup_chip_history_reconciliation", _should_not_run)

    decision = runner._classify_startup_goal_recovery(entry)

    assert decision.status == "auto_resume"
    assert decision.reason == "active-goal-startup-recovery"
    assert "continue DM goal" in decision.prompt


def test_active_goal_startup_checks_chip_history_before_auto_resume(hermes_home, monkeypatch):
    runner, _adapter = make_restart_runner()
    source = make_restart_source(chat_id="-100private", chat_type="group", thread_id="1858")
    now = datetime.now()
    entry = SessionEntry(
        session_key="agent:main:telegram:group:-100private:1858",
        session_id="chip-history-active-goal-sid",
        created_at=now,
        updated_at=now,
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="group",
        resume_pending=False,
        last_resume_marked_at=now,
    )
    GoalManager(session_id=entry.session_id).set("continue after checking recent visible messages")
    monkeypatch.setenv("TELEGRAM_PRIVATE_CHATS", "-100private")
    monkeypatch.setattr(runner, "_startup_chip_history_reconciliation", lambda _entry: _UnsafeChipHistoryResult())

    decision = runner._classify_startup_goal_recovery(entry)

    assert decision.status == "alert_only"
    assert "telegram-chip recent history requires operator review" in decision.reason
    assert "missed_by_gateway" in decision.reason


@pytest.mark.asyncio
async def test_alert_only_startup_recovery_notifies_private_chat_without_crashing(hermes_home):
    runner, adapter = make_restart_runner()
    entry = _goal_entry(session_id="already-running-goal-sid", resume_pending=False)
    runner.session_store._entries = {entry.session_key: entry}
    runner._running_agents[entry.session_key] = object()
    GoalManager(session_id=entry.session_id).set("keep the active goal safe")

    scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 0
    assert len(adapter.sent) == 1
    assert "auto-resume was withheld" in adapter.sent[0]
    assert "agent-already-running" in adapter.sent[0]

    # Re-running the startup scan must not send duplicate alerts.
    scheduled_again = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled_again == 0
    assert len(adapter.sent) == 1


def test_public_telegram_group_generic_resume_still_waits_without_global_flag(hermes_home):
    runner, _adapter = make_restart_runner()
    source = make_restart_source(chat_id="-100public", chat_type="group", thread_id="1858")
    now = datetime.now()
    entry = SessionEntry(
        session_key="agent:main:telegram:group:-100public:1858",
        session_id="public-group-generic-sid",
        created_at=now,
        updated_at=now,
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="group",
        resume_pending=True,
        resume_reason="shutdown_timeout",
        last_resume_marked_at=now,
    )

    with patch.dict("os.environ", {"TELEGRAM_PUBLIC_CHATS": "-100public"}, clear=True):
        decision = runner._classify_startup_goal_recovery(entry)

    assert decision.status == "skip"
    assert decision.reason == "generic-auto-resume-disabled"


class _RiskyToolTailDB:
    def list_gateway_message_ledger_for_session(self, _session_key, *, limit=20):
        return [{"status": "drained"}]

    def get_messages(self, _session_id):
        return [
            {"role": "user", "content": "run a deploy"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"function": {"name": "terminal"}}],
            },
        ]


def test_startup_goal_recovery_unwraps_async_session_db(hermes_home):
    runner, _adapter = make_restart_runner()
    entry = _goal_entry(session_id="async-db-goal-sid")
    runner._session_db = AsyncSessionDB(_RiskyToolTailDB())
    GoalManager(session_id=entry.session_id).set("recover through async DB facade")

    decision = runner._classify_startup_goal_recovery(entry)

    assert decision.status == "auto_resume"
    assert decision.reason == "active-goal-startup-recovery-with-open-tool-tail"
    assert "terminal" in decision.prompt


@pytest.mark.asyncio
async def test_uncheckpointed_side_effectful_tool_tail_still_auto_resumes_goal(hermes_home):
    runner, adapter = make_restart_runner()
    entry = _goal_entry(session_id="risky-tail-sid")
    runner.session_store._entries = {entry.session_key: entry}
    runner._session_db = _RiskyToolTailDB()
    adapter.handle_message = AsyncMock()
    GoalManager(session_id=entry.session_id).set("recover safely without duplicating deploy")

    with patch.dict("os.environ", {}, clear=True):
        scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 1
    adapter.handle_message.assert_awaited_once()
    assert not adapter.sent
    event = adapter.handle_message.await_args.args[0]
    assert event.internal is True
    assert "recover safely without duplicating deploy" in event.text
    assert "Startup recovery note" in event.text
    assert "uncheckpointed assistant tool call(s): terminal" in event.text
    decision = runner._classify_startup_goal_recovery(entry)
    assert decision.status == "auto_resume"
    assert decision.reason == "active-goal-startup-recovery-with-open-tool-tail"
    assert "terminal" in decision.prompt
