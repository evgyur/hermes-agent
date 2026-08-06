"""Regression coverage for TurnRunner startup wiring."""

from pathlib import Path


def test_run_agent_inner_constructs_turn_context_before_use():
    run_path = Path(__file__).resolve().parents[2] / "gateway" / "run.py"
    source = run_path.read_text(encoding="utf-8")

    context_pos = source.find("turn_ctx = TurnContext(")
    runner_pos = source.find("turn_runner = TurnRunner(self, turn_ctx)")
    first_use_pos = source.find("turn_ctx._progress_metadata")

    assert context_pos >= 0
    assert runner_pos > context_pos
    assert first_use_pos > runner_pos
