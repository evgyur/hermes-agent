"""Pure tool-call guardrail primitive tests."""

import json
from types import SimpleNamespace

from agent.conversation_loop import _without_guardrail_suppressed_tool_definitions
from agent.tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    ToolCallSignature,
    canonical_tool_args,
    classify_tool_failure,
)


def test_tool_call_signature_hashes_canonical_nested_unicode_args_without_exposing_raw_args():
    args_a = {
        "z": [{"β": "☤", "a": 1}],
        "a": {"y": 2, "x": "secret-token-value"},
    }
    args_b = {
        "a": {"x": "secret-token-value", "y": 2},
        "z": [{"a": 1, "β": "☤"}],
    }

    assert canonical_tool_args(args_a) == canonical_tool_args(args_b)
    sig_a = ToolCallSignature.from_call("web_search", args_a)
    sig_b = ToolCallSignature.from_call("web_search", args_b)

    assert sig_a == sig_b
    assert len(sig_a.args_hash) == 64
    metadata = sig_a.to_metadata()
    assert metadata == {"tool_name": "web_search", "args_hash": sig_a.args_hash}
    assert "secret-token-value" not in json.dumps(metadata)
    assert "☤" not in json.dumps(metadata)




def test_config_parses_nested_warn_and_hard_stop_thresholds():
    cfg = ToolCallGuardrailConfig.from_mapping(
        {
            "warnings_enabled": False,
            "hard_stop_enabled": True,
            "warn_after": {
                "exact_failure": 3,
                "same_tool_failure": 4,
                "idempotent_no_progress": 5,
            },
            "hard_stop_after": {
                "exact_failure": 6,
                "same_tool_failure": 7,
                "idempotent_no_progress": 8,
            },
        }
    )

    assert cfg.warnings_enabled is False
    assert cfg.hard_stop_enabled is True
    assert cfg.exact_failure_warn_after == 3
    assert cfg.same_tool_failure_warn_after == 4
    assert cfg.no_progress_warn_after == 5
    assert cfg.exact_failure_block_after == 6
    assert cfg.same_tool_failure_halt_after == 7
    assert cfg.no_progress_block_after == 8


def test_config_adds_explicit_read_only_mcp_tools_to_idempotent_guardrails():
    tool_name = "mcp__seya__get_workshop"
    cfg = ToolCallGuardrailConfig.from_mapping(
        {"additional_idempotent_tools": [tool_name]}
    )
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            no_progress_warn_after=2,
            no_progress_block_after=2,
            idempotent_tools=cfg.idempotent_tools,
        )
    )
    args = {"workshop_id": "human20"}

    assert controller.before_call(tool_name, args).action == "allow"
    assert (
        controller.after_call(tool_name, args, "same content", failed=False).action
        == "allow"
    )
    assert controller.before_call(tool_name, args).action == "allow"
    warning = controller.after_call(tool_name, args, "same content", failed=False)
    assert warning.code == "idempotent_no_progress_warning"

    blocked = controller.before_call(tool_name, args)
    assert blocked.action == "block"
    assert blocked.code == "idempotent_no_progress_block"


def test_config_parses_successful_tools_that_require_immediate_synthesis():
    tool_name = "mcp__seya__get_workshop_lesson_count"
    cfg = ToolCallGuardrailConfig.from_mapping(
        {"synthesize_after_successful_tools": [tool_name, "", 7, None]}
    )

    assert cfg.synthesize_after_successful_tools == frozenset({tool_name})


def test_config_can_block_repeated_read_without_halting_the_turn():
    tool_name = "mcp__seya__list_resources"
    cfg = ToolCallGuardrailConfig.from_mapping(
        {
            "hard_stop_enabled": True,
            "no_progress_block_after": 1,
            "additional_idempotent_tools": [tool_name],
            "resume_after_no_progress_block": True,
        }
    )
    controller = ToolCallGuardrailController(cfg)

    controller.after_call(tool_name, {}, "same resources", failed=False)
    decision = controller.before_call(tool_name, {})

    assert decision.action == "block_continue"
    assert decision.allows_execution is False
    assert decision.should_halt is False
    assert controller.halt_decision is None


def test_guardrail_suppresses_only_the_blocked_tool_for_the_rest_of_the_turn():
    blocked = "mcp__seya__get_workshop"
    allowed = "mcp__seya__get_content_detail"
    tools = [
        {"type": "function", "function": {"name": blocked}},
        {"type": "function", "function": {"name": allowed}},
    ]
    agent = SimpleNamespace(_guardrail_suppressed_tools={blocked})

    filtered = _without_guardrail_suppressed_tool_definitions(agent, tools)

    assert [tool["function"]["name"] for tool in filtered] == [allowed]
    assert agent._guardrail_suppressed_tools == {blocked}


