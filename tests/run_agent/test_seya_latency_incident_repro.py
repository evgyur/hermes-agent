"""Deterministic, private-content-free reproduction of the Seya latency incident.

These tests pin the independent failure classes observed in the 2026-08-23
turn. Unrepaired contracts remain strict xfails; repaired contracts are normal
regressions so either kind of drift fails loudly.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from agent import tool_executor
from agent.tool_guardrails import ToolCallGuardrailController, ToolCallGuardrailConfig
from hermes_state import SessionDB
from tools import delegate_tool


class _NonConvergingChild:
    """Child that acknowledges cancellation but cannot unwind immediately."""

    def __init__(self):
        self._subagent_id = None
        self._delegate_saved_tool_names = []
        self._delegate_role = "leaf"
        self._credential_pool = None
        self.worker_started = threading.Event()
        self.worker_release = threading.Event()
        self.worker_done = threading.Event()
        self.close_called = threading.Event()
        self.close_saw_worker_done = None

    def run_conversation(self, **_kwargs):
        self.worker_started.set()
        self.worker_release.wait(timeout=5)
        self.worker_done.set()
        return {"final_response": "late", "completed": True, "api_calls": 1}

    def get_activity_summary(self):
        return {"api_call_count": 1, "max_iterations": 1, "current_tool": "synthetic"}

    def close(self):
        self.close_saw_worker_done = self.worker_done.is_set()
        self.close_called.set()


def test_timed_out_child_resources_close_only_after_worker_converges(monkeypatch):
    """The owner must not close a SessionDB while its child can still write."""
    child = _NonConvergingChild()
    parent = MagicMock()
    parent._touch_activity = MagicMock()
    parent._current_task_id = None
    parent._active_children = [child]

    monkeypatch.setattr(delegate_tool, "_get_child_timeout", lambda: 0.05)
    def _interrupt(target):
        target.worker_release.set()
        return True

    monkeypatch.setattr(delegate_tool, "request_hard_interrupt", _interrupt)

    try:
        result = delegate_tool._run_single_child(
            task_index=0,
            goal="synthetic non-converging child",
            child=child,
            parent_agent=parent,
        )
        assert child.worker_started.is_set()
        assert result["status"] == "timeout"
        assert child.close_called.is_set()
        assert child.close_saw_worker_done is True
    finally:
        child.worker_release.set()
        child.worker_done.wait(timeout=2)


def test_nonconverging_timed_out_child_defers_resource_close(monkeypatch):
    """An uncooperative worker keeps its resources until its own terminal edge."""
    child = _NonConvergingChild()
    parent = MagicMock()
    parent._touch_activity = MagicMock()
    parent._current_task_id = None
    parent._active_children = [child]

    monkeypatch.setattr(delegate_tool, "_get_child_timeout", lambda: 0.05)
    monkeypatch.setattr(delegate_tool, "_CHILD_CANCEL_CONVERGENCE_GRACE_S", 0.05)
    monkeypatch.setattr(delegate_tool, "request_hard_interrupt", lambda _child: True)

    result = delegate_tool._run_single_child(
        task_index=0,
        goal="synthetic uncooperative child",
        child=child,
        parent_agent=parent,
    )
    assert result["status"] == "timeout"
    assert not child.close_called.is_set()
    assert child in parent._active_children

    child.worker_release.set()
    assert child.worker_done.wait(timeout=2)
    assert child.close_called.wait(timeout=2)
    assert child.close_saw_worker_done is True
    assert child not in parent._active_children


def test_closed_session_writer_fails_with_typed_lifecycle_error(tmp_path):
    """Cancellation/close must never surface an internal NoneType.execute."""
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="synthetic-parent", source="test", model="test/model")
    db.close()

    with pytest.raises(RuntimeError, match="(?i)session.*closed"):
        db.append_messages_batch(
            "synthetic-parent",
            [{"role": "tool", "content": "synthetic result", "tool_call_id": "call-1"}],
        )


def test_active_append_and_close_serialize_for_100_iterations(tmp_path):
    """Close may follow an append, but cannot invalidate its live connection."""
    for iteration in range(100):
        path = tmp_path / f"race-{iteration}.db"
        db = SessionDB(db_path=path)
        session_id = f"synthetic-{iteration}"
        db.create_session(session_id=session_id, source="test", model="test/model")

        writer_entered = threading.Event()
        writer_release = threading.Event()
        writer_errors = []
        original_insert = db._insert_message_rows

        def _blocked_insert(conn, target_session_id, messages):
            writer_entered.set()
            assert writer_release.wait(timeout=2)
            return original_insert(conn, target_session_id, messages)

        db._insert_message_rows = _blocked_insert

        def _append():
            try:
                db.append_messages_batch(
                    session_id,
                    [{"role": "assistant", "content": "synthetic terminal"}],
                )
            except BaseException as exc:
                writer_errors.append(exc)

        writer = threading.Thread(target=_append)
        closer = threading.Thread(target=db.close)
        writer.start()
        assert writer_entered.wait(timeout=2)
        closer.start()
        time.sleep(0.001)
        assert closer.is_alive(), "close must wait behind the active write lock"
        writer_release.set()
        writer.join(timeout=2)
        closer.join(timeout=2)

        assert not writer.is_alive()
        assert not closer.is_alive()
        assert writer_errors == []
        with SessionDB(db_path=path) as readback:
            rows = readback.get_messages(session_id)
        assert [row["content"] for row in rows] == ["synthetic terminal"]


def test_child_deadline_is_strictly_inside_parent_tool_deadline(monkeypatch):
    """Reproduce the production 420-second parent / 600-second child inversion."""
    monkeypatch.setattr(delegate_tool, "_load_config", lambda: {"child_timeout_seconds": 600})
    monkeypatch.setattr(
        "agent.deadline._timeouts_section",
        lambda: {"tools": {"concurrent_batch": 420}},
    )
    monkeypatch.delenv("HERMES_CONCURRENT_TOOL_TIMEOUT_S", raising=False)
    monkeypatch.delenv("DELEGATION_CHILD_TIMEOUT_SECONDS", raising=False)

    parent_timeout = tool_executor._resolve_concurrent_tool_timeout()
    child_timeout = delegate_tool._get_effective_child_timeout()

    assert parent_timeout is not None
    assert child_timeout is not None
    assert child_timeout < parent_timeout


def test_degraded_provider_chain_stops_after_three_external_failures():
    """Different tool surfaces must not reset one provider failure storm."""
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            exact_failure_block_after=2,
            same_tool_failure_halt_after=2,
        )
    )
    chain = [
        ("web_search", {"query": "synthetic-a"}, "Error: provider timeout"),
        ("web_search", {"query": "synthetic-b"}, "Error: ungrounded response"),
        ("web_extract", {"url": "https://invalid.example"}, "Error: capability unavailable"),
        ("browser_navigate", {"url": "https://invalid.example"}, "Error: provider unavailable"),
    ]

    executed = 0
    for tool_name, args, result in chain:
        before = controller.before_call(tool_name, args)
        if before.action in {"block", "halt"}:
            break
        executed += 1
        controller.after_call(tool_name, args, result, failed=True)

    assert executed <= 3


def test_same_external_failure_signature_opens_after_two_attempts():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(hard_stop_enabled=True)
    )
    args = {"query": "synthetic"}
    for _ in range(2):
        assert controller.before_call("web_search", args).allows_execution
        controller.after_call(
            "web_search", args, "Error: provider timeout", failed=True
        )
    assert controller.before_call("web_search", args).code == "external_provider_breaker_open"


def test_later_success_does_not_reopen_external_provider_breaker():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(hard_stop_enabled=True)
    )
    controller.after_call(
        "web_search", {"query": "a"}, "Error: timeout", failed=True
    )
    controller.after_call(
        "web_search", {"query": "b"}, "Error: timeout", failed=True
    )
    controller.after_call(
        "web_search", {"query": "c"}, '{"success": true}', failed=False
    )
    assert controller.before_call("web_extract", {"url": "https://invalid.example"}).action == "block"


def test_successful_rescue_is_single_use_for_the_turn():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(hard_stop_enabled=True)
    )
    controller.after_call(
        "web_search",
        {"query": "synthetic"},
        '{"success":true,"data":{"rescued_from":"exa","web":[]}}',
        failed=False,
    )
    assert controller.before_call("web_search", {"query": "again"}).action == "block"
