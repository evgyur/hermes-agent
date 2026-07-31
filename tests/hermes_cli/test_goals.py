"""Tests for hermes_cli/goals.py — persistent cross-turn goals."""

from __future__ import annotations

import json
import time
from unittest.mock import patch, MagicMock

import pytest


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME so SessionDB.state_meta writes don't clobber the real one."""
    from pathlib import Path

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Bust the goal-module's DB cache for each test so it re-resolves HERMES_HOME.
    from hermes_cli import goals

    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


# ──────────────────────────────────────────────────────────────────────
# _parse_judge_response
# ──────────────────────────────────────────────────────────────────────


class TestParseJudgeResponse:
    def test_clean_json_done(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, _pf, wait = _parse_judge_response('{"done": true, "reason": "all good"}')
        assert verdict == "done"
        assert reason == "all good"
        assert wait is None

    def test_clean_json_continue(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, _pf, wait = _parse_judge_response('{"done": false, "reason": "more work needed"}')
        assert verdict == "continue"
        assert reason == "more work needed"
        assert wait is None

    def test_json_in_markdown_fence(self):
        from hermes_cli.goals import _parse_judge_response

        raw = '```json\n{"done": true, "reason": "done"}\n```'
        verdict, reason, _pf, _w = _parse_judge_response(raw)
        assert verdict == "done"
        assert "done" in reason

    def test_json_embedded_in_prose(self):
        """Some models prefix reasoning before emitting JSON — we extract it."""
        from hermes_cli.goals import _parse_judge_response

        raw = 'Looking at this... the agent says X. Verdict: {"done": false, "reason": "partial"}'
        verdict, reason, _pf, _w = _parse_judge_response(raw)
        assert verdict == "continue"
        assert reason == "partial"

    def test_string_done_values(self):
        from hermes_cli.goals import _parse_judge_response

        for s in ("true", "yes", "done", "1"):
            verdict, _, _, _ = _parse_judge_response(f'{{"done": "{s}", "reason": "r"}}')
            assert verdict == "done"
        for s in ("false", "no", "not yet"):
            verdict, _, _, _ = _parse_judge_response(f'{{"done": "{s}", "reason": "r"}}')
            assert verdict == "continue"

    def test_new_verdict_shape(self):
        """The explicit {"verdict": ...} shape is honored."""
        from hermes_cli.goals import _parse_judge_response

        v, _, _, _ = _parse_judge_response('{"verdict": "done", "reason": "r"}')
        assert v == "done"
        v, _, _, _ = _parse_judge_response('{"verdict": "continue", "reason": "r"}')
        assert v == "continue"

    def test_wait_verdict_with_pid(self):
        from hermes_cli.goals import _parse_judge_response

        v, reason, pf, wait = _parse_judge_response(
            '{"verdict": "wait", "wait_on_pid": 4242, "reason": "CI running"}'
        )
        assert v == "wait"
        assert pf is False
        assert wait == {"pid": 4242}
        assert reason == "CI running"

    def test_wait_verdict_with_seconds(self):
        from hermes_cli.goals import _parse_judge_response

        v, _, _, wait = _parse_judge_response(
            '{"verdict": "wait", "wait_for_seconds": 90, "reason": "rate limited"}'
        )
        assert v == "wait"
        assert wait == {"seconds": 90}

    def test_wait_verdict_without_target_downgrades_to_continue(self):
        """A wait verdict with no pid/seconds can't park on anything → continue."""
        from hermes_cli.goals import _parse_judge_response

        v, _, pf, wait = _parse_judge_response('{"verdict": "wait", "reason": "vague"}')
        assert v == "continue"
        assert wait is None
        assert pf is False

    def test_unknown_verdict_falls_back_to_continue(self):
        from hermes_cli.goals import _parse_judge_response

        v, _, _, _ = _parse_judge_response('{"verdict": "maybe", "reason": "r"}')
        assert v == "continue"

    def test_malformed_json_fails_open(self):
        """Non-JSON → continue + parse_failed, with error-ish reason."""
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, parse_failed, _w = _parse_judge_response("this is not json at all")
        assert verdict == "continue"
        assert parse_failed is True
        assert reason  # non-empty

    def test_empty_response(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, parse_failed, _w = _parse_judge_response("")
        assert verdict == "continue"
        assert parse_failed is True
        assert reason


# ──────────────────────────────────────────────────────────────────────
# judge_goal — fail-open semantics
# ──────────────────────────────────────────────────────────────────────


class TestJudgeGoal:
    def test_empty_goal_skipped(self):
        from hermes_cli.goals import judge_goal

        verdict, _, _, _wd, _tf = judge_goal("", "some response")
        assert verdict == "skipped"

    def test_empty_response_continues(self):
        from hermes_cli.goals import judge_goal

        verdict, _, _, _wd, _tf = judge_goal("ship the thing", "")
        assert verdict == "continue"

    def test_no_aux_client_continues(self):
        """Fail-open: if no aux client, we must return continue, not skipped/done."""
        from hermes_cli import goals

        with patch(
            "agent.auxiliary_client.call_llm",
            side_effect=RuntimeError("No LLM provider configured"),
        ):
            verdict, _, _, _wd, _tf = goals.judge_goal("my goal", "my response")
        assert verdict == "continue"

    def test_api_error_continues(self):
        """Judge exception → fail-open continue (don't wedge progress on judge bugs)."""
        from hermes_cli import goals

        with patch(
            "agent.auxiliary_client.call_llm",
            side_effect=RuntimeError("boom"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal("goal", "response")
        assert verdict == "continue"
        assert "judge error" in reason.lower()

    def test_judge_says_done(self):
        from hermes_cli import goals

        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"done": true, "reason": "achieved"}'))]
            ),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal("goal", "agent response")
        assert verdict == "done"
        assert reason == "achieved"

    def test_judge_says_continue(self):
        from hermes_cli import goals

        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"done": false, "reason": "not yet"}'))]
            ),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal("goal", "agent response")
        assert verdict == "continue"
        assert reason == "not yet"

    def test_supergoal_requires_terminal_markers_before_judge(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": true, "reason": "generic done"}')
                )
            ]
        )
        goal = "Run phases, then print AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=fake_client.chat.completions.create.return_value,
        ) as mock_call:
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "Phase 0 done, continuing")
        assert verdict == "continue"
        assert "SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_terminal_marker_prefix_stops_without_aux_judge(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": false, "reason": "keep going"}')
                )
            ]
        )
        goal = "Run phases; finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        response = (
            "SUPERGOAL_RUN_COMPLETE — уже закрыто: AUDIT_COMPLETE, "
            "Status: COMPLETE. Стоп."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, response)
        assert verdict == "done"
        assert "SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_positive_marker_status_lines_are_standalone_markers(self):
        from hermes_cli.goal_policies import has_standalone_marker

        assert has_standalone_marker("AUDIT_COMPLETE: yes", "AUDIT_COMPLETE")
        assert has_standalone_marker("**SUPERGOAL_RUN_COMPLETE: true**", "SUPERGOAL_RUN_COMPLETE")
        assert not has_standalone_marker("Goal complete: no", "Goal complete")

    def test_supergoal_negative_terminal_status_line_does_not_stop(self):
        from hermes_cli import goals

        goal = "Run phases; finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        response = "AUDIT_COMPLETE: yes\nSUPERGOAL_RUN_COMPLETE: no\nGoal complete: no"
        result = goals.judge_goal(goal, response)
        verdict, reason = result[0], result[1]
        assert verdict == "continue"
        assert "standalone SUPERGOAL_RUN_COMPLETE" in reason

    def test_supergoal_nested_state_status_run_complete_reconciles_without_llm_turn(self, hermes_home, tmp_path):
        from hermes_cli.goals import GoalManager

        package = tmp_path / "repo" / ".supergoal" / "trader20-security-hardening-v1"
        package.mkdir(parents=True)
        (package / "STATE.md").write_text(
            "# STATE\n"
            "Status: SUPERGOAL_RUN_COMPLETE\n"
            "Current phase: 12\n"
            "Release: 20260707T011841Z-dd852560a176\n",
            encoding="utf-8",
        )

        mgr = GoalManager("supergoal-nested-state-status")
        mgr.set(
            f"Execute the disk-backed SuperGoal package in `{package}`. "
            "Completion requires AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )

        decision = mgr.reconcile_structured_completion_from_state()
        assert decision is not None
        assert decision["status"] == "done"
        assert mgr.state is not None
        assert mgr.state.status == "done"
        assert "already audit-complete" in str(mgr.state.last_reason)

    def test_supergoal_nested_package_final_audit_report_is_allowed_base(self, hermes_home, tmp_path):
        from hermes_cli.goals import GoalManager

        package = tmp_path / "repo" / ".supergoal" / "nested-package"
        reports = package / "reports"
        reports.mkdir(parents=True)
        (package / "STATE.md").write_text(
            "# STATE\n"
            "Status: COMPLETE\n"
            "Current phase: DONE\n"
            "Final audit: reports/final-audit.md\n",
            encoding="utf-8",
        )
        (reports / "final-audit.md").write_text(
            "AUDIT_COMPLETE: yes\nSUPERGOAL_RUN_COMPLETE: yes\nGoal complete: yes\n",
            encoding="utf-8",
        )

        mgr = GoalManager("supergoal-nested-final-audit")
        mgr.set(
            f"Run `{package}` and stop only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )

        decision = mgr.reconcile_structured_completion_from_state()
        assert decision is not None
        assert decision["status"] == "done"
        assert mgr.state is not None
        assert mgr.state.status == "done"
        assert "already complete" in str(mgr.state.last_reason)

    def test_supergoal_inline_marker_mentions_do_not_satisfy_guard(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": true, "reason": "too lenient"}')
                )
            ]
        )
        goal = "Run phases; finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        response = (
            "Reply with this fallback command:\n"
            "/goal Run the plan; finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, response)
        assert verdict == "continue"
        assert "standalone SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_markers_inside_code_fence_do_not_satisfy_guard(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": true, "reason": "too lenient"}')
                )
            ]
        )
        goal = "Run phases; finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        response = "Example only:\n```\nAUDIT_COMPLETE\nSUPERGOAL_RUN_COMPLETE\n```"
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, response)
        assert verdict == "continue"
        assert "standalone SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_failure_handoff_stops_without_aux_judge(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": false, "reason": "keep going"}')
                )
            ]
        )
        goal = "Run phases; on missing credentials use FAILURE_HANDOFF; finish after SUPERGOAL_RUN_COMPLETE."
        response = "FAILURE_HANDOFF\nSTATE.md updated to BLOCKED."
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, response)
        assert verdict == "done"
        assert "FAILURE_HANDOFF" in reason
        fake_client.chat.completions.create.assert_not_called()

    @pytest.mark.parametrize(
        ("marker", "response"),
        [
            ("FAILURE_HANDOFF", "FAILURE_HANDOFF: missing provider credentials\nSTATE.md updated to BLOCKED."),
            ("AUDIT_HANDOFF", "AUDIT_HANDOFF — deliverable gap remains\nSTATE.md updated to AUDIT."),
        ],
    )
    def test_supergoal_handoff_prefix_stops_without_aux_judge(self, marker, response):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": false, "reason": "keep going"}')
                )
            ]
        )
        goal = f"Run phases; on blocker use {marker}; finish after SUPERGOAL_RUN_COMPLETE."
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, response)
        assert verdict == "done"
        assert marker in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_blocked_by_approval_stops_without_aux_judge(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": false, "reason": "keep going"}')
                )
            ]
        )
        goal = "Run phases; print AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE only after all approved live actions."
        response = (
            "BLOCKED_BY_APPROVAL — phase 7.\n"
            "STATE.md confirms Current phase: 7 and missing exact approval."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, response)
        assert verdict == "done"
        assert "BLOCKED_BY_APPROVAL" in reason
        fake_client.chat.completions.create.assert_not_called()

    @pytest.mark.parametrize(
        "response",
        [
            "blocked: нужен explicit approval.\nSafe-lane complete; live rollout requires approval.",
            "SUPERGOAL_TURN_YIELD — blocked by approval gate.\nGoal complete: no",
            "Goal complete: no\nBlocked: yes\nNeed user input: explicit approval for prod deploy.",
        ],
    )
    def test_supergoal_human_approval_blocker_cards_stop_without_aux_judge(self, response):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": false, "reason": "keep going"}')
                )
            ]
        )
        goal = (
            "Execute `.supergoal/PROTOCOL.md`; preserve public discovery; "
            "stop for explicit approval before prod deploy; finish after "
            "AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, response)
        assert verdict == "done"
        assert "BLOCKED_BY_APPROVAL" in reason
        fake_client.chat.completions.create.assert_not_called()

    @pytest.mark.parametrize(
        "response",
        [
            "I will not use BLOCKED_BY_APPROVAL unless approval is missing; continuing phase work.",
            "Example only:\n```\nBLOCKED_BY_APPROVAL — phase 7\n```",
        ],
    )
    def test_supergoal_blocked_by_approval_mentions_do_not_stop(self, response):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": true, "reason": "too lenient"}')
                )
            ]
        )
        goal = "Run phases; print AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE after approval."
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, response)
        assert verdict == "continue"
        assert "SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_completed_state_file_stops_without_aux_judge(self, tmp_path):
        from hermes_cli import goals

        root = tmp_path / "done-supergoal"
        sg = root / ".supergoal"
        sg.mkdir(parents=True)
        (sg / "STATE.md").write_text(
            "# STATE\n"
            "Status: DONE\n"
            "Current phase: DONE\n"
            "AUDIT_COMPLETE\n"
            "SUPERGOAL_RUN_COMPLETE\n",
            encoding="utf-8",
        )
        fake_client = MagicMock()
        goal = (
            f"Execute the Supergoal from project root `{root}`. "
            f"Use `{root}/.supergoal/AUDIT_HANDOFF.md/PROTOCOL.md` and finish "
            "only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "COMPLETE — stop.")
        assert verdict == "done"
        assert "STATE.md already complete" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_status_snapshot_completed_state_file_stops_without_aux_judge(self, tmp_path):
        from hermes_cli import goals

        root = tmp_path / "done-supergoal-snapshot"
        sg = root / ".supergoal"
        sg.mkdir(parents=True)
        (sg / "STATE.md").write_text(
            "# STATE — demo\n"
            "Current phase: DONE\n"
            "Status snapshot: DONE — all phases and final audit complete\n"
            "Phase 5 complete.\n"
            "AUDIT_COMPLETE\n"
            "SUPERGOAL_RUN_COMPLETE\n",
            encoding="utf-8",
        )
        fake_client = MagicMock()
        goal = (
            f"From `{root}`, execute `{root}/.supergoal/PROTOCOL.md`, "
            f"`{root}/.supergoal/ROADMAP.md`, and `{root}/.supergoal/STATE.md`. "
            "Finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "Goal complete: yes. Останавливаюсь.")
        assert verdict == "done"
        assert "STATE.md already complete" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_nested_explicit_audit_complete_state_stops_without_aux_judge(self, tmp_path):
        from hermes_cli import goals

        root = tmp_path / "repo"
        package = root / ".supergoal" / "self-improving-copytrader"
        package.mkdir(parents=True)
        # Older root package state must not steal precedence from the explicit
        # nested STATE.md named in the goal body.
        (root / ".supergoal" / "STATE.md").write_text(
            "# unrelated root state\nCurrent phase: 4\nStatus: ACTIVE\n",
            encoding="utf-8",
        )
        (package / "STATE.md").write_text(
            "# Self-improving copy-trader state\n\n"
            "Status: `AUDIT_COMPLETE`\n"
            "Updated: 2026-07-05 15:45:47 UTC\n\n"
            "## Completed\n"
            "- SuperGoal phases 1-8 executed.\n"
            "- Tests: `36 passed`.\n",
            encoding="utf-8",
        )
        fake_client = MagicMock()
        goal = (
            f"Execute the Self-improving Hyperliquid copy-trader SuperGoal from `{package}`. "
            f"Load `{package}/PROTOCOL.md`, `{package}/STATE.md`, `{package}/ROADMAP.md`, "
            "then execute phases 1-8 continuously until AUDIT_COMPLETE or a real blocker."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "GOAL COMPLETE / AUDIT_COMPLETE. Останавливаюсь.")
        assert verdict == "done"
        assert "STATE.md already audit-complete" in reason
        assert str(package) in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_nested_package_root_audit_complete_state_stops_without_state_file_name(self, tmp_path):
        from hermes_cli import goals

        root = tmp_path / "repo"
        package = root / ".supergoal" / "hlcopy-self-improving-safe-autonomy-v2"
        package.mkdir(parents=True)
        (root / ".supergoal" / "STATE.md").write_text(
            "# unrelated root state\nStatus: ACTIVE\n",
            encoding="utf-8",
        )
        (package / "STATE.md").write_text(
            "# SuperGoal State\n\n"
            "Status: AUDIT_COMPLETE\n"
            "Updated: 2026-07-05T19:45:27Z\n",
            encoding="utf-8",
        )
        fake_client = MagicMock()
        goal = (
            f"Execute the SuperGoal package at `{package}` from `PROTOCOL.md`. "
            "Continue through all phases until AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE, or a real blocker."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "SUPERGOAL_RUN_COMPLETE — already closed.")
        assert verdict == "done"
        assert "STATE.md already audit-complete" in reason
        assert str(package) in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_markdown_state_with_external_final_audit_stops_without_aux_judge(self, tmp_path):
        from hermes_cli import goals

        root = tmp_path / "done-supergoal-heading-state"
        sg = root / ".supergoal"
        reports = sg / "reports"
        reports.mkdir(parents=True)
        (sg / "STATE.md").write_text(
            "# STATE.md\n\n"
            "## Current phase\n"
            "DONE\n\n"
            "## Status snapshot\n"
            "- DONE at 2026-06-25T19:47:43Z.\n"
            "- Final audit: `reports/final-audit.md`.\n",
            encoding="utf-8",
        )
        (reports / "final-audit.md").write_text(
            "AUDIT_COMPLETE\nSUPERGOAL_RUN_COMPLETE\nGoal complete: yes\n",
            encoding="utf-8",
        )
        fake_client = MagicMock()
        goal = (
            f"From the project root `{tmp_path}`, execute the SuperGoal in "
            f"`{root}/.supergoal` using `{root}/.supergoal/STATE.md`, "
            "and finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "COMPLETE — no-op.")
        assert verdict == "done"
        assert "STATE.md already complete" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_terminal_heading_without_final_audit_report_does_not_stop(self, tmp_path):
        from hermes_cli import goals

        root = tmp_path / "done-looking-but-no-audit"
        sg = root / ".supergoal"
        sg.mkdir(parents=True)
        (sg / "STATE.md").write_text(
            "# STATE.md\n\n## Current phase\nDONE\n",
            encoding="utf-8",
        )
        fake_client = MagicMock()
        goal = (
            f"Execute `{root}/.supergoal/STATE.md`; finish only after "
            "AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "COMPLETE — no-op.")
        assert verdict == "continue"
        assert "SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_markdown_markers_count_as_standalone_completion(self):
        from hermes_cli import goals

        goal = "Finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        response = "## AUDIT_COMPLETE\n\n- [x] SUPERGOAL_RUN_COMPLETE\n"
        verdict, reason, _, _wd, _tf = goals.judge_goal(goal, response)
        # The deterministic guard must not block markdown-rendered markers as
        # missing; with no auxiliary client configured this falls through to the
        # normal judge-unavailable path instead of returning the marker-missing
        # continue reason.
        assert verdict in {"continue", "skipped", "done"}
        assert "missing standalone" not in reason

    def test_supergoal_markdown_state_negated_marker_mentions_do_not_stop(self, tmp_path):
        from hermes_cli import goals

        root = tmp_path / "negated-markers"
        sg = root / ".supergoal"
        sg.mkdir(parents=True)
        (sg / "STATE.md").write_text(
            "# STATE\n\n## Current phase\nDONE\n\n"
            "Note: final audit is missing AUDIT_COMPLETE and "
            "SUPERGOAL_RUN_COMPLETE; do not stop.\n",
            encoding="utf-8",
        )
        fake_client = MagicMock()
        goal = f"Execute `{root}/.supergoal/STATE.md`; finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=fake_client.chat.completions.create.return_value,
        ) as mock_call:
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "COMPLETE — no-op.")
        assert verdict == "continue"
        assert "SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_external_absolute_final_audit_does_not_satisfy_state(self, tmp_path):
        from hermes_cli import goals

        root = tmp_path / "root"
        sg = root / ".supergoal"
        sg.mkdir(parents=True)
        outside = tmp_path / "outside" / "final-audit.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("AUDIT_COMPLETE\nSUPERGOAL_RUN_COMPLETE\n", encoding="utf-8")
        (sg / "STATE.md").write_text(
            f"# STATE\n\n## Current phase\nDONE\n\nFinal audit: `{outside}`\n",
            encoding="utf-8",
        )
        fake_client = MagicMock()
        goal = f"Execute `{root}/.supergoal/STATE.md`; finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "COMPLETE — no-op.")
        assert verdict == "continue"
        assert "SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_structured_done_with_subgoals_uses_aux_judge(self, tmp_path):
        from hermes_cli import goals

        root = tmp_path / "subgoal-terminal"
        sg = root / ".supergoal"
        sg.mkdir(parents=True)
        (sg / "STATE.md").write_text(
            "# STATE\nStatus: DONE\nCurrent phase: DONE\nAUDIT_COMPLETE\nSUPERGOAL_RUN_COMPLETE\n",
            encoding="utf-8",
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"done": false, "reason": "subgoal missing"}'))]
        )
        goal = f"Execute `{root}/.supergoal/STATE.md`; finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=fake_client.chat.completions.create.return_value,
        ) as mock_call:
            verdict, reason, _, _wd, _tf = goals.judge_goal(
                goal,
                "AUDIT_COMPLETE\nSUPERGOAL_RUN_COMPLETE\n",
                subgoals=["also provide docs evidence"],
            )
        assert verdict == "continue"
        assert reason == "subgoal missing"
        mock_call.assert_called_once()

    def test_supergoal_terminal_state_without_final_markers_does_not_stop(self, tmp_path):
        from hermes_cli import goals

        root = tmp_path / "malformed-supergoal"
        sg = root / ".supergoal"
        sg.mkdir(parents=True)
        (sg / "STATE.md").write_text(
            "# STATE\nStatus: DONE\nCurrent phase: DONE\n",
            encoding="utf-8",
        )
        fake_client = MagicMock()
        goal = (
            f"Execute the Supergoal from project root `{root}`. "
            "Finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )
        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=fake_client.chat.completions.create.return_value,
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "COMPLETE — stop.")
        assert verdict == "continue"
        assert "SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_relative_state_file_completion_uses_current_root(self, tmp_path, monkeypatch):
        from hermes_cli import goals

        sg = tmp_path / ".supergoal"
        sg.mkdir(parents=True)
        (sg / "STATE.md").write_text(
            "# STATE\nStatus: DONE\nCurrent phase: DONE\nAUDIT_COMPLETE\nSUPERGOAL_RUN_COMPLETE\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        fake_client = MagicMock()
        goal = "Use `.supergoal/STATE.md`; finish after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "COMPLETE — stop.")
        assert verdict == "done"
        assert "STATE.md already complete" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_relative_state_does_not_steal_explicit_root(self, tmp_path, monkeypatch):
        from hermes_cli import goals

        cwd = tmp_path / "cwd"
        current = tmp_path / "current"
        for root, state in [
            (cwd, "# STATE\nStatus: DONE\nCurrent phase: DONE\nAUDIT_COMPLETE\nSUPERGOAL_RUN_COMPLETE\n"),
            (current, "# STATE\nStatus: READY\nCurrent phase: 0\n"),
        ]:
            sg = root / ".supergoal"
            sg.mkdir(parents=True)
            (sg / "STATE.md").write_text(state, encoding="utf-8")
        monkeypatch.chdir(cwd)
        fake_client = MagicMock()
        goal = (
            f"Implement `.supergoal/STATE.md` in `{current}`. "
            "Finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "COMPLETE — stop.")
        assert verdict == "continue"
        assert "SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_incomplete_current_root_not_satisfied_by_previous_complete_root(self, tmp_path):
        from hermes_cli import goals

        current = tmp_path / "live-activation-supergoal"
        old = tmp_path / "old-complete-supergoal"
        for root, state in [
            (
                current,
                "# STATE\nStatus: READY\nCurrent phase: 0\n",
            ),
            (
                old,
                "# STATE\nStatus: COMPLETE\nCurrent phase: complete\nAUDIT_COMPLETE\nSUPERGOAL_RUN_COMPLETE\n",
            ),
        ]:
            sg = root / ".supergoal"
            sg.mkdir(parents=True)
            (sg / "STATE.md").write_text(state, encoding="utf-8")

        fake_client = MagicMock()
        goal = (
            f"Implement `.supergoal/ROADMAP.md` in `{current}` from phase 0. "
            f"Previous rail is `{old}`. Finish only after AUDIT_COMPLETE and "
            "SUPERGOAL_RUN_COMPLETE."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "COMPLETE — stop.")
        assert verdict == "continue"
        assert "SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()


    def test_supergoal_explicit_root_wins_over_older_direct_completed_state_path(self, tmp_path):
        from hermes_cli import goals

        current = tmp_path / "current-supergoal"
        old = tmp_path / "old-supergoal"
        for root, state in [
            (
                current,
                "# STATE\nStatus: READY\nCurrent phase: 0\n",
            ),
            (
                old,
                "# STATE\nStatus: COMPLETE\nCurrent phase: DONE\nAUDIT_COMPLETE\nSUPERGOAL_RUN_COMPLETE\n",
            ),
        ]:
            sg = root / ".supergoal"
            sg.mkdir(parents=True)
            (sg / "STATE.md").write_text(state, encoding="utf-8")

        fake_client = MagicMock()
        goal = (
            f"Previous rail: `{old}/.supergoal/STATE.md`. "
            f"Execute the Supergoal from project root `{current}`. "
            "Finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, "COMPLETE — stop.")
        assert verdict == "continue"
        assert "SUPERGOAL_RUN_COMPLETE" in reason
        fake_client.chat.completions.create.assert_not_called()

    def test_supergoal_with_terminal_markers_can_reach_judge(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": true, "reason": "markers present"}')
                )
            ]
        )
        goal = "Run phases, then print AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        response = "AUDIT_COMPLETE\nSUPERGOAL_RUN_COMPLETE"
        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=fake_client.chat.completions.create.return_value,
        ):
            verdict, reason, _, _wd, _tf = goals.judge_goal(goal, response)
        assert verdict == "done"
        assert reason == "markers present"


