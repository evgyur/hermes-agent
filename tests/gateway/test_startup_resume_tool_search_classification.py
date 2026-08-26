"""Startup replay classification for immutable Tool Search wrapper calls."""

import json

from gateway.run import GatewayRunner


def _wrapped_call(inner_name: str, inner_args: dict, call_id: str) -> dict:
    wrapper_args = json.dumps(
        {"name": inner_name, "arguments": inner_args},
        ensure_ascii=False,
    )
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "tool_call",
                    "arguments": wrapper_args,
                },
            }
        ],
    }


def _rows(inner_name: str, *, completed: bool) -> list[dict]:
    rows = [
        {
            "role": "user",
            "content": "continue",
            "platform_message_id": "msg-42",
        },
        _wrapped_call(inner_name, {"q": "status"}, "wrapped-1"),
    ]
    if completed:
        rows.append(
            {
                "role": "tool",
                "tool_call_id": "wrapped-1",
                "content": "current status",
            }
        )
    return rows


def test_completed_wrapped_read_only_call_is_not_effect_fenced():
    runner = GatewayRunner.__new__(GatewayRunner)

    analysis = runner._analyze_startup_resume_rows(
        _rows("web_search", completed=True),
        source_message_id="msg-42",
    )

    assert analysis == {
        "disposition": "continue",
        "safe_dangling_calls": [],
        "effect_fence": {},
    }


def test_dangling_wrapped_read_only_call_is_safe_to_repeat():
    runner = GatewayRunner.__new__(GatewayRunner)

    analysis = runner._analyze_startup_resume_rows(
        _rows("web_search", completed=False),
        source_message_id="msg-42",
    )

    assert analysis == {
        "disposition": "continue",
        "safe_dangling_calls": [
            {"tool_call_id": "wrapped-1", "tool_name": "tool_call"}
        ],
        "effect_fence": {},
    }


def test_completed_wrapped_effect_keeps_raw_wrapper_replay_identity():
    runner = GatewayRunner.__new__(GatewayRunner)
    rows = _rows("mcp_deploy", completed=True)
    rows[-1]["effect_disposition"] = "completed"
    wrapper_args = rows[1]["tool_calls"][0]["function"]["arguments"]
    canonical_wrapper_args = json.dumps(
        json.loads(wrapper_args),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    analysis = runner._analyze_startup_resume_rows(
        rows,
        source_message_id="msg-42",
    )

    assert analysis["effect_fence"] == {
        ("tool_call", canonical_wrapper_args): "completed_effect_receipt"
    }
