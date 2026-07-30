"""Tests for setting /goal from reply context in gateway platforms."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionEntry, SessionSource, build_session_key


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import goals

    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


class RecordingAdapter:
    def __init__(self) -> None:
        self._pending_messages: dict[str, MessageEvent] = {}


@pytest.fixture()
def runner():
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="group",
        thread_id="120",
    )
    session_entry = SessionEntry(
        session_key=build_session_key(source),
        session_id=f"goal-reply-session-{uuid.uuid4().hex[:8]}",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="group",
    )

    r = object.__new__(GatewayRunner)
    r.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
    )
    r.adapters = {Platform.TELEGRAM: RecordingAdapter()}
    r._queued_events = {}
    r.session_store = MagicMock()
    r.session_store.get_or_create_session.return_value = session_entry
    r.session_store._generate_session_key.return_value = session_entry.session_key
    return SimpleNamespace(runner=r, adapter=r.adapters[Platform.TELEGRAM], source=source, session=session_entry)


@pytest.mark.asyncio
async def test_bare_goal_reply_uses_replied_to_text_as_goal(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-2",
        reply_to_message_id="plan-1",
        reply_to_text="Implement the approved Project Storm plan with tests.",
    )

    response = await runner.runner._handle_goal_command(event)

    assert "Goal set" in response
    assert "Implement the approved Project Storm plan" in response

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert state is not None
    assert state.status == "active"
    assert state.goal == "Implement the approved Project Storm plan with tests."

    queued = runner.adapter._pending_messages[runner.session.session_key]
    assert queued.text.startswith("[Continuing toward your standing goal]\nGoal: ")
    assert state.goal in queued.text
    assert queued.source == runner.source


@pytest.mark.asyncio
async def test_bare_goal_without_reply_remains_status(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-1",
    )

    response = await runner.runner._handle_goal_command(event)

    assert "No active goal" in response
    assert runner.adapter._pending_messages == {}


@pytest.mark.asyncio
async def test_goal_status_reply_does_not_replace_goal(hermes_home, runner):
    from hermes_cli.goals import GoalManager

    GoalManager(runner.session.session_id).set("existing goal")
    event = MessageEvent(
        text="/goal status",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-3",
        reply_to_message_id="plan-1",
        reply_to_text="This text must not replace the active goal.",
    )

    response = await runner.runner._handle_goal_command(event)

    assert "existing goal" in response
    assert "This text must not replace" not in response
    assert GoalManager(runner.session.session_id).state.goal == "existing goal"


@pytest.mark.asyncio
async def test_goal_resume_queues_immediate_canonical_continuation(hermes_home, runner):
    """Regression: /goal resume must do work without another user message."""
    from hermes_cli.goals import GoalManager

    mgr = GoalManager(runner.session.session_id)
    mgr.set("finish the paused work")
    mgr.pause(reason="turn budget exhausted")
    event = MessageEvent(
        text="/goal resume",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-resume-1",
    )

    response = await runner.runner._handle_goal_command(event)

    state = GoalManager(runner.session.session_id).state
    assert state is not None
    assert state.status == "active"
    assert state.turns_used == 0
    assert "continuation queued immediately" in response.lower()
    queued = runner.adapter._pending_messages[runner.session.session_key]
    assert queued.text.startswith("[Continuing toward your standing goal]\nGoal: ")
    assert state.goal in queued.text
    assert queued.internal is True


@pytest.mark.asyncio
async def test_goal_resume_replaces_stale_continuations_without_duplication(hermes_home, runner):
    """Repeated resume is idempotent for queued synthetic continuation turns."""
    from hermes_cli.goals import CONTINUATION_PROMPT_TEMPLATE, GoalManager

    mgr = GoalManager(runner.session.session_id)
    mgr.set("finish exactly once")
    mgr.pause(reason="user-paused")
    stale = MessageEvent(
        text=CONTINUATION_PROMPT_TEMPLATE.format(goal="stale goal text"),
        message_type=MessageType.TEXT,
        source=runner.source,
        internal=True,
    )
    runner.adapter._pending_messages[runner.session.session_key] = stale
    event = MessageEvent(
        text="/goal resume",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-resume-2",
    )

    await runner.runner._handle_goal_command(event)
    await runner.runner._handle_goal_command(event)

    pending = runner.adapter._pending_messages[runner.session.session_key]
    overflow = runner.runner._queued_events.get(runner.session.session_key, [])
    all_events = [pending, *overflow]
    continuations = [e for e in all_events if runner.runner._is_goal_continuation_event(e)]
    assert len(continuations) == 1
    assert "finish exactly once" in continuations[0].text


@pytest.mark.asyncio
async def test_goal_resume_preserves_real_fifo_events(hermes_home, runner):
    """Replacing stale goal turns must never drop or reorder user messages."""
    from hermes_cli.goals import CONTINUATION_PROMPT_TEMPLATE, GoalManager

    mgr = GoalManager(runner.session.session_id)
    mgr.set("resume after user queue")
    mgr.pause(reason="user-paused")
    first_user = MessageEvent(
        text="first real user message",
        message_type=MessageType.TEXT,
        source=runner.source,
    )
    stale = MessageEvent(
        text=CONTINUATION_PROMPT_TEMPLATE.format(goal="stale"),
        message_type=MessageType.TEXT,
        source=runner.source,
        internal=True,
    )
    second_user = MessageEvent(
        text="second real user message",
        message_type=MessageType.TEXT,
        source=runner.source,
    )
    key = runner.session.session_key
    runner.adapter._pending_messages[key] = first_user
    runner.runner._queued_events[key] = [stale, second_user]

    response = await runner.runner._handle_goal_command(
        MessageEvent(
            text="/goal resume",
            message_type=MessageType.TEXT,
            source=runner.source,
            message_id="cmd-resume-fifo",
        )
    )

    assert "continuation queued immediately" in response.lower()
    pending = runner.adapter._pending_messages[key]
    overflow = runner.runner._queued_events[key]
    assert pending is first_user
    assert overflow[0] is second_user
    assert len(overflow) == 2
    assert runner.runner._is_goal_continuation_event(overflow[1])
    assert "resume after user queue" in overflow[1].text


@pytest.mark.asyncio
async def test_goal_resume_promotes_real_user_behind_stale_head(hermes_home, runner):
    """A real user event behind a stale goal head must run before resumed goal work."""
    from hermes_cli.goals import CONTINUATION_PROMPT_TEMPLATE, GoalManager

    mgr = GoalManager(runner.session.session_id)
    mgr.set("resume only after queued user")
    mgr.pause(reason="user-paused")
    key = runner.session.session_key
    stale = MessageEvent(
        text=CONTINUATION_PROMPT_TEMPLATE.format(goal="stale head"),
        message_type=MessageType.TEXT,
        source=runner.source,
        internal=True,
    )
    real_user = MessageEvent(
        text="accepted real user turn",
        message_type=MessageType.TEXT,
        source=runner.source,
    )
    runner.adapter._pending_messages[key] = stale
    runner.runner._queued_events[key] = [real_user]

    await runner.runner._handle_goal_command(
        MessageEvent(
            text="/goal resume",
            message_type=MessageType.TEXT,
            source=runner.source,
            message_id="cmd-resume-promote-user",
        )
    )

    assert runner.adapter._pending_messages[key] is real_user
    overflow = runner.runner._queued_events[key]
    assert len(overflow) == 1
    assert runner.runner._is_goal_continuation_event(overflow[0])
    assert "resume only after queued user" in overflow[0].text


@pytest.mark.asyncio
async def test_goal_resume_fails_closed_when_continuation_cannot_queue(hermes_home, runner):
    """Never claim success while leaving an active goal with no queued work."""
    from hermes_cli.goals import GoalManager

    mgr = GoalManager(runner.session.session_id)
    mgr.set("must not strand active")
    mgr.pause(reason="user-paused")
    runner.runner.adapters.pop(runner.source.platform)

    response = await runner.runner._handle_goal_command(
        MessageEvent(
            text="/goal resume",
            message_type=MessageType.TEXT,
            source=runner.source,
            message_id="cmd-resume-no-adapter",
        )
    )

    state = GoalManager(runner.session.session_id).state
    assert state is not None
    assert state.status == "paused"
    assert state.paused_reason == "resume continuation enqueue failed"
    assert "could not resume" in response.lower()


@pytest.mark.asyncio
async def test_bare_goal_reply_strips_supergoal_body_prefix_and_sets_high(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-4",
        reply_to_message_id="body-1",
        reply_to_text=(
            "SUPERGOAL_GOAL_BODY: Execute all phases from "
            "/tmp/project/.supergoal/ROADMAP.md and finish with SUPERGOAL_RUN_COMPLETE.\n\n"
            "Не стартовал за тебя. Сейчас только выдал файлы."
        ),
    )

    response = await runner.runner._handle_goal_command(event)

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert state is not None
    assert state.goal.startswith("Execute all phases")
    assert "SUPERGOAL_GOAL_BODY" not in state.goal
    assert "Не стартовал" not in state.goal
    assert "Goal set" in response
    override = runner.runner._session_reasoning_overrides[runner.session.session_key]
    assert override["effort"] == "xhigh"


@pytest.mark.asyncio
async def test_bare_goal_reply_extracts_supergoal_body_when_marker_is_inside_report(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-4a",
        reply_to_message_id="body-report-1",
        reply_to_text=(
            "Файлы готовы, ниже тело для запуска.\n\n"
            "SUPERGOAL_GOAL_BODY: From project root, execute `.supergoal/demo` "
            "and finish with SUPERGOAL_RUN_COMPLETE.\n\n"
            "Не стартовал за тебя. Сейчас только выдал файлы."
        ),
    )

    response = await runner.runner._handle_goal_command(event)

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert "Goal set" in response
    assert state is not None
    assert state.goal == "From project root, execute `.supergoal/demo` and finish with SUPERGOAL_RUN_COMPLETE."
    assert "Файлы готовы" not in state.goal
    assert "Не стартовал" not in state.goal


@pytest.mark.asyncio
async def test_bare_goal_reply_extracts_supergoal_body_from_replied_markdown_document_text(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-4doc",
        reply_to_message_id="roadmap-md-1",
        reply_to_text=(
            "[Content of replied document ROADMAP.md]:\n"
            "# Roadmap\n\n"
            "SUPERGOAL_GOAL_BODY: From project root, execute `.supergoal/demo` "
            "and finish with AUDIT_COMPLETE + SUPERGOAL_RUN_COMPLETE.\n\n"
            "## Artifacts\n"
            "Roadmap: `.supergoal/ROADMAP.md`"
        ),
    )

    response = await runner.runner._handle_goal_command(event)

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert "Goal set" in response
    assert state is not None
    assert state.goal == (
        "From project root, execute `.supergoal/demo` "
        "and finish with AUDIT_COMPLETE + SUPERGOAL_RUN_COMPLETE."
    )
    assert "Artifacts" not in state.goal


@pytest.mark.asyncio
async def test_bare_goal_reply_extracts_supergoal_body_from_launch_goal_markdown(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-4launch",
        reply_to_message_id="launch-md-1",
        reply_to_text=(
            "[Content of replied-to LAUNCH_GOAL.md]:\n"
            "# Human20 Auth Perfect -- SuperGoal launch\n\n"
            "SUPERGOAL_GOAL_BODY:\n"
            "Execute SuperGoal demo from /tmp/project using .supergoal/demo/PROTOCOL.md, "
            "ROADMAP.md, STATE.md, and phases/phase-0.md..phase-7.md. Run exactly one "
            "numbered phase per turn and finish with AUDIT_COMPLETE then SUPERGOAL_RUN_COMPLETE.\n\n"
            "DONE_CONDITION:\n"
            "Done when SUPERGOAL_RUN_COMPLETE appears in the transcript.\n\n"
            "OPERATOR_ACTION:\n"
            "Reply to this file in Telegram with exactly: /goal\n\n"
            "NOTES:\n"
            "- This file is a launch artifact only.\n"
            "- It does not autostart by being posted."
        ),
    )

    response = await runner.runner._handle_goal_command(event)

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert "Goal set" in response
    assert state is not None
    assert state.goal == (
        "Execute SuperGoal demo from /tmp/project using .supergoal/demo/PROTOCOL.md, "
        "ROADMAP.md, STATE.md, and phases/phase-0.md..phase-7.md. Run exactly one "
        "numbered phase per turn and finish with AUDIT_COMPLETE then SUPERGOAL_RUN_COMPLETE."
    )
    queued = runner.adapter._pending_messages[runner.session.session_key]
    assert queued.text.startswith("[Continuing toward your standing goal]\nGoal: ")
    assert state.goal in queued.text
    assert not queued.text.startswith(state.goal)
    assert "DONE_CONDITION" not in state.goal
    assert "OPERATOR_ACTION" not in state.goal
    assert "NOTES" not in state.goal
    assert runner.runner._session_reasoning_overrides[runner.session.session_key]["effort"] == "xhigh"


@pytest.mark.asyncio
async def test_goal_args_with_hydrated_launch_markdown_extracts_supergoal_body(hermes_home, runner):
    event = MessageEvent(
        text=(
            "/goal\n\n"
            "[Content of replied-to LAUNCH_GOAL.md]:\n"
            "# Human20 Auth Perfect -- SuperGoal launch\n\n"
            "SUPERGOAL_GOAL_BODY:\r\n"
            "Execute SuperGoal demo from /tmp/project using .supergoal/demo/PROTOCOL.md.\r\n"
            "DONE_CONDITION:\r\n"
            "Done when SUPERGOAL_RUN_COMPLETE appears.\r\n\r\n"
            "NOTES:\r\n"
            "- This file is a launch artifact only."
        ),
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-4launch-args",
        reply_to_message_id="launch-md-args-1",
    )

    response = await runner.runner._handle_goal_command(event)

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert "Goal set" in response
    assert state is not None
    assert state.goal == "Execute SuperGoal demo from /tmp/project using .supergoal/demo/PROTOCOL.md."
    assert "[Content of replied-to" not in state.goal
    assert "DONE_CONDITION" not in state.goal
    assert "NOTES" not in state.goal


def test_extract_supergoal_body_cuts_earliest_section_without_blank_line(runner):
    text = (
        "SUPERGOAL_GOAL_BODY:\n"
        "Run the thing.\n"
        "NOTES:\n"
        "This must be stripped.\n\n"
        "DONE_CONDITION:\n"
        "Too late."
    )

    body = runner.runner._extract_supergoal_body(text)

    assert body == "Run the thing."


def test_extract_supergoal_body_normalizes_crlf_and_strips_launch_tails(runner):
    text = "SUPERGOAL_GOAL_BODY:\r\nRun it.\r\n\r\nDONE_CONDITION:\r\nDone."

    body = runner.runner._extract_supergoal_body(text)

    assert body == "Run it."


def test_extract_supergoal_body_preserves_numbered_goal_body(runner):
    text = (
        "SUPERGOAL_GOAL_BODY:\n"
        "1. Start from `.supergoal/STATE.md`.\n"
        "2. Finish after SUPERGOAL_RUN_COMPLETE.\n\n"
        "1. Start now"
    )

    body = runner.runner._extract_supergoal_body(text)

    assert body == (
        "1. Start from `.supergoal/STATE.md`.\n"
        "2. Finish after SUPERGOAL_RUN_COMPLETE."
    )


@pytest.mark.asyncio
async def test_bare_goal_reply_to_supergoal_plan_extracts_artifact_goal(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-4b",
        reply_to_message_id="plan-1",
        reply_to_text=(
            "План Supergoal такой:\n\n"
            "MEDIA:/home/hermes/workspace/human20-app-prod/.supergoal/"
            "h20-auth-telegram-prod-perfect/ROADMAP.md\n"
            "MEDIA:/home/hermes/workspace/human20-app-prod/.supergoal/"
            "h20-auth-telegram-prod-perfect/THINKING.md\n\n"
            "Artifacts:\n"
            "  Progress: `.supergoal/h20-auth-telegram-prod-perfect/STATE.md`\n"
            "  Phase specs: `.supergoal/h20-auth-telegram-prod-perfect/phases/phase-0..7.md`\n"
        ),
    )

    response = await runner.runner._handle_goal_command(event)

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert "Goal set" in response
    assert state is not None
    assert state.goal.startswith("Execute the Supergoal from project root")
    assert "/home/hermes/workspace/human20-app-prod/.supergoal/h20-auth-telegram-prod-perfect/ROADMAP.md" in state.goal
    assert "План Supergoal" not in state.goal
    assert runner.runner._session_reasoning_overrides[runner.session.session_key]["effort"] == "xhigh"


@pytest.mark.asyncio
async def test_bare_goal_reply_to_relative_supergoal_state_extracts_artifact_goal(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-4b-relative",
        reply_to_message_id="plan-relative",
        reply_to_text="Progress: `.supergoal/STATE.md`\nRoadmap: `.supergoal/ROADMAP.md`",
    )

    response = await runner.runner._handle_goal_command(event)

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert "Goal set" in response
    assert state is not None
    assert state.goal.startswith("Execute the Supergoal from the project root.")
    assert "`.supergoal/PROTOCOL.md`" in state.goal
    assert "`.supergoal/STATE.md`" in state.goal


@pytest.mark.asyncio
async def test_bare_goal_reply_to_absolute_supergoal_state_extracts_artifact_goal(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-4b-absolute-state",
        reply_to_message_id="plan-absolute-state",
        reply_to_text="Progress: `/tmp/project/.supergoal/STATE.md`",
    )

    response = await runner.runner._handle_goal_command(event)

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert "Goal set" in response
    assert state is not None
    assert state.goal.startswith("Execute the Supergoal from project root `/tmp/project`.")
    assert "`/tmp/project/.supergoal/STATE.md`" in state.goal


def test_pasted_supergoal_body_plus_goal_status_does_not_autodispatch(runner):
    pasted = (
        "SUPERGOAL_GOAL_BODY: Discuss `.supergoal/demo` but do not start yet.\n\n"
        "/goal status"
    )

    assert runner.runner._goal_text_from_pasted_supergoal_handoff(pasted) == ""


@pytest.mark.asyncio
async def test_bare_goal_reply_to_long_non_supergoal_text_is_goal(hermes_home, runner):
    long_goal = "Обычный длинный goal body без supergoal marker. " * 80
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-4c",
        reply_to_message_id="report-1",
        reply_to_text=long_goal,
    )

    response = await runner.runner._handle_goal_command(event)

    from hermes_cli.goals import GoalManager

    state = GoalManager(runner.session.session_id).state
    assert "Goal set" in response
    assert state is not None
    assert state.status == "active"
    assert state.goal == long_goal.strip()
    queued = runner.adapter._pending_messages[runner.session.session_key]
    assert state.goal in queued.text


@pytest.mark.asyncio
async def test_bare_goal_reply_to_status_line_is_not_reused_as_goal(hermes_home, runner):
    event = MessageEvent(
        text="/goal",
        message_type=MessageType.TEXT,
        source=runner.source,
        message_id="cmd-5",
        reply_to_message_id="status-1",
        reply_to_text='✓ Goal done (1/20 turns): "Execute all phases"',
    )

    response = await runner.runner._handle_goal_command(event)

    assert "No active goal" in response
    assert runner.adapter._pending_messages == {}


def test_pasted_supergoal_handoff_extracts_body_without_followup_text(runner):
    pasted = (
        "Кнопки вот — ты нажал Start now\n\n"
        "SUPERGOAL_GOAL_BODY: From the project root, run `.supergoal/demo` "
        "and finish with SUPERGOAL_RUN_COMPLETE.\n\n"
        "Теперь reply на это сообщение ровно:\n\n"
        "/goal\n\n"
        "Не копируй длинный текст."
    )

    body = runner.runner._goal_text_from_pasted_supergoal_handoff(pasted)

    assert body == (
        "From the project root, run `.supergoal/demo` "
        "and finish with SUPERGOAL_RUN_COMPLETE."
    )
    assert runner.runner._is_supergoal_dispatch(body)


def test_pasted_supergoal_handoff_requires_goal_line(runner):
    pasted = "SUPERGOAL_GOAL_BODY: Discuss `.supergoal/demo` but do not start yet."

    assert runner.runner._goal_text_from_pasted_supergoal_handoff(pasted) == ""


@pytest.mark.asyncio
async def test_assistant_supergoal_body_without_autodispatch_sentinel_does_not_start_goal(hermes_home, runner):
    session_entry = runner.session
    response = (
        "Pre-flight green.\n\n"
        "SUPERGOAL_GOAL_BODY: From project root, execute `.supergoal/demo` "
        "and finish with SUPERGOAL_RUN_COMPLETE.\n\n"
        "This should start automatically after Start now."
    )

    did_dispatch = await runner.runner._auto_dispatch_supergoal_from_response(
        session_entry=session_entry,
        source=runner.source,
        final_response=response,
    )

    from hermes_cli.goals import GoalManager

    state = GoalManager(session_entry.session_id).state
    assert did_dispatch is False
    assert state is None
    assert runner.adapter._pending_messages == {}


@pytest.mark.asyncio
async def test_assistant_supergoal_body_with_autodispatch_sentinel_starts_goal(hermes_home, runner):
    session_entry = runner.session
    response = (
        "SUPERGOAL_AUTODISPATCH: true\n\n"
        "SUPERGOAL_GOAL_BODY: From project root, execute `.supergoal/demo` "
        "and finish with SUPERGOAL_RUN_COMPLETE."
    )

    did_dispatch = await runner.runner._auto_dispatch_supergoal_from_response(
        session_entry=session_entry,
        source=runner.source,
        final_response=response,
    )

    from hermes_cli.goals import GoalManager

    state = GoalManager(session_entry.session_id).state
    assert did_dispatch is True
    assert state is not None
    assert state.status == "active"
    assert state.goal.startswith("From project root")
    assert "SUPERGOAL_GOAL_BODY" not in state.goal
    queued = runner.adapter._pending_messages[session_entry.session_key]
    assert queued.text.startswith("[Continuing toward your standing goal]\nGoal: ")
    assert state.goal in queued.text
    assert not queued.text.startswith(state.goal)
    assert runner.runner._session_reasoning_overrides[session_entry.session_key]["effort"] == "xhigh"
