"""Regression for exact authority lost by the agent-level Tool Search unwrap.

The agent executor unwraps ``tool_call`` before ``model_tools.handle_function_call``.
That path must bind the exact admitted underlying tool while its handler runs;
the model-tools bridge binding alone does not cover real gateway dispatch.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent
from tools.tool_search import get_active_scoped_deferred_tool_authority


def _tc(call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name="tool_call",
            arguments=json.dumps(
                {
                    "name": "thread_start",
                    "arguments": {"goal": "executor authority probe"},
                }
            ),
        ),
    )


@pytest.fixture()
def agent() -> AIAgent:
    defs = [
        {
            "type": "function",
            "function": {
                "name": "tool_call",
                "description": "deferred bridge",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    with (
        patch("run_agent.get_tool_definitions", return_value=defs),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        instance = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        instance.client = MagicMock()
        return instance


def _executor_patches(observed: list[tuple[str, str | None]]):
    def fake_handle(name, args, task_id, **kwargs):
        observed.append((name, get_active_scoped_deferred_tool_authority()))
        return json.dumps({"ok": True})

    return (
        patch(
            "tools.tool_search.resolve_underlying_call",
            return_value=("thread_start", {"goal": "executor authority probe"}, None),
        ),
        patch("tools.tool_search.validate_deferred_call_args", return_value=None),
        patch(
            "agent.tool_executor._tool_search_scoped_names",
            return_value=frozenset({"thread_start"}),
        ),
        patch("run_agent.handle_function_call", side_effect=fake_handle),
    )


def test_sequential_tool_call_unwrap_binds_exact_deferred_authority(agent: AIAgent) -> None:
    observed: list[tuple[str, str | None]] = []
    message = SimpleNamespace(content="", tool_calls=[_tc("call-sequential")])
    messages: list[dict] = []

    patches = _executor_patches(observed)
    with patches[0], patches[1], patches[2], patches[3]:
        agent._execute_tool_calls_sequential(message, messages, "task-sequential")

    assert observed == [("thread_start", "thread_start")]
    assert get_active_scoped_deferred_tool_authority() is None


def test_concurrent_tool_call_unwrap_binds_exact_deferred_authority(agent: AIAgent) -> None:
    observed: list[tuple[str, str | None]] = []
    message = SimpleNamespace(
        content="",
        tool_calls=[_tc("call-concurrent-a"), _tc("call-concurrent-b")],
    )
    messages: list[dict] = []

    patches = _executor_patches(observed)
    with patches[0], patches[1], patches[2], patches[3]:
        agent._execute_tool_calls_concurrent(message, messages, "task-concurrent")

    assert sorted(observed) == [
        ("thread_start", "thread_start"),
        ("thread_start", "thread_start"),
    ]
    assert get_active_scoped_deferred_tool_authority() is None


def test_direct_tool_dispatch_does_not_mint_deferred_authority(agent: AIAgent) -> None:
    observed: list[tuple[str, str | None]] = []
    direct = SimpleNamespace(
        id="call-direct",
        type="function",
        function=SimpleNamespace(
            name="thread_start",
            arguments=json.dumps({"goal": "direct call must stay untrusted"}),
        ),
    )
    message = SimpleNamespace(content="", tool_calls=[direct])

    def fake_handle(name, args, task_id, **kwargs):
        observed.append((name, get_active_scoped_deferred_tool_authority()))
        return json.dumps({"ok": True})

    with patch("run_agent.handle_function_call", side_effect=fake_handle):
        agent._execute_tool_calls_sequential(message, [], "task-direct")

    assert observed == [("thread_start", None)]
    assert get_active_scoped_deferred_tool_authority() is None


def test_deferred_authority_resets_when_underlying_dispatch_raises(agent: AIAgent) -> None:
    observed: list[str | None] = []
    message = SimpleNamespace(content="", tool_calls=[_tc("call-raises")])

    def fake_handle(name, args, task_id, **kwargs):
        observed.append(get_active_scoped_deferred_tool_authority())
        raise RuntimeError("probe failure")

    patches = _executor_patches([])
    with (
        patches[0],
        patches[1],
        patches[2],
        patch("run_agent.handle_function_call", side_effect=fake_handle),
    ):
        agent._execute_tool_calls_sequential(message, [], "task-raises")

    assert observed == ["thread_start"]
    assert get_active_scoped_deferred_tool_authority() is None
