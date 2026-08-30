import sys
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from agent.turn_finalizer import finalize_turn


@pytest.fixture(autouse=True)
def _initialized_parent_barrier_storage(monkeypatch, tmp_path):
    from tools import parent_task_barrier

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    parent_task_barrier.initialize_storage()


class FakeAgent:
    def __init__(self):
        self.max_iterations = 90
        self.iteration_budget = SimpleNamespace(remaining=10, used=1, max_total=90)
        self.quiet_mode = True
        self.model = "test-model"
        self.provider = "test-provider"
        self.base_url = ""
        self.session_id = "sess-test"
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0)
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_cache_read_tokens = 0
        self.session_cache_write_tokens = 0
        self.session_reasoning_tokens = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self.session_estimated_cost_usd = 0
        self.session_cost_status = "unknown"
        self.session_cost_source = "test"
        self._tool_guardrail_halt_decision = None
        self._interrupt_message = None
        self._response_was_previewed = True
        self._skill_nudge_interval = 0
        self._iters_since_skill = 0
        self.valid_tool_names = []
        self.persisted_messages: list[dict[str, Any]] | None = None
        self._persist_user_message_idx: int | None = None
        self._persist_user_message_override: Any = None
        self._persist_user_message_timestamp: float | None = None

    def _handle_max_iterations(self, messages, api_call_count):
        raise AssertionError("not expected")

    def _emit_status(self, *_args, **_kwargs):
        pass

    def _safe_print(self, *_args, **_kwargs):
        pass

    def _save_trajectory(self, *_args, **_kwargs):
        pass

    def _cleanup_task_resources(self, *_args, **_kwargs):
        pass

    def _drop_trailing_empty_response_scaffolding(self, messages):
        pass

    def _persist_session(self, messages, conversation_history):
        # Capture the durable write before finalization restores API-local
        # guidance to the returned/live transcript.
        self.persisted_messages = [dict(message) for message in messages]

    def _apply_persist_user_message_override(self, messages):
        idx = self._persist_user_message_idx
        override = self._persist_user_message_override
        if idx is not None and override is not None:
            messages[idx]["content"] = override

    def _file_mutation_verifier_enabled(self):
        return False

    def _turn_completion_explainer_enabled(self):
        return False

    def _drain_pending_steer(self):
        return None

    def clear_interrupt(self):
        pass

    def _sync_external_memory_for_turn(self, **_kwargs):
        pass






def test_final_response_closes_tool_tail_before_persistence(monkeypatch):
    """A recovered/previewed final response must be durable in session history.

    Regression for turns where the caller receives a non-empty final_response,
    but the message transcript still ends at a tool result. If persisted that
    way, the next turn reloads a stale/malformed history and can appear to loop
    because the assistant's visible final answer is missing from durable state.
    """
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "do it"},
        {
            "role": "assistant",
            "content": "I'll check.",
            "tool_calls": [
                {"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "terminal", "content": "ok"},
    ]

    result = finalize_turn(
        agent,
        final_response="Done.",
        api_call_count=2,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="do it",
        original_user_message="do it",
        _should_review_memory=False,
        _turn_exit_reason="fallback_prior_turn_content",
    )

    assert result["messages"][-1]["role"] == "assistant"
    assert result["messages"][-1]["content"] == "Done."
    assert isinstance(result["messages"][-1]["timestamp"], float)
    assert agent.persisted_messages is not None
    assert agent.persisted_messages[-1] == result["messages"][-1]


def test_fallback_timestamp_survives_delayed_sqlite_persistence(
    monkeypatch, tmp_path
):
    """The durable row records message creation, not the later DB flush."""
    from hermes_state import SessionDB

    created_at = 1_781_976_577.25
    persisted_at = created_at + 600
    monkeypatch.setattr("agent.message_metadata.wall_time", lambda: created_at)
    monkeypatch.setattr("hermes_state.time.time", lambda: persisted_at)
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("sess-test", source="cli")
    agent = FakeAgent()

    def persist_to_sqlite(messages, _conversation_history):
        db.replace_messages(agent.session_id, messages)
        agent.persisted_messages = db.get_messages_as_conversation(agent.session_id)

    agent._persist_session = persist_to_sqlite
    messages = [
        {"role": "user", "content": "do it", "timestamp": created_at - 1},
        {"role": "tool", "content": "ok", "tool_call_id": "call-1"},
    ]

    finalize_turn(
        agent,
        final_response="Done.",
        api_call_count=2,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="do it",
        original_user_message="do it",
        _should_review_memory=False,
        _turn_exit_reason="fallback_prior_turn_content",
    )

    assert agent.persisted_messages[-1]["timestamp"] == created_at
    assert agent.persisted_messages[-1]["timestamp"] != persisted_at


def test_final_response_fills_pure_tool_call_tail(monkeypatch):
    """A tail assistant row that is a *pure tool-call turn* carries no answer.

    The role check alone ("tail is assistant ⇒ nothing to do") leaves the
    #43849/#44100 invariant unmet when the tail is ``assistant(tool_calls)``
    with no text of its own: the caller and the gateway already delivered
    ``final_response``, but it never reaches the transcript. The next turn then
    replays the user backlog and the model re-answers it — the exact symptom
    that block exists to prevent.
    """
    agent = FakeAgent()
    messages = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "t1", "type": "function",
                 "function": {"name": "f", "arguments": "{}"}}
            ],
        },
    ]

    result = finalize_turn(
        agent,
        final_response="Here is your answer.",
        api_call_count=3,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="t",
        turn_id="tid",
        user_message="q",
        original_user_message="q",
        _should_review_memory=False,
        _turn_exit_reason="text_response(final)",
    )

    persisted = agent.persisted_messages
    assert any(
        m.get("role") == "assistant" and m.get("content") == result["final_response"]
        for m in persisted
    ), "delivered final_response never reached the durable transcript"
    # Filled in place — no assistant→assistant pair, tool_calls preserved.
    assert persisted[-1]["content"] == "Here is your answer."
    assert persisted[-1]["tool_calls"]
    assert sum(1 for m in persisted if m.get("role") == "assistant") == 1






