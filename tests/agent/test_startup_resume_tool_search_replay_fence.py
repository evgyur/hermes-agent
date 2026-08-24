"""Cold regression for startup replay through the Tool Search bridge.

This file is run in its own subprocess by ``scripts/run_tests.sh``.  The model
emits ``tool_call`` while dispatch sees its unwrapped underlying tool; replay
authority must remain bound to the immutable wrapper stored in the transcript.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent


def _tool_defs(*names: str) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


@pytest.fixture()
def agent():
    with (
        patch("run_agent.get_tool_definitions", return_value=_tool_defs("web_search")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        value = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    value.client = MagicMock()
    value.startup_resume = True
    return value


def _wrapped_call(arguments: dict, call_id: str = "wrapped-1") -> SimpleNamespace:
    raw = {"name": "web_search", "arguments": arguments}
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="tool_call",
            arguments=json.dumps(raw, ensure_ascii=False),
        ),
    )


def _raw_fence(arguments: dict) -> dict[tuple[str, str], str]:
    raw = {"name": "web_search", "arguments": arguments}
    canonical = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {("tool_call", canonical): "completed_effect_receipt"}


def _execute(agent, mode: str, tool_call, messages: list[dict]) -> None:
    assistant = SimpleNamespace(tool_calls=[tool_call])
    executor = getattr(agent, f"_execute_tool_calls_{mode}")
    executor(assistant, messages, "recovery-task")


def _tool_search_unwrap_patches():
    return (
        patch(
            "agent.tool_executor._tool_search_scoped_names",
            return_value=frozenset({"web_search"}),
        ),
        patch(
            "tools.tool_search.resolve_underlying_call",
            side_effect=lambda raw: (raw["name"], raw["arguments"], None),
        ),
        patch("tools.tool_search.validate_deferred_call_args", return_value=None),
    )


@pytest.mark.parametrize("mode", ["sequential", "concurrent"])
def test_completed_wrapped_effect_is_fenced_before_underlying_dispatch(agent, mode):
    completed_args = {"q": "already completed"}
    agent.startup_resume_effect_fence = _raw_fence(completed_args)
    messages: list[dict] = []

    scope_patch, unwrap_patch, validate_patch = _tool_search_unwrap_patches()
    with (
        scope_patch,
        unwrap_patch,
        validate_patch,
        patch("run_agent.handle_function_call", return_value="SHOULD NOT RUN") as invoke,
    ):
        _execute(agent, mode, _wrapped_call(completed_args), messages)

    invoke.assert_not_called()
    result = messages[-1]["content"]
    assert '"status": "replay_fenced"' in result
    assert '"reason": "completed_effect_receipt"' in result


@pytest.mark.parametrize("mode", ["sequential", "concurrent"])
def test_distinct_unfinished_wrapped_effect_still_dispatches(agent, mode):
    agent.startup_resume_effect_fence = _raw_fence({"q": "already completed"})
    messages: list[dict] = []
    fresh_args = {"q": "not completed"}

    scope_patch, unwrap_patch, validate_patch = _tool_search_unwrap_patches()
    with (
        scope_patch,
        unwrap_patch,
        validate_patch,
        patch("run_agent.handle_function_call", return_value="fresh result") as invoke,
    ):
        _execute(agent, mode, _wrapped_call(fresh_args, "wrapped-2"), messages)

    assert invoke.call_count == 1
    positional = invoke.call_args.args
    assert positional[:3] == ("web_search", fresh_args, "recovery-task")
    assert "fresh result" in messages[-1]["content"]