def test_repeated_successful_read_forces_one_tool_free_synthesis_call():
    blocked = "mcp__seya__get_section"
    tools = [
        {"type": "function", "function": {"name": blocked}},
        {"type": "function", "function": {"name": "mcp__seya__get_workshop"}},
        {"type": "function", "function": {"name": "mcp__seya__search"}},
    ]
    agent = SimpleNamespace(
        _guardrail_suppressed_tools={blocked},
        _force_synthesis_without_tools=True,
    )

    assert _without_guardrail_suppressed_tool_definitions(agent, tools) == []
    assert agent._force_synthesis_without_tools is False
    assert [
        tool["function"]["name"]
        for tool in _without_guardrail_suppressed_tool_definitions(agent, tools)
    ] == ["mcp__seya__get_workshop", "mcp__seya__search"]


def test_config_rejects_invalid_additional_idempotent_tool_values():
    cfg = ToolCallGuardrailConfig.from_mapping(
        {"additional_idempotent_tools": ["mcp__seya__get_workshop", "", 7, None]}
    )

    assert "mcp__seya__get_workshop" in cfg.idempotent_tools
    assert "" not in cfg.idempotent_tools
    assert 7 not in cfg.idempotent_tools


def test_default_repeated_identical_failed_call_warns_without_blocking():
    controller = ToolCallGuardrailController()
    args = {"query": "same"}

    decisions = []
    for _ in range(5):
        assert controller.before_call("web_search", args).action == "allow"
        decisions.append(
            controller.after_call("web_search", args, '{"error":"boom"}', failed=True)
        )

    assert decisions[0].action == "allow"
    assert [d.action for d in decisions[1:]] == ["warn", "warn", "warn", "warn"]
    assert {d.code for d in decisions[1:]} == {"repeated_exact_failure_warning"}
    assert controller.before_call("web_search", args).action == "allow"
    assert controller.halt_decision is None


def test_hard_stop_enabled_blocks_repeated_exact_failure_before_next_execution():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            exact_failure_warn_after=2,
            exact_failure_block_after=2,
            same_tool_failure_halt_after=99,
        )
    )
    args = {"query": "same"}

    assert controller.before_call("web_search", args).action == "allow"
    first = controller.after_call("web_search", args, '{"error":"boom"}', failed=True)
    assert first.action == "allow"

    assert controller.before_call("web_search", args).action == "allow"
    second = controller.after_call("web_search", args, '{"error":"boom"}', failed=True)
    assert second.action == "warn"
    assert second.code == "repeated_exact_failure_warning"

    blocked = controller.before_call("web_search", args)
    assert blocked.action == "block"
    assert blocked.code == "repeated_exact_failure_block"
    assert blocked.count == 2














def test_mutating_or_unknown_tools_are_not_blocked_for_repeated_identical_success_output_by_default():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(no_progress_warn_after=2, no_progress_block_after=2)
    )

    for _ in range(3):
        assert controller.before_call("write_file", {"path": "/tmp/x", "content": "x"}).action == "allow"
        assert controller.after_call("write_file", {"path": "/tmp/x", "content": "x"}, "ok", failed=False).action == "allow"
        assert controller.before_call("custom_tool", {"x": 1}).action == "allow"
        assert controller.after_call("custom_tool", {"x": 1}, "ok", failed=False).action == "allow"






# ── Per-turn runaway-loop caps (Claude Code v2.1.212, Week 29) ──────────────

from agent.tool_guardrails import LoopCapConfig  # noqa: E402






def test_loop_cap_zero_disables_and_junk_falls_back():
    # 0 is a legitimate "unlimited" value; negatives / junk fall back to default.
    assert LoopCapConfig.from_mapping({"max_web_searches": 0}).max_web_searches == 0
    assert LoopCapConfig.from_mapping({"max_web_searches": -5}).max_web_searches == 50
    assert LoopCapConfig.from_mapping({"max_subagents": "nope"}).max_subagents == 50


def test_web_search_cap_blocks_after_limit_regardless_of_hard_stop():
    # Loop caps fire even with hard_stop_enabled=False (the per-turn loop
    # detector's flag). Each distinct query avoids the loop detector so we know
    # the block came from the loop cap, not exact-failure repetition.
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=False,
            loop_caps=LoopCapConfig(max_web_searches=3),
        )
    )
    for i in range(3):
        assert controller.before_call("web_search", {"query": f"q{i}"}).action == "allow"
    decision = controller.before_call("web_search", {"query": "q4"})
    assert decision.action == "block"
    assert decision.code == "loop_web_search_cap"
    assert decision.should_halt is True




