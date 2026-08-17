from hermes_cli.goals import (
    CONTINUATION_PROMPT_GATE_FAILED_TEMPLATE,
    CONTINUATION_PROMPT_TEMPLATE,
    CONTINUATION_PROMPT_WITH_CONTRACT_TEMPLATE,
    CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE,
)


def test_all_goal_continuations_require_end_to_end_same_turn_execution():
    prompts = [
        CONTINUATION_PROMPT_TEMPLATE.format(goal="finish it"),
        CONTINUATION_PROMPT_WITH_CONTRACT_TEMPLATE.format(
            goal="finish it", contract_block="Outcome: done"
        ),
        CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
            goal="finish it", subgoals_block="1. survive restart"
        ),
        CONTINUATION_PROMPT_GATE_FAILED_TEMPLATE.format(
            goal="finish it",
            attempt=1,
            max_retries=3,
            command="pytest",
            exit_code=1,
            output="failed",
        ),
    ]

    for prompt in prompts:
        assert "keep taking every safe dependent step" in prompt
        assert "in this same turn" in prompt
        assert "Do not stop after one step" in prompt
        assert "Do not ask for approval the user already granted" in prompt
        assert "gateway restart or tool-turn boundary is not a blocker" in prompt
        assert "resume automatically after recovery" in prompt