# ──────────────────────────────────────────────────────────────────────
# GoalManager lifecycle + persistence
# ──────────────────────────────────────────────────────────────────────


class TestGoalManager:
    def test_no_goal_initial(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-1")
        assert mgr.state is None
        assert not mgr.is_active()
        assert not mgr.has_goal()
        assert "No active goal" in mgr.status_line()

    def test_set_then_status(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-2", default_max_turns=5)
        state = mgr.set("port the thing")
        assert state.goal == "port the thing"
        assert state.status == "active"
        assert state.max_turns == 5
        assert state.turns_used == 0
        assert mgr.is_active()
        assert "active" in mgr.status_line().lower()
        assert "port the thing" in mgr.status_line()

    def test_goal_manager_reconciles_done_supergoal_before_continuation(self, hermes_home, tmp_path):
        from hermes_cli.goals import GoalManager

        root = tmp_path / "completed-package"
        sg = root / ".supergoal"
        sg.mkdir(parents=True)
        (sg / "STATE.md").write_text(
            "# STATE\n"
            "Current phase: DONE\n"
            "Status snapshot: DONE — all phases and final audit complete\n"
            "AUDIT_COMPLETE\n"
            "SUPERGOAL_RUN_COMPLETE\n",
            encoding="utf-8",
        )
        mgr = GoalManager(session_id="already-done-supergoal")
        mgr.set(
            f"From `{root}`, execute `{root}/.supergoal/STATE.md`; "
            "finish only after AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )

        assert mgr.next_continuation_prompt() is None
        assert mgr.state is not None
        assert mgr.state.status == "done"
        assert "STATE.md already complete" in (mgr.state.last_reason or "")
        assert not GoalManager(session_id="already-done-supergoal").is_active()

    def test_goal_manager_reconciles_nested_audit_complete_state_before_continuation(self, hermes_home, tmp_path):
        from hermes_cli.goals import GoalManager

        root = tmp_path / "repo"
        package = root / ".supergoal" / "nested-package"
        package.mkdir(parents=True)
        (root / ".supergoal" / "STATE.md").write_text(
            "# root state\nStatus: ACTIVE\n",
            encoding="utf-8",
        )
        (package / "STATE.md").write_text(
            "# STATE\nStatus: `AUDIT_COMPLETE`\n",
            encoding="utf-8",
        )
        mgr = GoalManager(session_id="already-audit-complete-nested-supergoal")
        mgr.set(
            f"Execute nested SuperGoal from `{package}`. Load `{package}/STATE.md`; "
            "continue until AUDIT_COMPLETE or a real blocker."
        )

        assert mgr.next_continuation_prompt() is None
        assert mgr.state is not None
        assert mgr.state.status == "done"
        assert "STATE.md already audit-complete" in (mgr.state.last_reason or "")

    def test_goal_manager_reconciles_nested_package_root_before_continuation(self, hermes_home, tmp_path):
        from hermes_cli.goals import GoalManager

        root = tmp_path / "repo"
        package = root / ".supergoal" / "hlcopy-self-improving-safe-autonomy-v2"
        package.mkdir(parents=True)
        (root / ".supergoal" / "STATE.md").write_text(
            "# root state\nStatus: ACTIVE\n",
            encoding="utf-8",
        )
        (package / "STATE.md").write_text(
            "# STATE\nStatus: AUDIT_COMPLETE\n",
            encoding="utf-8",
        )
        mgr = GoalManager(session_id="already-audit-complete-package-root")
        mgr.set(
            f"Execute the SuperGoal package at `{package}` from `PROTOCOL.md`. "
            "Continue until AUDIT_COMPLETE and SUPERGOAL_RUN_COMPLETE."
        )

        assert mgr.next_continuation_prompt() is None
        assert mgr.state is not None
        assert mgr.state.status == "done"
        assert "STATE.md already audit-complete" in (mgr.state.last_reason or "")

    def test_set_rejects_empty(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-3")
        with pytest.raises(ValueError):
            mgr.set("")
        with pytest.raises(ValueError):
            mgr.set("   ")

    def test_pause_and_resume(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-4")
        mgr.set("goal text")
        mgr.pause(reason="user-paused")
        assert mgr.state.status == "paused"
        assert not mgr.is_active()
        assert mgr.has_goal()

        mgr.resume()
        assert mgr.state.status == "active"
        assert mgr.is_active()

    def test_clear(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-5")
        mgr.set("goal")
        mgr.clear()
        assert mgr.state is None
        assert not mgr.is_active()

    def test_persistence_across_managers(self, hermes_home):
        """Key invariant: a second manager on the same session sees the goal.

        This is what makes /resume work — each session rebinds its
        GoalManager and picks up the saved state.
        """
        from hermes_cli.goals import GoalManager

        mgr1 = GoalManager(session_id="persist-sid")
        mgr1.set("do the thing")

        mgr2 = GoalManager(session_id="persist-sid")
        assert mgr2.state is not None
        assert mgr2.state.goal == "do the thing"
        assert mgr2.is_active()

    def test_evaluate_after_turn_done(self, hermes_home):
        """Judge says done → status=done, no continuation."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-1")
        mgr.set("ship it")

        with patch.object(goals, "judge_goal", return_value=("done", "shipped", False, None, False)):
            decision = mgr.evaluate_after_turn("I shipped the feature.")

        assert decision["verdict"] == "done"
        assert decision["should_continue"] is False
        assert decision["continuation_prompt"] is None
        assert mgr.state.status == "done"
        assert mgr.state.turns_used == 1

    def test_evaluate_after_turn_supergoal_handoff_is_blocked_not_achieved(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-blocked")
        mgr.set("Run Supergoal and stop with FAILURE_HANDOFF if credentials are missing.")

        with patch.object(
            goals,
            "judge_goal",
            return_value=("done", "supergoal stopped with FAILURE_HANDOFF", False),
        ):
            decision = mgr.evaluate_after_turn("FAILURE_HANDOFF")

        assert decision["verdict"] == "done"
        assert decision["status"] == "blocked"
        assert decision["should_continue"] is False
        assert decision["continuation_prompt"] is None
        assert "Goal stopped" in decision["message"]
        assert "Goal achieved" not in decision["message"]
        assert mgr.state is not None
        assert mgr.state.status == "blocked"
        assert "Goal blocked" in mgr.status_line()

    def test_evaluate_after_turn_supergoal_approval_block_is_blocked(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-approval-blocked")
        mgr.set("Run Supergoal; stop with BLOCKED_BY_APPROVAL if approval is missing.")

        with patch.object(
            goals,
            "judge_goal",
            return_value=("done", "supergoal stopped with BLOCKED_BY_APPROVAL", False),
        ):
            decision = mgr.evaluate_after_turn("BLOCKED_BY_APPROVAL")

        assert decision["verdict"] == "done"
        assert decision["status"] == "blocked"
        assert decision["should_continue"] is False
        assert decision["continuation_prompt"] is None
        assert mgr.state is not None
        assert mgr.state.status == "blocked"
        assert "BLOCKED_BY_APPROVAL" in (mgr.state.last_reason or "")

    def test_active_goal_migrates_to_compression_child(self, hermes_home):
        from hermes_cli.goals import GoalManager
        from hermes_state import SessionDB

        db = SessionDB()
        db.create_session("goal-parent", source="telegram")
        db.create_session("goal-child", source="telegram", parent_session_id="goal-parent")
        db.end_session("goal-parent", "compression")

        parent = GoalManager(session_id="goal-parent")
        parent.set("ship through the official goal loop")

        child = GoalManager(session_id="goal-child")

        assert child.state is not None
        assert child.state.status == "active"
        assert child.state.goal == "ship through the official goal loop"
        parent_state = GoalManager(session_id="goal-parent").state
        assert parent_state is not None
        assert parent_state.status == "cleared"

    def test_goal_does_not_migrate_across_non_compression_parent(self, hermes_home):
        from hermes_cli.goals import GoalManager
        from hermes_state import SessionDB

        db = SessionDB()
        db.create_session("branch-parent", source="telegram")
        db.create_session("branch-child", source="telegram", parent_session_id="branch-parent")
        db.end_session("branch-parent", "branch")

        GoalManager(session_id="branch-parent").set("do not inherit this")

        assert GoalManager(session_id="branch-child").state is None
        parent_state = GoalManager(session_id="branch-parent").state
        assert parent_state is not None
        assert parent_state.status == "active"

    def test_evaluate_after_turn_continue_under_budget(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-2", default_max_turns=5)
        mgr.set("a long goal")

        with patch.object(goals, "judge_goal", return_value=("continue", "more work", False, None, False)):
            decision = mgr.evaluate_after_turn("made some progress")

        assert decision["verdict"] == "continue"
        assert decision["should_continue"] is True
        assert decision["continuation_prompt"] is not None
        assert "a long goal" in decision["continuation_prompt"]
        assert mgr.state.status == "active"
        assert mgr.state.turns_used == 1

    def test_technical_iteration_boundary_forces_fresh_goal_cycle(self, hermes_home):
        """A per-turn tool cap is a checkpoint, never a completion verdict."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-technical-boundary", default_max_turns=5)
        mgr.set("finish the production repair")

        with patch.object(goals, "judge_goal") as judge:
            decision = mgr.evaluate_after_turn(
                "A partial summary that could be misread as terminal.",
                technical_boundary="max_iterations_reached(200/200)",
            )

        judge.assert_not_called()
        assert decision["verdict"] == "continue"
        assert decision["should_continue"] is True
        assert "finish the production repair" in decision["continuation_prompt"]
        assert "technical iteration limit" in decision["reason"]
        assert mgr.state.status == "active"
        assert mgr.state.turns_used == 1

    def test_technical_iteration_boundary_still_respects_goal_turn_budget(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-technical-budget", default_max_turns=1)
        mgr.set("bounded goal")

        with patch.object(goals, "judge_goal") as judge:
            decision = mgr.evaluate_after_turn(
                "Partial work.",
                technical_boundary="max_iterations_reached(200/200)",
            )

        judge.assert_not_called()
        assert decision["should_continue"] is False
        assert decision["status"] == "paused"
        assert mgr.state.turns_used == 1
        assert "budget" in (mgr.state.paused_reason or "").lower()

    def test_evaluate_after_turn_budget_exhausted(self, hermes_home):
        """When turn budget hits ceiling, auto-pause instead of continuing."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-3", default_max_turns=2)
        mgr.set("hard goal")

        with patch.object(goals, "judge_goal", return_value=("continue", "not yet", False, None, False)):
            d1 = mgr.evaluate_after_turn("step 1")
            assert d1["should_continue"] is True
            assert mgr.state.turns_used == 1
            assert mgr.state.status == "active"

            d2 = mgr.evaluate_after_turn("step 2")
            # turns_used is now 2 which equals max_turns → paused
            assert d2["should_continue"] is False
            assert mgr.state.status == "paused"
            assert mgr.state.turns_used == 2
            assert "budget" in (mgr.state.paused_reason or "").lower()

    def test_evaluate_after_turn_inactive(self, hermes_home):
        """evaluate_after_turn is a no-op when goal isn't active."""
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-4")
        d = mgr.evaluate_after_turn("anything")
        assert d["verdict"] == "inactive"
        assert d["should_continue"] is False

        mgr.set("a goal")
        mgr.pause()
        d2 = mgr.evaluate_after_turn("anything")
        assert d2["verdict"] == "inactive"
        assert d2["should_continue"] is False


    def test_stale_manager_does_not_repeat_done_notice(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        active = GoalManager(session_id="eval-stale-done")
        active.set("finish once")

        stale = GoalManager(session_id="eval-stale-done")
        active.mark_done("already finished")

        with patch.object(goals, "judge_goal") as judge:
            decision = stale.evaluate_after_turn("Goal complete: yes")

        judge.assert_not_called()
        assert decision["verdict"] == "inactive"
        assert decision["should_continue"] is False
        assert decision["message"] == ""

    def test_continuation_prompt_shape(self, hermes_home):
        """The continuation prompt must include the goal text verbatim —
        and must be safe to inject as a user-role message (prompt-cache
        invariants: no system-prompt mutation)."""
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="cont-sid")
        mgr.set("port goal command to hermes")
        prompt = mgr.next_continuation_prompt()
        assert prompt is not None
        assert "port goal command to hermes" in prompt
        assert prompt.strip()  # non-empty


# ──────────────────────────────────────────────────────────────────────
# Smoke: CommandDef is wired
# ──────────────────────────────────────────────────────────────────────


def test_goal_command_in_registry():
    from hermes_cli.commands import resolve_command

    cmd = resolve_command("goal")
    assert cmd is not None
    assert cmd.name == "goal"


def test_goal_command_dispatches_in_cli_registry_helpers():
    """goal shows up in autocomplete / help categories alongside other Session cmds."""
    from hermes_cli.commands import COMMANDS, COMMANDS_BY_CATEGORY

    assert "/goal" in COMMANDS
    session_cmds = COMMANDS_BY_CATEGORY.get("Session", {})
    assert "/goal" in session_cmds


# ──────────────────────────────────────────────────────────────────────
# Auto-pause on consecutive judge parse failures
# ──────────────────────────────────────────────────────────────────────


class TestJudgeParseFailureAutoPause:
    """Regression: weak judge models (e.g. deepseek-v4-flash) that return
    empty strings or non-JSON prose must auto-pause the loop after N turns
    instead of burning the whole turn budget."""

    def test_parse_response_flags_empty_as_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, parse_failed, _w = _parse_judge_response("")
        assert verdict == "continue"
        assert parse_failed is True
        assert "empty" in reason.lower()

    def test_parse_response_flags_non_json_as_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, parse_failed, _w = _parse_judge_response(
            "Let me analyze whether the goal is fully satisfied based on the agent's response..."
        )
        assert verdict == "continue"
        assert parse_failed is True
        assert "not json" in reason.lower()

    def test_parse_response_clean_json_is_not_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, _, parse_failed, _w = _parse_judge_response(
            '{"done": false, "reason": "more work"}'
        )
        assert verdict == "continue"
        assert parse_failed is False

    def test_api_error_does_not_count_as_parse_failure(self):
        """Transient network/API errors must not trip the auto-pause guard."""
        from hermes_cli import goals

        with patch(
            "agent.auxiliary_client.call_llm",
            side_effect=RuntimeError("connection reset"),
        ):
            verdict, _, parse_failed, _wd, transport_failed = goals.judge_goal(
                "goal", "response"
            )
        assert verdict == "continue"
        assert parse_failed is False
        assert transport_failed is True

    def test_empty_judge_reply_flagged_as_parse_failure(self):
        """End-to-end: judge returns empty content → parse_failed=True."""
        from hermes_cli import goals

        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=""))]),
        ):
            verdict, _, parse_failed, _wd, _tf = goals.judge_goal("goal", "response")
        assert verdict == "continue"
        assert parse_failed is True

    def test_auto_pause_after_three_consecutive_parse_failures(self, hermes_home):
        """N=3 consecutive parse failures → auto-pause with config pointer."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager, DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES

        assert DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES == 3
        mgr = GoalManager(session_id="parse-fail-sid-1", default_max_turns=20)
        mgr.set("do a thing")

        with patch.object(
            goals, "judge_goal", return_value=("continue", "judge returned empty response", True, None, False)
        ):
            d1 = mgr.evaluate_after_turn("step 1")
            assert d1["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 1

            d2 = mgr.evaluate_after_turn("step 2")
            assert d2["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 2

            d3 = mgr.evaluate_after_turn("step 3")
            assert d3["should_continue"] is False
            assert d3["status"] == "paused"
            assert mgr.state.consecutive_parse_failures == 3
            # Message points at the config surface so the user can fix it.
            assert "auxiliary" in d3["message"]
            assert "goal_judge" in d3["message"]
            assert "config.yaml" in d3["message"]

    def test_parse_failure_counter_resets_on_good_reply(self, hermes_home):
        """A single good judge reply resets the counter — transient flakes don't pause."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="parse-fail-sid-2", default_max_turns=20)
        mgr.set("another goal")

        # Two parse failures…
        with patch.object(
            goals, "judge_goal", return_value=("continue", "not json", True, None, False)
        ):
            mgr.evaluate_after_turn("step 1")
            mgr.evaluate_after_turn("step 2")
            assert mgr.state.consecutive_parse_failures == 2

        # …then one clean reply resets the counter.
        with patch.object(
            goals, "judge_goal", return_value=("continue", "making progress", False, None, False)
        ):
            d = mgr.evaluate_after_turn("step 3")
            assert d["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 0

    def test_transport_failures_do_not_increment_parse_counter(self, hermes_home):
        """Transport failures use their own counter and a good reply resets both."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="parse-fail-sid-3", default_max_turns=20)
        mgr.set("goal")
        assert mgr.state is not None

        with patch.object(
            goals,
            "judge_goal",
            return_value=(
                "continue",
                "judge error: RuntimeError",
                False,
                None,
                True,
            ),
        ):
            for _ in range(2):
                d = mgr.evaluate_after_turn("still going")
                assert d["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 0
            assert mgr.state.consecutive_transport_failures == 2
            assert mgr.state.status == "active"

        with patch.object(
            goals,
            "judge_goal",
            return_value=("continue", "making progress", False, None, False),
        ):
            d = mgr.evaluate_after_turn("recovered")

        assert d["should_continue"] is True
        assert mgr.state.consecutive_parse_failures == 0
        assert mgr.state.consecutive_transport_failures == 0

    def test_consecutive_parse_failures_persists_across_goalmanager_reloads(
        self, hermes_home
    ):
        """The counter must be durable so cross-session resumes see it."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager, load_goal

        mgr = GoalManager(session_id="parse-fail-sid-4", default_max_turns=20)
        mgr.set("persistent goal")

        with patch.object(
            goals, "judge_goal", return_value=("continue", "empty", True, None, False)
        ):
            mgr.evaluate_after_turn("r")
            mgr.evaluate_after_turn("r")

        reloaded = load_goal("parse-fail-sid-4")
        assert reloaded is not None
        assert reloaded.consecutive_parse_failures == 2


# ──────────────────────────────────────────────────────────────────────
# /subgoal — user-added criteria
# ──────────────────────────────────────────────────────────────────────


class TestGoalStateSubgoalsBackcompat:
    def test_old_state_meta_row_loads_without_subgoals(self):
        """A goal serialized BEFORE the subgoals field existed must
        round-trip with an empty list, not crash."""
        from hermes_cli.goals import GoalState

        legacy = json.dumps({
            "goal": "do a thing",
            "status": "active",
            "turns_used": 2,
            "max_turns": 20,
            "created_at": 1.0,
            "last_turn_at": 2.0,
            "consecutive_parse_failures": 0,
        })
        state = GoalState.from_json(legacy)
        assert state.goal == "do a thing"
        assert state.subgoals == []

    def test_subgoals_round_trip(self):
        from hermes_cli.goals import GoalState
        state = GoalState(goal="g", subgoals=["a", "b", "c"])
        rt = GoalState.from_json(state.to_json())
        assert rt.subgoals == ["a", "b", "c"]


class TestMigrateGoalToSession:
    """migrate_goal_to_session carries a /goal from a parent session to its
    compression continuation child (#33618). load_goal does a flat
    per-session lookup with no lineage walk, so without migration an active
    goal silently dies when compression rotates session_id."""

    def test_migrates_active_goal_to_child(self, hermes_home):
        from hermes_cli.goals import save_goal, load_goal, migrate_goal_to_session, GoalState
        save_goal("parent-sid", GoalState(goal="ship the feature"))
        assert migrate_goal_to_session("parent-sid", "child-sid", reason="compression") is True
        child = load_goal("child-sid")
        assert child is not None and child.goal == "ship the feature"
        # Parent row archived (cleared) so only the child is active.
        parent = load_goal("parent-sid")
        assert parent is not None and parent.status == "cleared"

    def test_no_goal_to_migrate_returns_false(self, hermes_home):
        from hermes_cli.goals import migrate_goal_to_session, load_goal
        assert migrate_goal_to_session("empty-parent", "child2") is False
        assert load_goal("child2") is None

    def test_does_not_clobber_existing_child_goal(self, hermes_home):
        from hermes_cli.goals import save_goal, load_goal, migrate_goal_to_session, GoalState
        save_goal("p3", GoalState(goal="parent goal"))
        save_goal("c3", GoalState(goal="child already has one"))
        assert migrate_goal_to_session("p3", "c3") is False
        assert load_goal("c3").goal == "child already has one"

    def test_same_id_is_noop(self, hermes_home):
        from hermes_cli.goals import save_goal, migrate_goal_to_session, GoalState
        save_goal("same", GoalState(goal="g"))
        assert migrate_goal_to_session("same", "same") is False

    def test_cleared_goal_not_migrated(self, hermes_home):
        from hermes_cli.goals import save_goal, clear_goal, migrate_goal_to_session, load_goal, GoalState
        save_goal("p4", GoalState(goal="done already"))
        clear_goal("p4")
        assert migrate_goal_to_session("p4", "c4") is False
        assert load_goal("c4") is None


class TestGoalManagerSubgoals:
    def test_add_subgoal(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-add")
        mgr.set("main goal")
        text = mgr.add_subgoal("  use bullet points  ")
        assert text == "use bullet points"
        assert mgr.state.subgoals == ["use bullet points"]

    def test_add_subgoal_requires_active_goal(self, hermes_home):
        import pytest
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-noactive")
        with pytest.raises(RuntimeError):
            mgr.add_subgoal("oops")

    def test_add_empty_subgoal_rejected(self, hermes_home):
        import pytest
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-empty")
        mgr.set("g")
        with pytest.raises(ValueError):
            mgr.add_subgoal("   ")

    def test_remove_subgoal(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-remove")
        mgr.set("g")
        mgr.add_subgoal("first")
        mgr.add_subgoal("second")
        mgr.add_subgoal("third")
        removed = mgr.remove_subgoal(2)
        assert removed == "second"
        assert mgr.state.subgoals == ["first", "third"]

    def test_remove_subgoal_out_of_range(self, hermes_home):
        import pytest
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-oob")
        mgr.set("g")
        mgr.add_subgoal("only")
        with pytest.raises(IndexError):
            mgr.remove_subgoal(5)
        with pytest.raises(IndexError):
            mgr.remove_subgoal(0)

    def test_clear_subgoals(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-clear")
        mgr.set("g")
        mgr.add_subgoal("a")
        mgr.add_subgoal("b")
        prev = mgr.clear_subgoals()
        assert prev == 2
        assert mgr.state.subgoals == []

    def test_subgoals_persist_across_reloads(self, hermes_home):
        """Subgoals stored in SessionDB survive a fresh GoalManager."""
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-persist")
        mgr.set("g")
        mgr.add_subgoal("first")
        mgr.add_subgoal("second")

        mgr2 = GoalManager(session_id="sub-persist")
        assert mgr2.state.subgoals == ["first", "second"]


class TestContinuationPromptWithSubgoals:
    def test_empty_subgoals_uses_original_template(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="cp-empty")
        mgr.set("ship the feature")
        prompt = mgr.next_continuation_prompt()
        assert prompt is not None
        assert "ship the feature" in prompt
        assert "Additional criteria" not in prompt

    def test_with_subgoals_includes_them(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="cp-with")
        mgr.set("ship the feature")
        mgr.add_subgoal("write tests")
        mgr.add_subgoal("update docs")
        prompt = mgr.next_continuation_prompt()
        assert prompt is not None
        assert "ship the feature" in prompt
        assert "Additional criteria" in prompt
        assert "1. write tests" in prompt
        assert "2. update docs" in prompt


class TestJudgeGoalWithSubgoals:
    def test_judge_uses_subgoals_template_when_provided(self, hermes_home):
        """judge_goal switches templates when subgoals is non-empty.

        We don't actually call the model — we patch the aux client to
        capture the prompt that would be sent.
        """
        from unittest.mock import patch
        from hermes_cli import goals

        captured = {}

        class _FakeMsg:
            content = '{"done": true, "reason": "all done"}'
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        def _fake_call_llm(**kwargs):
            captured.update(kwargs)
            return _FakeResp()

        with patch("agent.auxiliary_client.call_llm", side_effect=_fake_call_llm):
            verdict, reason, parse_failed, _wd, _tf = goals.judge_goal(
                "ship the feature",
                "ok shipped",
                subgoals=["write tests", "update docs"],
            )

        # The aux client was called with a prompt that includes the subgoals.
        sent_messages = captured.get("messages") or []
        user_msg = next((m["content"] for m in sent_messages if m["role"] == "user"), "")
        assert "Additional criteria" in user_msg
        assert "1. write tests" in user_msg
        assert "2. update docs" in user_msg
        assert "every additional criterion" in user_msg
        assert verdict == "done"

    def test_judge_uses_original_template_when_no_subgoals(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals

        captured = {}

        class _FakeMsg:
            content = '{"done": true, "reason": "ok"}'
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        def _fake_call_llm(**kwargs):
            captured.update(kwargs)
            return _FakeResp()

        with patch("agent.auxiliary_client.call_llm", side_effect=_fake_call_llm):
            goals.judge_goal("ship it", "done", subgoals=None)

        sent_messages = captured.get("messages") or []
        user_msg = next((m["content"] for m in sent_messages if m["role"] == "user"), "")
        assert "Additional criteria" not in user_msg
        assert "ship it" in user_msg


class TestStatusLineSubgoalCount:
    def test_status_line_no_subgoals(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sl-empty")
        mgr.set("ship it")
        line = mgr.status_line()
        assert "ship it" in line
        assert "subgoal" not in line.lower()

    def test_status_line_with_subgoals(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sl-with")
        mgr.set("ship it")
        mgr.add_subgoal("a")
        mgr.add_subgoal("b")
        line = mgr.status_line()
        assert "2 subgoals" in line


# ──────────────────────────────────────────────────────────────────────
# Wait barrier — parking the goal loop on a background process
# ──────────────────────────────────────────────────────────────────────


class TestWaitBarrier:
    """The /goal wait barrier parks the loop on a live PID and resumes when
    the process exits, without burning turns or calling the judge."""

    @staticmethod
    def _spawn_sleeper():
        """Start a short-lived child process; return its Popen handle."""
        import subprocess
        import sys
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    @staticmethod
    def _dead_pid():
        """A PID that is essentially guaranteed not to be running."""
        return 2_000_000_000

    def test_wait_on_requires_active_goal(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="wb-noactive")
        with pytest.raises(RuntimeError):
            mgr.wait_on(12345)

    def test_wait_on_rejects_bad_pid(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="wb-badpid")
        mgr.set("g")
        with pytest.raises(ValueError):
            mgr.wait_on(0)

    def test_parked_on_live_pid_does_not_continue_or_judge(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        try:
            mgr = GoalManager(session_id="wb-live")
            mgr.set("ship it", max_turns=5)
            mgr.wait_on(proc.pid, reason="CI green")
            assert mgr.is_waiting() is True

            # The judge must NOT be called while parked, and no turn is burned.
            judge = MagicMock(return_value=("continue", "x", False, None, False))
            with patch.object(goals, "judge_goal", judge):
                decision = mgr.evaluate_after_turn("still waiting on CI")

            judge.assert_not_called()
            assert decision["verdict"] == "waiting"
            assert decision["should_continue"] is False
            assert decision["continuation_prompt"] is None
            assert mgr.state.turns_used == 0  # no turn consumed while parked
            assert "CI green" in decision["message"]
            assert mgr.state.status == "active"  # still active, just parked
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_barrier_auto_clears_when_process_exits_and_loop_resumes(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        mgr = GoalManager(session_id="wb-exit")
        mgr.set("ship it", max_turns=5)
        mgr.wait_on(proc.pid, reason="build")
        assert mgr.is_waiting() is True

        # Kill the process — barrier should auto-clear and judging resumes.
        proc.terminate()
        proc.wait(timeout=10)

        assert mgr.is_waiting() is False  # lazy auto-clear
        assert mgr.state.waiting_on_pid is None

        with patch.object(goals, "judge_goal", return_value=("continue", "more", False, None, False)):
            decision = mgr.evaluate_after_turn("process finished, here are results")

        assert decision["verdict"] == "continue"
        assert decision["should_continue"] is True
        assert mgr.state.turns_used == 1  # now a turn IS consumed

    def test_dead_pid_never_parks(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="wb-dead")
        mgr.set("g", max_turns=5)
        mgr.wait_on(self._dead_pid(), reason="already-dead")
        # is_waiting clears the stale barrier immediately.
        assert mgr.is_waiting() is False

        with patch.object(goals, "judge_goal", return_value=("continue", "go", False, None, False)):
            decision = mgr.evaluate_after_turn("response")
        assert decision["should_continue"] is True

    def test_stop_waiting_clears_barrier(self, hermes_home):
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        try:
            mgr = GoalManager(session_id="wb-stop")
            mgr.set("g")
            mgr.wait_on(proc.pid)
            assert mgr.is_waiting() is True
            assert mgr.stop_waiting() is True
            assert mgr.state.waiting_on_pid is None
            assert mgr.is_waiting() is False
            assert mgr.stop_waiting() is False  # idempotent
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_pause_and_resume_clear_barrier(self, hermes_home):
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        try:
            mgr = GoalManager(session_id="wb-pause")
            mgr.set("g")
            mgr.wait_on(proc.pid)
            mgr.pause()
            assert mgr.state.waiting_on_pid is None

            mgr.resume()
            assert mgr.state.waiting_on_pid is None
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_barrier_persists_and_reloads(self, hermes_home):
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        try:
            mgr = GoalManager(session_id="wb-persist")
            mgr.set("g")
            mgr.wait_on(proc.pid, reason="deploy")

            # Fresh manager loads the persisted barrier.
            mgr2 = GoalManager(session_id="wb-persist")
            assert mgr2.state.waiting_on_pid == proc.pid
            assert mgr2.state.waiting_reason == "deploy"
            assert mgr2.is_waiting() is True
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_old_state_row_loads_without_barrier_fields(self, hermes_home):
        """Backwards-compat: a state_meta row written before the barrier
        existed must load with no barrier."""
        from hermes_cli.goals import GoalState

        legacy = json.dumps({
            "goal": "old goal",
            "status": "active",
            "turns_used": 2,
            "max_turns": 20,
        })
        st = GoalState.from_json(legacy)
        assert st.goal == "old goal"
        assert st.waiting_on_pid is None
        assert st.waiting_reason is None
        assert st.waiting_since == 0.0
        assert st.waiting_until == 0.0


# ──────────────────────────────────────────────────────────────────────
# Judge-driven auto-wait — the judge parks the loop on its own
# ──────────────────────────────────────────────────────────────────────


class TestJudgeDrivenWait:
    """The judge returns a `wait` verdict (given live background-process
    context) and the loop parks automatically — no manual /goal wait."""

    @staticmethod
    def _spawn_sleeper():
        import subprocess, sys
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    def test_judge_wait_pid_parks_loop(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        try:
            mgr = GoalManager(session_id="jw-pid", default_max_turns=10)
            mgr.set("ship the PR")
            # Judge sees the running process and says wait-on-pid.
            with patch.object(
                goals, "judge_goal",
                return_value=("wait", "CI watcher still running", False, {"pid": proc.pid}, False),
            ):
                decision = mgr.evaluate_after_turn(
                    "Pushed the PR, watching CI.",
                    background_processes=[{
                        "pid": proc.pid, "command": "wait_for_pr_green.sh",
                        "status": "running", "uptime_seconds": 12,
                    }],
                    durable_resume_scheduled=True,
                )
            assert decision["verdict"] == "wait"
            assert decision["should_continue"] is False
            assert decision["continuation_prompt"] is None
            assert mgr.state.waiting_on_pid == proc.pid
            assert mgr.is_waiting() is True

            # Next turn while still parked: judge must NOT be called again.
            judge = MagicMock()
            with patch.object(goals, "judge_goal", judge):
                d2 = mgr.evaluate_after_turn("still going")
            judge.assert_not_called()
            assert d2["verdict"] == "waiting"
            assert d2["should_continue"] is False
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_judge_wait_seconds_parks_loop(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="jw-secs", default_max_turns=10)
        mgr.set("retry after backoff")
        with patch.object(
            goals, "judge_goal",
            return_value=("wait", "rate limited", False, {"seconds": 120}, False),
        ):
            decision = mgr.evaluate_after_turn(
                "Hit a 429, backing off.",
                durable_resume_scheduled=True,
            )
        assert decision["verdict"] == "wait"
        assert decision["should_continue"] is False
        assert mgr.state.waiting_until > 0
        assert mgr.state.waiting_on_pid is None
        assert mgr.is_waiting() is True

    def test_judge_wait_without_durable_resume_cron_keeps_goal_running(self, hermes_home):
        """A process callback alone must never park a standing goal."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="jw-no-cron", default_max_turns=10)
        mgr.set("finish the release")
        with patch.object(
            goals,
            "judge_goal",
            return_value=(
                "wait",
                "reviewer still running",
                False,
                {"session_id": "proc_unbacked"},
                False,
            ),
        ):
            decision = mgr.evaluate_after_turn(
                "Waiting for the reviewer callback.",
                background_processes=[{
                    "session_id": "proc_unbacked",
                    "pid": 4242,
                    "command": "review.sh",
                    "status": "running",
                }],
            )

        assert decision["verdict"] == "continue"
        assert decision["should_continue"] is True
        assert decision["continuation_prompt"]
        assert mgr.state.waiting_on_session is None
        assert mgr.state.waiting_on_pid is None
        assert mgr.state.waiting_until == 0.0
        assert "durable resume cron" in decision["reason"]

    def test_time_barrier_clears_after_deadline(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="jw-deadline")
        mgr.set("g")
        mgr.wait_for_seconds(120, reason="backoff")
        assert mgr.is_waiting() is True
        # Force the deadline into the past → barrier auto-clears.
        mgr.state.waiting_until = time.time() - 1
        assert mgr.is_waiting() is False
        assert mgr.state.waiting_until == 0.0

    def test_continue_verdict_still_continues_with_background(self, hermes_home):
        """A running process present but judge says continue → normal loop."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="jw-cont", default_max_turns=10)
        mgr.set("do work")
        with patch.object(
            goals, "judge_goal",
            return_value=("continue", "more to do", False, None, False),
        ):
            decision = mgr.evaluate_after_turn(
                "made progress",
                background_processes=[{"pid": 999999, "command": "x", "status": "running"}],
            )
        assert decision["verdict"] == "continue"
        assert decision["should_continue"] is True
        assert mgr.state.waiting_on_pid is None


# ──────────────────────────────────────────────────────────────────────
# Session/trigger barrier — wait on a process's OWN trigger, not just exit
# ──────────────────────────────────────────────────────────────────────


class TestSessionTriggerBarrier:
    """The session barrier (wait_on_session) releases when a process's own
    trigger fires — a watch_patterns match mid-run (process may never exit)
    OR exit — not only on PID exit. CI-safe: uses synthetic registry session
    objects, no real child processes."""

    @staticmethod
    def _inject(sid, *, watch_patterns=None, exited=False):
        import time as _t
        from tools.process_registry import process_registry, ProcessSession
        s = ProcessSession(id=sid, command="watcher.sh", task_id="t",
                           session_key="", cwd="/tmp", started_at=_t.time())
        if watch_patterns:
            s.watch_patterns = list(watch_patterns)
        s.exited = exited
        if exited:
            process_registry._finished[sid] = s
        else:
            process_registry._running[sid] = s
        return s, process_registry

    def test_registry_is_session_waiting_running_unmatched(self, hermes_home):
        s, reg = self._inject("proc_t1", watch_patterns=["READY"])
        assert reg.is_session_waiting("proc_t1") is True

    def test_registry_releases_on_watch_match_while_alive(self, hermes_home):
        s, reg = self._inject("proc_t2", watch_patterns=["READY"])
        assert reg.is_session_waiting("proc_t2") is True
        s._watch_hits = 1  # what _check_watch_patterns sets on a match
        # Released even though the process is STILL running (never exited).
        assert s.exited is False
        assert reg.is_session_waiting("proc_t2") is False

    def test_registry_releases_on_exit_plain_session(self, hermes_home):
        s, reg = self._inject("proc_t3")  # no watch pattern
        assert reg.is_session_waiting("proc_t3") is True
        s.exited = True
        assert reg.is_session_waiting("proc_t3") is False

    def test_registry_unknown_session_never_waits(self, hermes_home):
        from tools.process_registry import process_registry
        assert process_registry.is_session_waiting("proc_does_not_exist") is False

    def test_goal_parks_on_session_and_releases_on_trigger(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        s, reg = self._inject("proc_t4", watch_patterns=["BUILD SUCCESSFUL"])
        mgr = GoalManager(session_id="st-goal", default_max_turns=10)
        mgr.set("wait for the build to succeed")
        with patch.object(
            goals, "judge_goal",
            return_value=("wait", "blocked on build", False, {"session_id": "proc_t4"}, False),
        ):
            decision = mgr.evaluate_after_turn(
                "Started the build watcher.",
                background_processes=[{
                    "session_id": "proc_t4", "pid": 4242, "command": "watcher.sh",
                    "status": "running", "watch_patterns": ["BUILD SUCCESSFUL"],
                    "watch_hit": False,
                }],
                durable_resume_scheduled=True,
            )
        assert decision["verdict"] == "wait"
        assert mgr.state.waiting_on_session == "proc_t4"
        assert mgr.is_waiting() is True

        # Judge must NOT be called again while parked.
        judge = MagicMock()
        with patch.object(goals, "judge_goal", judge):
            d2 = mgr.evaluate_after_turn("still building")
        judge.assert_not_called()
        assert d2["should_continue"] is False

        # Trigger fires mid-run (process still alive) → barrier releases.
        s._watch_hits = 1
        assert mgr.is_waiting() is False
        assert mgr.state.waiting_on_session is None

        # Loop resumes with a real judge verdict.
        with patch.object(goals, "judge_goal",
                          return_value=("continue", "build done", False, None, False)):
            d3 = mgr.evaluate_after_turn("build succeeded")
        assert d3["should_continue"] is True

    def test_wait_on_session_validation(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="st-val")
        # No active goal → RuntimeError
        try:
            mgr.wait_on_session("proc_x")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass
        mgr.set("g")
        try:
            mgr.wait_on_session("")
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_session_directive_parsed_from_judge(self, hermes_home):
        from hermes_cli.goals import _parse_judge_response
        v, _, pf, wd = _parse_judge_response(
            '{"verdict": "wait", "wait_on_session": "proc_abc", "reason": "r"}'
        )
        assert v == "wait"
        assert pf is False
        assert wd == {"session_id": "proc_abc"}

    def test_old_state_loads_without_session_field(self, hermes_home):
        from hermes_cli.goals import GoalState
        st = GoalState.from_json(json.dumps({
            "goal": "g", "status": "active", "turns_used": 0, "max_turns": 20,
        }))
        assert st.waiting_on_session is None


# ──────────────────────────────────────────────────────────────────────
# Completion contract (Codex-inspired structured goals)
# ──────────────────────────────────────────────────────────────────────


class TestParseContract:
    def test_plain_goal_no_contract(self):
        from hermes_cli.goals import parse_contract

        headline, contract = parse_contract("Migrate auth to JWT")
        assert headline == "Migrate auth to JWT"
        assert contract.is_empty()

    def test_incidental_colon_not_treated_as_field(self):
        from hermes_cli.goals import parse_contract

        # "Fix bug:" — "fix bug" is not a known alias, so the whole line
        # stays the headline and no contract field is populated.
        headline, contract = parse_contract("Fix bug: the parser drops trailing commas")
        assert headline == "Fix bug: the parser drops trailing commas"
        assert contract.is_empty()

    def test_inline_fields_parsed(self):
        from hermes_cli.goals import parse_contract

        text = (
            "Migrate auth to JWT\n"
            "verify: the auth test suite passes\n"
            "constraints: keep the /login response shape unchanged\n"
            "boundaries: only touch services/auth and its tests\n"
            "stop when: a schema change needs product sign-off"
        )
        headline, contract = parse_contract(text)
        assert headline == "Migrate auth to JWT"
        assert contract.verification == "the auth test suite passes"
        assert contract.constraints == "keep the /login response shape unchanged"
        assert contract.boundaries == "only touch services/auth and its tests"
        assert contract.stop_when == "a schema change needs product sign-off"
        assert not contract.is_empty()

    def test_alias_variants(self):
        from hermes_cli.goals import parse_contract

        _, c = parse_contract("Goal\nverified by: tests green\npreserve: public API")
        assert c.verification == "tests green"
        assert c.constraints == "public API"

    def test_multiple_lines_same_field_joined(self):
        from hermes_cli.goals import parse_contract

        _, c = parse_contract("G\nconstraints: a\nconstraints: b")
        assert c.constraints == "a b"


class TestGoalContractSerialization:
    def test_roundtrip_with_contract(self):
        from hermes_cli.goals import GoalState, GoalContract

        state = GoalState(
            goal="ship it",
            contract=GoalContract(
                verification="pytest passes",
                constraints="don't break the API",
            ),
        )
        restored = GoalState.from_json(state.to_json())
        assert restored.goal == "ship it"
        assert restored.contract.verification == "pytest passes"
        assert restored.contract.constraints == "don't break the API"
        assert restored.has_contract()

    def test_old_row_without_contract_loads_clean(self):
        # A state_meta row written before this feature has no "contract" key.
        from hermes_cli.goals import GoalState

        legacy = '{"goal": "old goal", "status": "active", "turns_used": 2}'
        state = GoalState.from_json(legacy)
        assert state.goal == "old goal"
        assert state.turns_used == 2
        assert state.contract.is_empty()
        assert not state.has_contract()

    def test_render_block_omits_empty_fields(self):
        from hermes_cli.goals import GoalContract

        block = GoalContract(outcome="X", verification="Y").render_block()
        assert "Outcome: X" in block
        assert "Verification: Y" in block
        assert "Constraints" not in block


class TestGoalManagerContract:
    def test_set_with_contract(self, hermes_home):
        from hermes_cli.goals import GoalManager, GoalContract

        mgr = GoalManager(session_id="c-set")
        mgr.set("ship it", contract=GoalContract(verification="tests pass"))
        assert mgr.has_contract()
        assert "contract" in mgr.status_line()

    def test_set_without_contract_no_marker(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="c-none")
        mgr.set("ship it")
        assert not mgr.has_contract()
        assert "contract" not in mgr.status_line()

    def test_continuation_prompt_includes_contract(self, hermes_home):
        from hermes_cli.goals import GoalManager, GoalContract

        mgr = GoalManager(session_id="c-cont")
        mgr.set("ship it", contract=GoalContract(verification="run pytest"))
        prompt = mgr.next_continuation_prompt()
        assert "Completion contract" in prompt
        assert "run pytest" in prompt
        assert "concrete evidence" in prompt

    def test_set_contract_after_the_fact(self, hermes_home):
        from hermes_cli.goals import GoalManager, GoalContract

        mgr = GoalManager(session_id="c-after")
        mgr.set("ship it")
        assert not mgr.has_contract()
        mgr.set_contract(GoalContract(verification="x"))
        assert mgr.has_contract()
        # Survives reload.
        from hermes_cli.goals import GoalManager as GM2
        assert GM2(session_id="c-after").has_contract()

    def test_persistence_roundtrip(self, hermes_home):
        from hermes_cli.goals import GoalManager, GoalContract

        GoalManager(session_id="c-persist").set(
            "ship it", contract=GoalContract(outcome="O", verification="V")
        )
        reloaded = GoalManager(session_id="c-persist")
        assert reloaded.state.contract.outcome == "O"
        assert reloaded.state.contract.verification == "V"


class TestJudgeWithContract:
    def _fake_call_llm(self, captured, content='{"done": false, "reason": "more"}'):
        """judge_goal routes through call_llm (#35566) — capture its kwargs."""
        class _FakeMsg:
            pass
        _FakeMsg.content = content
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]

        def _fake(**kwargs):
            captured.update(kwargs)
            return _FakeResp()
        return _fake

    def test_judge_uses_contract_template(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals
        from hermes_cli.goals import GoalContract

        captured = {}
        with patch("agent.auxiliary_client.call_llm",
                   side_effect=self._fake_call_llm(captured)):
            goals.judge_goal(
                "ship it", "I think it's done",
                contract=GoalContract(verification="pytest -q passes"),
            )
        user_msg = next(
            (m["content"] for m in (captured.get("messages") or []) if m["role"] == "user"), ""
        )
        assert "completion contract" in user_msg.lower()
        assert "pytest -q passes" in user_msg
        assert "concrete evidence" in user_msg

    def test_contract_plus_subgoals_combine(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals
        from hermes_cli.goals import GoalContract

        captured = {}
        with patch("agent.auxiliary_client.call_llm",
                   side_effect=self._fake_call_llm(captured)):
            goals.judge_goal(
                "ship it", "done",
                subgoals=["write changelog"],
                contract=GoalContract(verification="pytest passes"),
            )
        user_msg = next(
            (m["content"] for m in (captured.get("messages") or []) if m["role"] == "user"), ""
        )
        assert "pytest passes" in user_msg
        assert "write changelog" in user_msg


class TestDraftContract:
    def test_draft_parses_json(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals

        class _FakeMsg:
            content = (
                '{"outcome": "auth on JWT", "verification": "auth suite green", '
                '"constraints": "no API change", "boundaries": "services/auth", '
                '"stop_when": "schema change needed"}'
            )
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        with patch("agent.auxiliary_client.call_llm",
                   return_value=_FakeResp()):
            contract = goals.draft_contract("Migrate auth to JWT")
        assert contract is not None
        assert contract.outcome == "auth on JWT"
        assert contract.verification == "auth suite green"
        assert not contract.is_empty()

    def test_draft_returns_none_on_bad_json(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals

        class _FakeMsg:
            content = "I cannot produce JSON, sorry"
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        with patch("agent.auxiliary_client.call_llm",
                   return_value=_FakeResp()):
            assert goals.draft_contract("anything") is None

    def test_draft_returns_none_when_no_client(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals

        with patch("agent.auxiliary_client.call_llm",
                   side_effect=RuntimeError("No LLM provider configured")):
            assert goals.draft_contract("anything") is None


# ──────────────────────────────────────────────────────────────────────
# Compose: completion contract + wait barrier in one judge call
# ──────────────────────────────────────────────────────────────────────


class TestContractAndBackgroundCompose:
    """A contract goal blocked on a background process must surface BOTH
    the contract block and the background-process list to the judge, so it
    can return either done (evidence met) or wait (parked on the poller)."""

    def _capture_call_llm(self, captured, content='{"verdict": "wait", "wait_on_pid": 4242, "reason": "CI still running"}'):
        """judge_goal routes through call_llm (#35566) — capture its kwargs."""
        class _FakeMsg:
            pass
        _FakeMsg.content = content
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]

        def _fake(**kwargs):
            captured.update(kwargs)
            return _FakeResp()
        return _fake

    def test_judge_prompt_carries_contract_and_background(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals
        from hermes_cli.goals import GoalContract

        captured = {}
        bg = [{
            "session_id": "ci-watch", "pid": 4242, "status": "running",
            "command": "wait_for_pr_green.sh 50501", "trigger": "exit",
        }]
        with patch("agent.auxiliary_client.call_llm",
                   side_effect=self._capture_call_llm(captured)):
            verdict, reason, parse_failed, wait_directive, _tf = goals.judge_goal(
                "ship the PR",
                "I pushed and started the CI watcher; waiting on it now.",
                contract=GoalContract(verification="PR CI goes green"),
                background_processes=bg,
            )
        user_msg = next(
            (m["content"] for m in (captured.get("messages") or []) if m["role"] == "user"), ""
        )
        # Both surfaces present in one prompt.
        assert "completion contract" in user_msg.lower()
        assert "PR CI goes green" in user_msg
        assert "Background processes" in user_msg
        assert "4242" in user_msg
        # The judge can return a wait verdict on a contract goal.
        assert verdict == "wait"
        assert wait_directive and wait_directive.get("pid") == 4242

    def test_contract_goal_can_still_complete_on_evidence(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals
        from hermes_cli.goals import GoalContract

        captured = {}
        bg = [{"session_id": "ci", "pid": 4242, "status": "running", "command": "ci", "trigger": "exit"}]
        with patch("agent.auxiliary_client.call_llm",
                   side_effect=self._capture_call_llm(
                       captured,
                       content='{"verdict": "done", "reason": "CI is green, evidence shown"}',
                   )):
            verdict, reason, parse_failed, wait_directive, _tf = goals.judge_goal(
                "ship the PR",
                "CI finished: 30 passed, 0 failed. Done.",
                contract=GoalContract(verification="PR CI goes green"),
                background_processes=bg,
            )
        assert verdict == "done"
        assert wait_directive is None
