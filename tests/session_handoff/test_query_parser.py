"""Tests for RecallQueryParser."""

from __future__ import annotations

import pytest

from hermes_cli.session_handoff.query_parser import (
    RecallQueryParser,
    ParsedRecallQuery,
)


class TestRecallQueryParser:
    def test_empty_query(self):
        p = RecallQueryParser.parse("")
        assert p.free_text == ""
        assert p.source is None
        assert p.tool is None
        assert p.model is None
        assert p.status is None
        assert p.days_back is None
        assert p.is_today is False
        assert p.unknown_filters == ()

    def test_none_query(self):
        p = RecallQueryParser.parse(None)  # type: ignore
        assert p.free_text == ""

    def test_plain_text_only(self):
        p = RecallQueryParser.parse("guardian setup")
        assert p.free_text == "guardian setup"
        assert p.source is None

    def test_source_filter(self):
        p = RecallQueryParser.parse("@source:telegram")
        assert p.source == "telegram"
        assert p.free_text == ""

    def test_source_filter_with_text(self):
        p = RecallQueryParser.parse("auth bug @source:telegram")
        assert p.source == "telegram"
        assert p.free_text == "auth bug"

    def test_tool_filter(self):
        p = RecallQueryParser.parse("@tool:terminal")
        assert p.tool == "terminal"
        assert p.free_text == ""

    def test_tool_filter_with_text(self):
        p = RecallQueryParser.parse("debug @tool:terminal")
        assert p.tool == "terminal"
        assert p.free_text == "debug"

    def test_model_filter(self):
        p = RecallQueryParser.parse("@model:gpt-5.5")
        assert p.model == "gpt-5.5"
        assert p.free_text == ""

    def test_model_filter_preserves_dots(self):
        p = RecallQueryParser.parse("@model:claude-3-5-sonnet")
        assert p.model == "claude-3-5-sonnet"

    def test_status_filter_active(self):
        p = RecallQueryParser.parse("@status:active")
        assert p.status == "active"

    def test_status_filter_completed(self):
        p = RecallQueryParser.parse("@status:completed")
        assert p.status == "completed"

    def test_status_filter_error(self):
        p = RecallQueryParser.parse("@status:error")
        assert p.status == "error"

    def test_status_filter_invalid(self):
        p = RecallQueryParser.parse("@status:xyz")
        assert p.status is None
        assert "@status:xyz" in p.unknown_filters

    def test_today_filter(self):
        p = RecallQueryParser.parse("@today")
        assert p.is_today is True
        assert p.days_back == 0
        assert p.free_text == ""

    def test_today_filter_with_text(self):
        p = RecallQueryParser.parse("guardian @today")
        assert p.is_today is True
        assert p.days_back == 0
        assert p.free_text == "guardian"

    def test_7d_filter(self):
        p = RecallQueryParser.parse("@7d")
        assert p.days_back == 7
        assert p.is_today is False

    def test_30d_filter(self):
        p = RecallQueryParser.parse("@30d")
        assert p.days_back == 30

    def test_0d_filter(self):
        p = RecallQueryParser.parse("@0d")
        assert p.days_back == 0

    def test_combined_filters(self):
        p = RecallQueryParser.parse("guardian @today @source:telegram @tool:terminal")
        assert p.free_text == "guardian"
        assert p.is_today is True
        assert p.days_back == 0
        assert p.source == "telegram"
        assert p.tool == "terminal"

    def test_all_filters_combined(self):
        p = RecallQueryParser.parse(
            "auth research @7d @source:cli @tool:terminal @model:gpt-5.5 @status:completed"
        )
        assert p.free_text == "auth research"
        assert p.days_back == 7
        assert p.source == "cli"
        assert p.tool == "terminal"
        assert p.model == "gpt-5.5"
        assert p.status == "completed"

    def test_unknown_filter_preserved(self):
        p = RecallQueryParser.parse("@unknown:xyz")
        assert "@unknown:xyz" in p.unknown_filters

    def test_multiple_unknown_filters(self):
        p = RecallQueryParser.parse("@foo @bar:baz @unknown")
        assert "@foo" in p.unknown_filters
        assert "@bar:baz" in p.unknown_filters
        assert "@unknown" in p.unknown_filters

    def test_free_text_with_dashes(self):
        p = RecallQueryParser.parse("web3-gambling-research @today")
        assert p.free_text == "web3-gambling-research"

    def test_raw_query_preserved(self):
        p = RecallQueryParser.parse("some query @today")
        assert p.raw_query == "some query @today"

    def test_double_quoted_filter(self):
        p = RecallQueryParser.parse('@source:"telegram"')
        assert p.source == '"telegram"'

    def test_filter_value_with_colons(self):
        p = RecallQueryParser.parse("@model:openai/gpt-5.5")
        assert p.model == "openai/gpt-5.5"


class TestRecallQueryParserValidate:
    def test_valid_query(self):
        p = RecallQueryParser.parse("guardian @today @source:telegram")
        errors = RecallQueryParser.validate(p)
        assert errors == []

    def test_invalid_status(self):
        p = RecallQueryParser.parse("@status:xyz")
        errors = RecallQueryParser.validate(p)
        assert any("xyz" in e for e in errors)

    def test_unknown_filter_error(self):
        p = RecallQueryParser.parse("@unknown:xyz")
        errors = RecallQueryParser.validate(p)
        assert any("unknown" in e.lower() for e in errors)


class TestRecallQueryParserToTraceArgs:
    def test_basic(self):
        p = RecallQueryParser.parse("guardian @today")
        args = RecallQueryParser.to_trace_args(p)
        assert args["free_text"] == "guardian"
        assert args["days_back"] == 0
        assert args["source"] is None

    def test_full(self):
        p = RecallQueryParser.parse("auth @7d @source:cli @tool:terminal @model:gpt-5.5 @status:completed")
        args = RecallQueryParser.to_trace_args(p)
        assert args["free_text"] == "auth"
        assert args["days_back"] == 7
        assert args["source"] == "cli"
        assert args["tool"] == "terminal"
        assert args["model"] == "gpt-5.5"
        assert args["status"] == "completed"

    def test_today_only(self):
        p = RecallQueryParser.parse("@today")
        args = RecallQueryParser.to_trace_args(p)
        assert args["days_back"] == 0

    def test_no_filters(self):
        p = RecallQueryParser.parse("plain query")
        args = RecallQueryParser.to_trace_args(p)
        assert args["free_text"] == "plain query"
        assert args["days_back"] is None