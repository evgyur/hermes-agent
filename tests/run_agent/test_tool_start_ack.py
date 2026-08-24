"""Deterministic pre-tool acknowledgment contract."""

import copy
from unittest.mock import MagicMock, patch

from run_agent import AIAgent
from agent.start_ack import StartAckReceipt
from agent.transports.codex import ResponsesApiTransport
from tests.run_agent.test_run_agent import _mock_response, _mock_tool_call


def _agent(callback):
    agent = object.__new__(AIAgent)
    agent.start_ack_callback = callback
    agent._delivered_interim_texts = set()
    agent._tool_start_ack_emitted = False
    return agent


def test_empty_assistant_tool_turn_emits_exactly_one_start_ack():
    callback = MagicMock(return_value=True)
    agent = _agent(callback)
    assistant = {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]}

    assert agent._emit_tool_start_ack_if_needed(assistant) is True
    assert agent._emit_tool_start_ack_if_needed(assistant) is None
    callback.assert_called_once_with()


def test_multi_tool_batch_emits_one_start_ack():
    callback = MagicMock(return_value=True)
    agent = _agent(callback)
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-1"}, {"id": "call-2"}],
    }

    assert agent._emit_tool_start_ack_if_needed(assistant) is True
    callback.assert_called_once_with()


def test_failed_start_ack_is_not_retried_after_first_tool_boundary():
    callback = MagicMock(return_value=False)
    agent = _agent(callback)
    assistant = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}

    assert agent._emit_tool_start_ack_if_needed(assistant) is False
    assert agent._emit_tool_start_ack_if_needed(assistant) is None
    callback.assert_called_once_with()


def test_model_commentary_suppresses_fallback_start_ack():
    start_callback = MagicMock(return_value=True)
    interim_callback = MagicMock()
    agent = _agent(start_callback)
    agent.interim_assistant_callback = interim_callback
    assistant = {
        "role": "assistant",
        "content": "Принял. Проверяю окружения.",
        "tool_calls": [{"id": "call-1"}],
    }

    agent._emit_interim_assistant_message(assistant)
    assert agent._emit_tool_start_ack_if_needed(assistant) is True
    interim_callback.assert_called_once()
    start_callback.assert_not_called()


def test_undelivered_model_commentary_does_not_suppress_fallback_start_ack():
    callback = MagicMock(return_value=True)
    agent = _agent(callback)
    agent.interim_assistant_callback = None
    assistant = {
        "role": "assistant",
        "content": "Commentary hidden by gateway policy.",
        "tool_calls": [{"id": "call-1"}],
    }

    agent._emit_interim_assistant_message(assistant)
    assert agent._emit_tool_start_ack_if_needed(assistant) is True
    callback.assert_called_once_with()


def test_failed_interim_callback_is_not_a_visible_delivery_receipt():
    start_callback = MagicMock(return_value=True)
    agent = _agent(start_callback)
    agent.interim_assistant_callback = MagicMock(return_value=False)
    assistant = {
        "role": "assistant",
        "content": "Queued is not delivered.",
        "tool_calls": [{"id": "call-1"}],
    }

    agent._emit_interim_assistant_message(assistant)
    assert agent._delivered_interim_texts == set()
    assert agent._emit_tool_start_ack_if_needed(assistant) is True
    start_callback.assert_called_once_with()


def test_required_streamed_commentary_waits_for_receipt_barrier():
    events = []
    agent = _agent(lambda: events.append("receipt_failed") or False)
    agent.start_ack_required = True
    agent.interim_assistant_callback = (
        lambda *args, **kwargs: events.append("stream_queued") or False
    )

    agent._fire_streamed_codex_commentary("streamed commentary")
    outcome = agent._emit_tool_start_ack_if_needed(
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}
    )

    assert outcome is False
    assert events == ["receipt_failed"]
    assert agent._delivered_interim_texts == set()


def test_required_mode_without_callback_fails_closed():
    agent = _agent(None)
    agent.start_ack_required = True

    assert agent._emit_tool_start_ack_if_needed(
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}
    ) is False


