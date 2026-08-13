from __future__ import annotations

import json

import model_tools
from tools.tool_search import _bind_scoped_deferred_tool_authority


def test_direct_dispatch_respects_effective_tool_allowlist(monkeypatch):
    called = []

    def fake_dispatch(*args, **kwargs):
        called.append((args, kwargs))
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
    assert called == []


def test_scoped_deferred_dispatch_can_leave_bridge_allowlist(monkeypatch):
    called = []

    def fake_dispatch(name, args, **kwargs):
        called.append((name, args, kwargs))
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    with _bind_scoped_deferred_tool_authority("thread_start"):
        result = json.loads(
            model_tools.handle_function_call(
                "thread_start",
                {"goal": "authorized deferred dispatch"},
                enabled_tools=["tool_search", "tool_describe", "tool_call"],
                enabled_toolsets=["continuum"],
            )
        )

    assert result == {"ok": True}
    assert called and called[0][0] == "thread_start"


def test_scoped_deferred_dispatch_requires_exact_authority(monkeypatch):
    called = []

    def fake_dispatch(*args, **kwargs):
        called.append((args, kwargs))
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    with _bind_scoped_deferred_tool_authority("continuum_tasks"):
        result = json.loads(
            model_tools.handle_function_call(
                "thread_start",
                {"goal": "mismatched authority must fail"},
                enabled_tools=["tool_search", "tool_describe", "tool_call"],
                enabled_toolsets=["continuum"],
            )
        )

    assert "not available" in result["error"]
    assert called == []


def test_unrestricted_dispatch_keeps_legacy_none_semantics(monkeypatch):
    result = model_tools.handle_function_call(
        "tool_search",
        {"query": "file"},
        enabled_tools=None,
        enabled_toolsets=None,
    )
    assert json.loads(result).get("error") is None
