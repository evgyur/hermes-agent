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
        patch(
            "run_agent.get_tool_definitions",
            return_value=_tool_defs("web_search", "terminal", "read_file"),
        ),
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
    value.startup_resume_reconciliation_only = False
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


def _direct_call(name: str, arguments: dict, call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
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


@pytest.mark.parametrize("mode", ["sequential", "concurrent"])
def test_reconciliation_only_blocks_new_effectful_call(agent, mode):
    agent.startup_resume_reconciliation_only = True
    agent.startup_resume_effect_fence = {}
    messages: list[dict] = []

    with patch("run_agent.handle_function_call", return_value="MUTATED") as invoke:
        _execute(
            agent,
            mode,
            _direct_call("terminal", {"command": "touch /tmp/nope"}, "effect-1"),
            messages,
        )

    invoke.assert_not_called()
    result = messages[-1]["content"]
    assert '"status": "reconciliation_effect_blocked"' in result
    assert '"reason": "startup_reconciliation_effect_block"' in result


@pytest.mark.parametrize("mode", ["sequential", "concurrent"])
def test_reconciliation_only_allows_no_effect_readback(agent, mode):
    agent.startup_resume_reconciliation_only = True
    agent.startup_resume_effect_fence = {}
    messages: list[dict] = []
    args = {"path": "/tmp/state.json"}

    with patch("run_agent.handle_function_call", return_value="readback") as invoke:
        _execute(
            agent,
            mode,
            _direct_call("read_file", args, "read-1"),
            messages,
        )

    invoke.assert_called_once()
    assert invoke.call_args.args[:3] == ("read_file", args, "recovery-task")
    assert "readback" in messages[-1]["content"]


@pytest.mark.parametrize("mode", ["sequential", "concurrent"])
def test_unknown_exact_replay_uses_unknown_fence_message(agent, mode):
    agent.startup_resume_reconciliation_only = True
    args = {"command": "deploy candidate"}
    canonical = json.dumps(
        args, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    agent.startup_resume_effect_fence = {
        ("terminal", canonical): "unknown_pre_restart_effect"
    }
    messages: list[dict] = []

    with patch("run_agent.handle_function_call", return_value="REPLAYED") as invoke:
        _execute(agent, mode, _direct_call("terminal", args, "unknown-1"), messages)

    invoke.assert_not_called()
    result = messages[-1]["content"]
    assert '"status": "replay_fenced"' in result
    assert '"reason": "unknown_pre_restart_effect"' in result
    assert "UNKNOWN outcome" in result