def test_final_response_fill_invalidates_flush_scan_cursor():
    """The fill's marker pop must invalidate the bounded flush-scan cursor.

    The cursor (run_agent.py) skips the identity-matched prefix of its
    previous snapshot assuming no live dict loses ``_db_persisted`` in place
    — the fill is the one path that pops it. Without invalidation, the
    turn-end flush skips the filled row as 'already stamped' and the
    delivered answer never reaches state.db (the #43849 class resurfacing).
    """
    agent = FakeAgent()
    agent._db_flush_scan_prefix = ["prior-snapshot"]
    messages = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "t1", "type": "function",
                 "function": {"name": "f", "arguments": "{}"}}
            ],
            "_db_persisted": True,
        },
    ]

    finalize_turn(
        agent,
        final_response="Here is your answer.",
        api_call_count=3,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="t",
        turn_id="tid",
        user_message="q",
        original_user_message="q",
        _should_review_memory=False,
        _turn_exit_reason="text_response(final)",
    )

    assert agent._db_flush_scan_prefix is None


def test_required_background_child_withholds_initial_parent_answer(monkeypatch):
    """A required child makes the root turn provisional before persistence."""

    barrier = ModuleType("tools.parent_task_barrier")
    setattr(
        barrier,
        "finalization_policy",
        lambda **_kwargs: {
            "action": "withhold",
            "barrier_id": "barrier-1",
            "defer_goal_evaluation": True,
        },
    )
    setattr(barrier, "mark_initial_persisted", lambda _barrier_id: True)
    monkeypatch.setitem(sys.modules, "tools.parent_task_barrier", barrier)
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    monkeypatch.setattr(
        "hermes_cli.lifecycle.invoke_hook",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("provisional answer reached lifecycle hook")
        ),
    )
    monkeypatch.setattr(
        "agent.conversation_loop._notify_context_engine_turn_complete",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("provisional transcript reached context hook")
        ),
    )

    agent = FakeAgent()
    setattr(agent, "_db_flush_scan_prefix", [])
    setattr(
        agent,
        "_save_trajectory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provisional answer reached trajectory storage")
        ),
    )
    setattr(
        agent,
        "_sync_external_memory_for_turn",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("provisional answer reached external memory")
        ),
    )
    messages = [
        {"role": "user", "content": "delegate and finish"},
        {
            "role": "assistant",
            "content": "PRE_TOOL_PROVISIONAL_SECRET",
            "reasoning": "PRIVATE_REASONING",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "delegate_task", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "dispatched"},
    ]

    result = finalize_turn(
        agent,
        final_response="Initial parent answer must stay hidden.",
        api_call_count=1,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="root-turn",
        user_message="delegate and finish",
        original_user_message="delegate and finish",
        _should_review_memory=False,
        _turn_exit_reason="text_response(final)",
    )

    assert result["suppress_delivery"] is True
    assert result["defer_goal_evaluation"] is True
    assert result["parent_task_barrier_id"] == "barrier-1"
    assert result["delivery_control"] == {
        "disposition": "DEFER",
        "barrier_id": "barrier-1",
        "defer_goal_evaluation": True,
        "outcome_id": "",
    }
    assert agent.persisted_messages is not None
    assert agent.persisted_messages[-1]["content"] == ""
    assert "Initial parent answer must stay hidden" not in str(
        agent.persisted_messages
    )
    assert "PRE_TOOL_PROVISIONAL_SECRET" not in str(agent.persisted_messages)
    assert "PRIVATE_REASONING" not in str(agent.persisted_messages)
    assert all(
        message.get("content") == ""
        for message in agent.persisted_messages
        if message.get("role") == "assistant"
    )
    assert agent.persisted_messages[-1]["_parent_task_candidate"] is True
    assert agent.persisted_messages[-1]["_parent_task_barrier_id"] == "barrier-1"