def test_commentary_on_first_tool_batch_cannot_trigger_late_start_ack():
    start_callback = MagicMock(return_value=True)
    interim_callback = MagicMock()
    agent = _agent(start_callback)
    agent.interim_assistant_callback = interim_callback
    first = {
        "role": "assistant",
        "content": "Starting now.",
        "tool_calls": [{"id": "call-1"}],
    }
    second = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-2"}],
    }

    agent._emit_interim_assistant_message(first)
    assert agent._emit_tool_start_ack_if_needed(first) is True
    agent._delivered_interim_texts.clear()
    assert agent._emit_tool_start_ack_if_needed(second) is None
    start_callback.assert_not_called()


def test_already_streamed_commentary_suppresses_fallback_start_ack():
    callback = MagicMock(return_value=True)
    agent = _agent(callback)
    agent._delivered_interim_texts.add("already delivered")

    assert agent._emit_tool_start_ack_if_needed(
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]}
    ) is True
    callback.assert_not_called()


def _loop_agent():
    tool_defs = [{
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "search",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    with (
        patch("run_agent.get_tool_definitions", return_value=tool_defs),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    agent.tool_delay = 0
    agent.compression_enabled = False
    agent.save_trajectories = False
    return agent


def test_conversation_settles_start_ack_before_first_tool_effect():
    events = []
    agent = _loop_agent()
    tool_call = _mock_tool_call(name="web_search", call_id="c1")
    agent.client.chat.completions.create.side_effect = [
        _mock_response(content="", finish_reason="tool_calls", tool_calls=[tool_call]),
        _mock_response(content="done", finish_reason="stop"),
    ]
    agent.start_ack_callback = lambda _visible=None: events.append("ack") or True

    with (
        patch(
            "run_agent.handle_function_call",
            side_effect=lambda *args, **kwargs: events.append("tool") or "result",
        ),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Go")

    assert result["final_response"] == "done"
    assert events == ["ack", "tool"]


def test_required_start_ack_failure_prevents_first_tool_effect():
    events = []
    agent = _loop_agent()
    tool_call = _mock_tool_call(name="web_search", call_id="c1")
    agent.client.chat.completions.create.return_value = _mock_response(
        content="", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    agent.start_ack_callback = lambda _visible="": events.append("ack_failed") or False
    agent.start_ack_required = True

    with (
        patch(
            "run_agent.handle_function_call",
            side_effect=lambda *args, **kwargs: events.append("tool") or "result",
        ),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Go")

    assert result["failed"] is True
    assert result["turn_exit_reason"] == "start_ack_delivery_failed"
    assert events == ["ack_failed"]
    cancelled = [m for m in result["messages"] if m.get("role") == "tool"]
    assert len(cancelled) == 1
    assert cancelled[0]["tool_call_id"] == "c1"
    assert "No tool effect was attempted" in cancelled[0]["content"]

    # The paired cancellation result is safe provider history: the next user
    # turn succeeds and cannot replay the cancelled tool call.
    agent.start_ack_callback = None
    agent.client.chat.completions.create.return_value = _mock_response(
        content="safe next turn", finish_reason="stop"
    )
    with (
        patch("run_agent.handle_function_call") as tool_handler,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        next_result = agent.run_conversation(
            "continue", conversation_history=result["messages"]
        )
    assert next_result["final_response"] == "safe next turn"
    tool_handler.assert_not_called()


def test_required_ack_failure_first_flush_is_already_replay_safe():
    durable = []
    agent = _loop_agent()
    tool_call = _mock_tool_call(name="web_search", call_id="atomic-cancel")
    agent.client.chat.completions.create.return_value = _mock_response(
        content="", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    agent.start_ack_callback = lambda: False
    agent.start_ack_required = True

    def capture_first_flush(messages, conversation_history):
        durable[:] = copy.deepcopy(messages)
        return True

    with (
        patch("run_agent.handle_function_call") as tool_handler,
        patch.object(agent, "_persist_session"),
        patch.object(
            agent, "_flush_messages_to_session_db", side_effect=capture_first_flush
        ) as flush,
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Go")

    assert result["turn_exit_reason"] == "start_ack_delivery_failed"
    assert flush.call_count == 1
    tool_handler.assert_not_called()
    assistant_calls = [
        call["id"]
        for message in durable
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    ]
    tool_results = [
        message["tool_call_id"]
        for message in durable
        if message.get("role") == "tool"
    ]
    assert assistant_calls == ["atomic-cancel"]
    assert tool_results == ["atomic-cancel"]
    assert durable[-1]["effect_disposition"] == "none"


def test_required_commentary_uses_receipt_barrier_not_async_interim_queue():
    events = []
    agent = _loop_agent()
    tool_call = _mock_tool_call(name="web_search", call_id="c1")
    agent.client.chat.completions.create.return_value = _mock_response(
        content="Checking now.", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    agent.interim_assistant_callback = lambda *args, **kwargs: events.append(
        "commentary_queued_only"
    )
    agent.start_ack_callback = lambda: events.append(
        ("receipt_failed", agent._pending_start_ack_visible_text)
    ) or False
    agent.start_ack_required = True

    with (
        patch(
            "run_agent.handle_function_call",
            side_effect=lambda *args, **kwargs: events.append("tool") or "result",
        ),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Go")

    assert result["turn_exit_reason"] == "start_ack_delivery_failed"
    assert events == [("receipt_failed", "Checking now.")]


def test_best_effort_start_ack_failure_preserves_tool_availability():
    events = []
    agent = _loop_agent()
    tool_call = _mock_tool_call(name="web_search", call_id="c1")
    agent.client.chat.completions.create.side_effect = [
        _mock_response(content="", finish_reason="tool_calls", tool_calls=[tool_call]),
        _mock_response(content="done", finish_reason="stop"),
    ]
    agent.start_ack_callback = (
        lambda _visible=None: events.append("ack_failed") or False
    )
    agent.start_ack_required = False

    with (
        patch(
            "run_agent.handle_function_call",
            side_effect=lambda *args, **kwargs: events.append("tool") or "result",
        ),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Go")

    assert result["final_response"] == "done"
    assert events == ["ack_failed", "tool"]


def test_required_multi_batch_delivers_later_commentary_once():
    events = []
    agent = _loop_agent()
    first = _mock_tool_call(name="web_search", call_id="c1")
    second = _mock_tool_call(name="web_search", call_id="c2")
    agent.client.chat.completions.create.side_effect = [
        _mock_response(
            content="First commentary.",
            finish_reason="tool_calls",
            tool_calls=[first],
        ),
        _mock_response(
            content="Second commentary.",
            finish_reason="tool_calls",
            tool_calls=[second],
        ),
        _mock_response(content="done", finish_reason="stop"),
    ]
    agent.start_ack_required = True
    agent.start_ack_callback = lambda: events.append(
        ("ack", agent._pending_start_ack_visible_text)
    ) or StartAckReceipt(text=agent._pending_start_ack_visible_text)
    agent.interim_assistant_callback = (
        lambda text, **kwargs: events.append(("interim", text)) or True
    )

    with (
        patch(
            "run_agent.handle_function_call",
            side_effect=lambda *args, **kwargs: events.append("tool") or "result",
        ),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Go")

    assert result["final_response"] == "done"
    assert events == [
        ("ack", "First commentary."),
        "tool",
        ("interim", "Second commentary."),
        "tool",
    ]


def test_ack_delivered_housekeeping_final_is_marked_already_delivered():
    agent = _loop_agent()
    agent.valid_tool_names.add("memory")
    tool_call = _mock_tool_call(name="memory", call_id="memory-1")
    final_text = "The requested result is ready."
    agent.client.chat.completions.create.side_effect = [
        _mock_response(
            content=final_text,
            finish_reason="tool_calls",
            tool_calls=[tool_call],
        ),
        _mock_response(content="", finish_reason="stop"),
    ]
    agent.start_ack_required = True
    agent.start_ack_callback = lambda: StartAckReceipt(text=final_text)

    with (
        patch("run_agent.handle_function_call", return_value="saved"),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch(
            "tools.parent_task_barrier.finalization_policy",
            return_value={"action": "deliver"},
        ),
    ):
        result = agent.run_conversation("Answer and remember it")

    assert result["final_response"] == final_text
    assert result["response_already_delivered"] is True


def test_generic_ack_receipt_cannot_claim_raw_model_narration():
    agent = _loop_agent()
    agent.valid_tool_names.add("memory")
    final_text = "Raw model narration"
    tool_call = _mock_tool_call(name="memory", call_id="memory-generic-ack")
    agent.client.chat.completions.create.side_effect = [
        _mock_response(
            content=final_text,
            finish_reason="tool_calls",
            tool_calls=[tool_call],
        ),
        _mock_response(content="", finish_reason="stop"),
    ]
    agent.start_ack_required = True
    agent.start_ack_callback = lambda: StartAckReceipt(
        text="Configured generic acknowledgement"
    )

    with (
        patch("run_agent.handle_function_call", return_value="saved"),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch(
            "tools.parent_task_barrier.finalization_policy",
            return_value={"action": "deliver"},
        ),
    ):
        result = agent.run_conversation("Do the work")

    assert result["final_response"].startswith(final_text)
    assert result["response_already_delivered"] is False


def test_housekeeping_preview_without_receipt_cannot_claim_delivery_authority():
    agent = _loop_agent()
    agent.valid_tool_names.add("memory")
    agent.start_ack_required = False
    agent.start_ack_callback = None
    agent.interim_assistant_callback = None
    agent.stream_delta_callback = None
    final_text = "The requested result is ready."
    tool_call = _mock_tool_call(name="memory", call_id="memory-no-wire")
    agent.client.chat.completions.create.side_effect = [
        _mock_response(
            content=final_text,
            finish_reason="tool_calls",
            tool_calls=[tool_call],
        ),
        _mock_response(content="", finish_reason="stop"),
    ]

    with (
        patch("run_agent.handle_function_call", return_value="saved"),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch(
            "tools.parent_task_barrier.finalization_policy",
            return_value={"action": "deliver"},
        ),
    ):
        result = agent.run_conversation("Answer and remember it")

    assert result["final_response"] == final_text
    assert result["response_previewed"] is True
    assert result["response_already_delivered"] is False


def test_conversation_with_no_tools_emits_no_start_ack():
    agent = _loop_agent()
    callback = MagicMock(return_value=True)
    agent.start_ack_callback = callback
    agent.client.chat.completions.create.return_value = _mock_response(
        content="short answer", finish_reason="stop"
    )

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("simple question")

    assert result["final_response"] == "short answer"
    callback.assert_not_called()


def test_reused_agent_emits_once_for_each_user_turn():
    agent = _loop_agent()
    callback = MagicMock(return_value=True)
    agent.start_ack_callback = callback

    with (
        patch("run_agent.handle_function_call", return_value="result"),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_flush_messages_to_session_db", return_value=True),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        for prompt in ("first", "second"):
            tool_call = _mock_tool_call(name="web_search")
            agent.client.chat.completions.create.side_effect = [
                _mock_response(
                    content="", finish_reason="tool_calls", tool_calls=[tool_call]
                ),
                _mock_response(content=f"done {prompt}", finish_reason="stop"),
            ]
            result = agent.run_conversation(prompt)
            assert result["final_response"] == f"done {prompt}"

    assert callback.call_count == 2


def test_required_ack_rejects_codex_app_server_before_runtime_effect():
    agent = _loop_agent()
    agent.api_mode = "codex_app_server"
    agent.start_ack_required = True
    agent._run_codex_app_server_turn = MagicMock(return_value={"completed": True})

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("Go")

    agent._run_codex_app_server_turn.assert_not_called()
    assert result["failed"] is True
    assert result["turn_exit_reason"] == "start_ack_runtime_unsupported"


def test_required_ack_rejects_provider_executed_responses_tool_preflight():
    transport = ResponsesApiTransport()
    api_kwargs = {
        "model": "gpt-test",
        "instructions": "test",
        "input": [{"role": "user", "content": "Go"}],
        "tools": [{"type": "web_search"}],
    }

    try:
        transport.preflight_kwargs(
            api_kwargs,
            reject_provider_executed_tools=True,
        )
    except RuntimeError as exc:
        assert "provider-executed Responses tools" in str(exc)
    else:
        raise AssertionError("strict provider tool request did not fail closed")


def test_best_effort_keeps_provider_executed_responses_tools_available():
    transport = ResponsesApiTransport()
    normalized = transport.preflight_kwargs(
        {
            "model": "gpt-test",
            "instructions": "test",
            "input": [{"role": "user", "content": "Go"}],
            "tools": [{"type": "web_search"}],
        },
        reject_provider_executed_tools=False,
    )

    assert normalized["tools"] == [{"type": "web_search"}]
