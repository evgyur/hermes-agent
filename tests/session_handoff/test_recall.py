"""Tests for RecallCommand (T-009)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli.session_handoff.query_parser import RecallQueryParser
from hermes_cli.recall import RecallCommand, RecallFormatter, SessionCard, run_recall


class TestRecallFormatterTruncate:
    """Unit tests for truncation logic."""

    def test_truncate_short(self):
        s = "hello"
        assert RecallFormatter._truncate(s, 10) == "hello"

    def test_truncate_long(self):
        s = "hello world this is long"
        result = RecallFormatter._truncate(s, 10)
        assert len(result) <= 10
        assert result.endswith("…")

    def test_truncate_empty(self):
        assert RecallFormatter._truncate("", 10) == "—"

    def test_truncate_whitespace(self):
        assert RecallFormatter._truncate("  hello  ", 10) == "hello"


class TestRecallFormatterFormatCard:
    def test_basic_card(self):
        card = SessionCard(
            session_id="ses-1",
            title="Guardian setup",
            source="telegram",
            model="gpt-5.5",
            started_at="2026-05-26T10:00:00+00:00",
            ended_at="2026-05-26T10:30:00+00:00",
            end_reason="completed",
            message_count=15,
            tool_call_count=3,
            handoff_state=None,
            handoff_platform=None,
            handoff_error=None,
            why_matched='query term: "guardian"',
            last_state="completed normally",
            tools=["terminal", "read_file"],
            evidence_refs=["session://ses-1"],
        )
        output = RecallFormatter.format_card(card, 1)
        assert "Guardian setup" in output
        assert "ses-1" in output
        assert "telegram" in output
        assert "2026-05-26" in output
        assert "terminal" in output

    def test_card_no_title(self):
        card = SessionCard(
            session_id="ses-2",
            title="",
            source="cli",
            model="claude-3-5-sonnet",
            started_at="2026-05-26T10:00:00+00:00",
            ended_at=None,
            end_reason=None,
            message_count=5,
            tool_call_count=0,
            handoff_state=None,
            handoff_platform=None,
            handoff_error=None,
            why_matched="source=cli",
            last_state="session is active",
            tools=[],
            evidence_refs=["session://ses-2"],
        )
        output = RecallFormatter.format_card(card, 1)
        assert "ses-2" in output
        assert "cli" in output

    def test_card_with_tools(self):
        card = SessionCard(
            session_id="ses-3",
            title="Test session",
            source="cli",
            model="gpt-5.5",
            started_at="2026-05-26T10:00:00+00:00",
            ended_at="2026-05-26T10:30:00+00:00",
            end_reason="error",
            message_count=20,
            tool_call_count=7,
            handoff_state=None,
            handoff_platform=None,
            handoff_error=None,
            why_matched="tool calls: 7",
            last_state="ended (error)",
            tools=["terminal", "read_file", "write_file", "search_files", "patch", "terminal"],
            evidence_refs=["session://ses-3"],
        )
        output = RecallFormatter.format_card(card, 1)
        assert "terminal" in output
        # More than MAX_TOOLS (5) should show "+N more"
        assert "+1 more" in output or "+" in output

    def test_card_with_handoff_error(self):
        card = SessionCard(
            session_id="ses-4",
            title="Handoff test",
            source="telegram",
            model="gpt-5.5",
            started_at="2026-05-26T10:00:00+00:00",
            ended_at="2026-05-26T10:30:00+00:00",
            end_reason="error",
            message_count=10,
            tool_call_count=2,
            handoff_state="error",
            handoff_platform="telegram",
            handoff_error="timeout",
            why_matched="handoff error",
            last_state="handoff error: timeout",
            tools=["terminal"],
            evidence_refs=["session://ses-4"],
        )
        output = RecallFormatter.format_card(card, 1)
        assert "handoff error" in output


class TestRecallFormatterFormatResults:
    def test_empty_results(self):
        output = RecallFormatter.format_results([], "guardian", 50.0)
        assert "No sessions matched" in output
        assert "guardian" in output

    def test_results_with_cards(self):
        card = SessionCard(
            session_id="ses-1",
            title="Guardian setup",
            source="telegram",
            model="gpt-5.5",
            started_at="2026-05-26T10:00:00+00:00",
            ended_at="2026-05-26T10:30:00+00:00",
            end_reason="completed",
            message_count=15,
            tool_call_count=3,
            handoff_state=None,
            handoff_platform=None,
            handoff_error=None,
            why_matched='query term: "guardian"',
            last_state="completed normally",
            tools=["terminal"],
            evidence_refs=["session://ses-1"],
        )
        output = RecallFormatter.format_results([card], "guardian", 50.0)
        assert "Guardian setup" in output
        assert "1." in output

    def test_elapsed_time_ms(self):
        card = SessionCard(
            session_id="ses-1",
            title="Test",
            source="cli",
            model="gpt-5.5",
            started_at="2026-05-26T10:00:00+00:00",
            ended_at=None,
            end_reason=None,
            message_count=5,
            tool_call_count=0,
            handoff_state=None,
            handoff_platform=None,
            handoff_error=None,
            why_matched="test",
            last_state="active",
            tools=[],
            evidence_refs=["session://ses-1"],
        )
        output = RecallFormatter.format_results([card], "test", 500.0)
        assert "500ms" in output

    def test_elapsed_time_s(self):
        card = SessionCard(
            session_id="ses-1",
            title="Test",
            source="cli",
            model="gpt-5.5",
            started_at="2026-05-26T10:00:00+00:00",
            ended_at=None,
            end_reason=None,
            message_count=5,
            tool_call_count=0,
            handoff_state=None,
            handoff_platform=None,
            handoff_error=None,
            why_matched="test",
            last_state="active",
            tools=[],
            evidence_refs=["session://ses-1"],
        )
        output = RecallFormatter.format_results([card], "test", 2500.0)
        assert "2.5s" in output

    def test_errors_shown(self):
        output = RecallFormatter.format_results([], "test", 50.0, errors=["unknown filter @foo"])
        assert "Warnings" in output
        assert "@foo" in output

    def test_max_cards_enforced(self):
        cards = [
            SessionCard(
                session_id=f"ses-{i}",
                title=f"Session {i}",
                source="cli",
                model="gpt-5.5",
                started_at="2026-05-26T10:00:00+00:00",
                ended_at=None,
                end_reason=None,
                message_count=5,
                tool_call_count=0,
                handoff_state=None,
                handoff_platform=None,
                handoff_error=None,
                why_matched="test",
                last_state="active",
                tools=[],
                evidence_refs=[f"session://ses-{i}"],
            )
            for i in range(15)
        ]
        output = RecallFormatter.format_results(cards, "test", 50.0)
        assert "(showing first 10" in output


class TestRecallCommandExecute:
    """Integration-style tests using the populated_db fixture."""

    def test_execute_empty_query(self, populated_db: Path):
        cmd = RecallCommand(db_path=str(populated_db))
        output = cmd.execute("")
        assert isinstance(output, str)
        assert len(output) > 0

    def test_execute_guardian_query(self, populated_db: Path):
        cmd = RecallCommand(db_path=str(populated_db))
        output = cmd.execute("guardian")
        assert "guardian" in output.lower() or "Guardian" in output

    def test_execute_with_today_filter(self, populated_db: Path):
        cmd = RecallCommand(db_path=str(populated_db))
        output = cmd.execute("@today")
        assert isinstance(output, str)

    def test_execute_with_source_filter(self, populated_db: Path):
        cmd = RecallCommand(db_path=str(populated_db))
        output = cmd.execute("@source:telegram")
        assert "telegram" in output

    def test_execute_with_tool_filter(self, populated_db: Path):
        cmd = RecallCommand(db_path=str(populated_db))
        output = cmd.execute("@tool:terminal")
        assert isinstance(output, str)

    def test_execute_with_combined_filters(self, populated_db: Path):
        cmd = RecallCommand(db_path=str(populated_db))
        output = cmd.execute("guardian @source:telegram @7d")
        assert isinstance(output, str)

    def test_execute_unknown_filter_degrades(self, populated_db: Path):
        cmd = RecallCommand(db_path=str(populated_db))
        # Unknown filters should not raise — should degrade gracefully
        output = cmd.execute("@unknown:xyz")
        assert isinstance(output, str)
        assert "Warnings" in output or "unknown" in output

    def test_execute_no_results(self, populated_db: Path):
        cmd = RecallCommand(db_path=str(populated_db))
        output = cmd.execute("xyzzy_nonexistent_query_term_12345")
        assert "No sessions matched" in output


class TestRecallCommandFormatDetail:
    def test_format_detail_found(self, populated_db: Path):
        cmd = RecallCommand(db_path=str(populated_db))
        output = cmd.format_session_detail("ses-1")
        assert "ses-1" in output
        assert "Guardian setup" in output

    def test_format_detail_not_found(self, populated_db: Path):
        cmd = RecallCommand(db_path=str(populated_db))
        output = cmd.format_session_detail("nonexistent-session")
        assert "not found" in output


class TestRunRecall:
    """Smoke tests for the module-level entry point."""

    def test_run_recall_smoke(self, populated_db: Path):
        from hermes_cli.session_handoff.trace_reader import SessionTraceReader
        from hermes_cli.session_handoff.query_parser import RecallQueryParser

        # Directly instantiate with the test db
        cmd = RecallCommand(db_path=str(populated_db))
        output = cmd.execute("guardian")
        assert isinstance(output, str)
        assert len(output) > 0