def test_parent_barrier_lookup_failure_suppresses_delivery(monkeypatch):
    barrier = ModuleType("tools.parent_task_barrier")

    def _broken_policy(**_kwargs):
        raise RuntimeError("state db unavailable")

    setattr(barrier, "finalization_policy", _broken_policy)
    monkeypatch.setitem(sys.modules, "tools.parent_task_barrier", barrier)
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])

    agent = FakeAgent()
    setattr(agent, "_db_flush_scan_prefix", [])
    result = finalize_turn(
        agent,
        final_response="must not leak",
        api_call_count=1,
        interrupted=False,
        failed=False,
        messages=[{"role": "user", "content": "delegate"}],
        conversation_history=[],
        effective_task_id="task",
        turn_id="root-turn",
        user_message="delegate",
        original_user_message="delegate",
        _should_review_memory=False,
        _turn_exit_reason="text_response(final)",
    )

    assert result["suppress_delivery"] is True
    assert result["defer_goal_evaluation"] is True
    assert result["delivery_control"]["disposition"] == "DEFER"
    assert "state db unavailable" in result["error"]
    assert agent.persisted_messages is not None
    assert all(
        "must not leak" not in repr(message) for message in agent.persisted_messages
    )


def test_empty_final_response_recovers_stream_buffer_into_blank_assistant_row(
    monkeypatch, tmp_path
):
    """#95514: empty terminal completion must not persist content='' over a live stream."""
    from hermes_state import SessionDB

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("sess-test", source="cli")
    agent = FakeAgent()
    agent._current_streamed_assistant_text = "Already streamed to the user."

    def persist_to_sqlite(messages, _conversation_history):
        db.replace_messages(agent.session_id, messages)
        agent.persisted_messages = db.get_messages_as_conversation(agent.session_id)

    agent._persist_session = persist_to_sqlite
    messages = [
        {"role": "user", "content": "summarize"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "t1", "type": "function",
                 "function": {"name": "terminal", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "name": "terminal", "content": "ok"},
        {"role": "assistant", "content": "", "_db_persisted": True},
    ]

    result = finalize_turn(
        agent,
        final_response="",
        api_call_count=2,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="summarize",
        original_user_message="summarize",
        _should_review_memory=False,
        _turn_exit_reason="text_response(final)",
    )

    assert result["final_response"] == "Already streamed to the user."
    assistants = [m for m in agent.persisted_messages if m.get("role") == "assistant"]
    assert assistants[-1]["content"] == "Already streamed to the user."
    assert assistants[0].get("tool_calls")
    assert sum(1 for m in assistants) == 2

    reopened = SessionDB(db_path=tmp_path / "state.db")
    reloaded = reopened.get_messages_as_conversation("sess-test")
    assert reloaded[-1]["role"] == "assistant"
    assert reloaded[-1]["content"] == "Already streamed to the user."
    assert sum(1 for m in reloaded if m.get("role") == "assistant") == 2


def test_failed_turn_does_not_recover_stream_buffer_as_final_response(monkeypatch):
    """A failed turn must not invent a successful answer from the stream buffer."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    agent._current_streamed_assistant_text = "partial tokens"

    result = finalize_turn(
        agent,
        final_response="",
        api_call_count=1,
        interrupted=False,
        failed=True,
        messages=[{"role": "user", "content": "q"}],
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="q",
        original_user_message="q",
        _should_review_memory=False,
        _turn_exit_reason="error",
    )

    assert result["final_response"] in ("", None)
    assert result["failed"] is True


def test_delivery_only_reasoning_excerpt_does_not_fill_blank_assistant(monkeypatch):
    """Labeled empty-terminal excerpt is delivery-only, not durable content."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = FakeAgent()
    excerpt = (
        "⚠️ The model produced only internal reasoning and no final answer, "
        "despite retries. Its last reasoning, which may contain the answer:\n\n"
        "The answer is 42"
    )
    messages = [
        {"role": "user", "content": "what is the answer?"},
        {"role": "assistant", "content": ""},
    ]

    result = finalize_turn(
        agent,
        final_response=excerpt,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="what is the answer?",
        original_user_message="what is the answer?",
        _should_review_memory=False,
        _turn_exit_reason="empty_response_exhausted",
    )

    assert result["final_response"] == excerpt
    assert not any(
        m.get("role") == "assistant"
        and "only internal reasoning" in (m.get("content") or "")
        for m in result["messages"]
    )
