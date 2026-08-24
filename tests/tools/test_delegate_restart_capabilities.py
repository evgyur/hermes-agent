from __future__ import annotations

from types import SimpleNamespace


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}


def test_resumed_child_capabilities_are_exact_and_refresh_stable() -> None:
    from model_tools import get_tool_definitions
    from toolsets import TOOLSETS
    from tools.delegate_tool import _apply_child_capability_ceiling

    child = SimpleNamespace(
        tools=[_schema("read_file"), _schema("terminal"), _schema("web_search")],
        valid_tool_names={"read_file", "terminal", "web_search"},
        enabled_toolsets=["hermes-cli"],
        disabled_toolsets=[],
        _context_engine_tool_names={"web_search"},
    )

    _apply_child_capability_ceiling(child, ["read_file", "terminal"])
    synthetic = child.enabled_toolsets[0]
    try:
        assert child.valid_tool_names == {"read_file", "terminal"}
        assert {item["function"]["name"] for item in child.tools} == {
            "read_file",
            "terminal",
        }
        assert child._context_engine_tool_names == set()

        refreshed = get_tool_definitions(
            enabled_toolsets=child.enabled_toolsets,
            disabled_toolsets=child.disabled_toolsets,
            quiet_mode=True,
        )
        assert {item["function"]["name"] for item in refreshed} == {
            "read_file",
            "terminal",
        }
    finally:
        TOOLSETS.pop(synthetic, None)


def test_resume_fails_closed_without_persisted_capability_snapshot() -> None:
    from tools.delegate_tool import resume_async_delegation

    claim = {
        "delegation_id": "deleg_missing_caps",
        "task": {"goals": ["continue"]},
        "child_session_ids": ["child-1"],
        "child_capability_names": [],
    }
    assert resume_async_delegation(claim, parent_agent=SimpleNamespace()) == "failed"
