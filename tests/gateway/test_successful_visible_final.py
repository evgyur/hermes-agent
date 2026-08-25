"""Release-A successful-final and bounded visible-final contract tests."""

import pytest

from gateway.visible_final import (
    extract_bounded_visible_final,
    is_successful_final,
)
from gateway.run import GatewayRunner


@pytest.fixture
def partial_assistant_rows():
    base = {"role": "assistant", "content": "partial", "finish_reason": "stop"}
    return [
        {**base, "finish_reason": None},
        {**base, "finish_reason": "incomplete"},
        {**base, "finish_reason": "length"},
        {**base, "turn_failed": 1},
        {**base, "turn_interrupted": 1},
        {**base, "turn_exit_reason": "verification_required"},
    ]


def test_nonempty_partial_or_interrupted_assistant_is_not_terminal(
    partial_assistant_rows,
):
    assert all(not is_successful_final(row) for row in partial_assistant_rows)

    runner = object.__new__(GatewayRunner)
    for row in partial_assistant_rows:
        analysis = runner._analyze_startup_resume_rows(
            [
                {"role": "user", "content": "A", "message_id": "a"},
                row,
            ],
            source_message_id="a",
        )
        assert analysis["disposition"] == "continue"


@pytest.fixture
def completed_empty_codex_final():
    return {
        "role": "assistant",
        "content": "",
        "finish_reason": "stop",
        "codex_message_items": [
            {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "status": "completed",
                "content": [{"type": "output_text", "text": ""}],
            }
        ],
    }


def test_completed_empty_codex_final_is_terminal_but_malformed_is_not(
    completed_empty_codex_final,
):
    assert is_successful_final(completed_empty_codex_final) is True

    runner = object.__new__(GatewayRunner)
    analysis = runner._analyze_startup_resume_rows(
        [
            {"role": "user", "content": "A", "message_id": "a"},
            completed_empty_codex_final,
        ],
        source_message_id="a",
    )
    assert analysis["disposition"] == "terminal_checkpoint"

    malformed = {
        **completed_empty_codex_final,
        "codex_message_items": [
            {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "status": "in_progress",
                "content": [{"type": "output_text", "text": "draft"}],
            }
        ],
    }
    assert is_successful_final(malformed) is False
    assert (
        is_successful_final(
            {"role": "assistant", "content": "", "finish_reason": "stop"}
        )
        is False
    )


@pytest.fixture
def noisy_visible_final_rows():
    return [
        {"role": "user", "content": "Do A", "message_id": "a"},
        {
            "id": 7,
            "role": "assistant",
            "content": "Deploy candidate A.",
            "finish_reason": "stop",
        },
        {
            "role": "assistant",
            "content": "watcher noise",
            "finish_reason": "stop",
            "internal": True,
        },
        {
            "role": "assistant",
            "content": "commentary noise",
            "finish_reason": "stop",
            "codex_message_items": [
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "ignore"}],
                }
            ],
        },
    ]


def test_extractor_uses_only_latest_real_user_turn_visible_successful_final(
    noisy_visible_final_rows,
):
    visible = extract_bounded_visible_final(noisy_visible_final_rows)
    assert visible is not None
    assert visible.row_id == 7
    assert visible.text == "Deploy candidate A."
    assert visible.source == "content"


def test_visible_non_synthetic_display_kind_remains_eligible():
    assert is_successful_final(
        {
            "role": "assistant",
            "content": "Visible final",
            "finish_reason": "stop",
            "display_kind": "assistant_final",
        }
    )
