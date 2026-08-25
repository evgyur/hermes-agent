from __future__ import annotations

import concurrent.futures
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import model_tools


def _tool_call(call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name="tool_call",
            arguments=json.dumps(
                {"name": "thread_start", "arguments": {"goal": "probe"}}
            ),
        ),
    )


@pytest.fixture()
def restricted_agent():
    from run_agent import AIAgent

    definitions = [
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
        patch("run_agent.get_tool_definitions", return_value=definitions),
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
        return agent


def _executor_patches(observed):
    from tools.tool_search import get_active_scoped_deferred_tool_authority

    def fake_handle(name, args, task_id, **kwargs):
        observed.append((name, get_active_scoped_deferred_tool_authority()))
        return json.dumps({"ok": True})

    return (
        patch(
            "tools.tool_search.resolve_underlying_call",
            return_value=("thread_start", {"goal": "probe"}, None),
        ),
        patch("tools.tool_search.validate_deferred_call_args", return_value=None),
        patch(
            "agent.tool_executor._tool_search_scoped_names",
            return_value=frozenset({"thread_start"}),
        ),
        patch("run_agent.handle_function_call", side_effect=fake_handle),
    )


def test_unadvertised_direct_call_has_zero_effects(monkeypatch):
    calls = []

    def fake_dispatch(*args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    result = json.loads(
        model_tools.handle_function_call(
            "cronjob",
            {"action": "list"},
            enabled_tools=["read_file", "search_files", "terminal"],
            enabled_toolsets=["file", "terminal"],
        )
    )

    assert "not available" in result["error"]
    assert calls == []


def test_malformed_unadvertised_call_has_zero_effects(monkeypatch):
    calls = []

    def fake_dispatch(*args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    result = json.loads(
        model_tools.handle_function_call(
            "cronjob",
            "provider-injected-non-object",
            enabled_tools=["terminal"],
            enabled_toolsets=["terminal"],
        )
    )

    assert "not available" in result["error"]
    assert calls == []


def test_advertised_valid_call_executes(monkeypatch):
    calls = []

    def fake_dispatch(name, args, **kwargs):
        calls.append((name, args, kwargs))
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    result = json.loads(
        model_tools.handle_function_call(
            "terminal",
            {"command": "pwd"},
            enabled_tools=["terminal"],
            enabled_toolsets=["terminal"],
        )
    )

    assert result == {"ok": True}
    assert calls and calls[0][:2] == ("terminal", {"command": "pwd"})


def test_deferred_authority_requires_exact_name(monkeypatch):
    from tools.tool_search import _bind_scoped_deferred_tool_authority

    calls = []
    monkeypatch.setattr(
        model_tools.registry,
        "dispatch",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    with _bind_scoped_deferred_tool_authority("continuum_tasks"):
        result = json.loads(
            model_tools.handle_function_call(
                "thread_start",
                {"goal": "must remain blocked"},
                enabled_tools=["tool_search", "tool_describe", "tool_call"],
                enabled_toolsets=["continuum"],
            )
        )

    assert "not available" in result["error"]
    assert calls == []


def test_deferred_authority_allows_exact_validated_name(monkeypatch):
    from tools.tool_search import _bind_scoped_deferred_tool_authority

    calls = []

    def fake_dispatch(name, args, **kwargs):
        calls.append((name, args, kwargs))
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    with _bind_scoped_deferred_tool_authority("thread_start"):
        result = json.loads(
            model_tools.handle_function_call(
                "thread_start",
                {"goal": "validated deferred dispatch"},
                enabled_tools=["tool_search", "tool_describe", "tool_call"],
                enabled_toolsets=["continuum"],
            )
        )

    assert result == {"ok": True}
    assert calls and calls[0][0] == "thread_start"


def test_authority_resets_after_exception_and_cancellation():
    from tools.tool_search import (
        _bind_scoped_deferred_tool_authority,
        get_active_scoped_deferred_tool_authority,
    )

    with pytest.raises(RuntimeError):
        with _bind_scoped_deferred_tool_authority("thread_start"):
            assert get_active_scoped_deferred_tool_authority() == "thread_start"
            raise RuntimeError("dispatch failed")
    assert get_active_scoped_deferred_tool_authority() is None

    def worker():
        try:
            with _bind_scoped_deferred_tool_authority("thread_start"):
                assert get_active_scoped_deferred_tool_authority() == "thread_start"
                raise concurrent.futures.CancelledError()
        finally:
            assert get_active_scoped_deferred_tool_authority() is None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        with pytest.raises(concurrent.futures.CancelledError):
            future.result()

    assert get_active_scoped_deferred_tool_authority() is None


def test_sequential_executor_binds_and_resets_deferred_authority(restricted_agent):
    from tools.tool_search import get_active_scoped_deferred_tool_authority

    observed = []
    patches = _executor_patches(observed)
    with patches[0], patches[1], patches[2], patches[3]:
        restricted_agent._execute_tool_calls_sequential(
            SimpleNamespace(content="", tool_calls=[_tool_call("sequential")]),
            [],
            "task-sequential",
        )

    assert observed == [("thread_start", "thread_start")]
    assert get_active_scoped_deferred_tool_authority() is None


def test_concurrent_executor_binds_and_resets_deferred_authority(restricted_agent):
    from tools.tool_search import get_active_scoped_deferred_tool_authority

    observed = []
    patches = _executor_patches(observed)
    with patches[0], patches[1], patches[2], patches[3]:
        restricted_agent._execute_tool_calls_concurrent(
            SimpleNamespace(
                content="",
                tool_calls=[_tool_call("concurrent-a"), _tool_call("concurrent-b")],
            ),
            [],
            "task-concurrent",
        )

    assert sorted(observed) == [
        ("thread_start", "thread_start"),
        ("thread_start", "thread_start"),
    ]
    assert get_active_scoped_deferred_tool_authority() is None


@pytest.mark.parametrize("execution", ["sequential", "concurrent"])
@pytest.mark.parametrize(
    "failure", [RuntimeError("dispatch failed"), concurrent.futures.CancelledError()]
)
def test_executor_failure_or_cancellation_resets_deferred_authority(
    restricted_agent, execution, failure
):
    from tools.tool_search import get_active_scoped_deferred_tool_authority

    observed = []

    def fail_dispatch(name, args, task_id, **kwargs):
        observed.append((name, get_active_scoped_deferred_tool_authority()))
        raise failure

    patches = _executor_patches([])
    message = SimpleNamespace(content="", tool_calls=[_tool_call(execution)])
    with (
        patches[0],
        patches[1],
        patches[2],
        patch("run_agent.handle_function_call", side_effect=fail_dispatch),
    ):
        getattr(restricted_agent, f"_execute_tool_calls_{execution}")(
            message, [], f"task-{execution}"
        )

    assert observed == [("thread_start", "thread_start")]
    assert get_active_scoped_deferred_tool_authority() is None
