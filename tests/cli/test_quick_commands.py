"""Tests for user-defined quick commands that bypass the agent loop."""
import os
import subprocess
from unittest.mock import MagicMock, patch
from rich.text import Text
import pytest


# ── CLI tests ──────────────────────────────────────────────────────────────

class TestCLIQuickCommands:
    """Test quick command dispatch in HermesCLI.process_command."""

    @staticmethod
    def _printed_plain(call_arg):
        if isinstance(call_arg, Text):
            return call_arg.plain
        return str(call_arg)

    def _make_cli(self, quick_commands):
        from cli import HermesCLI
        cli = HermesCLI.__new__(HermesCLI)
        cli.config = {"quick_commands": quick_commands}
        cli.console = MagicMock()
        cli.agent = None
        cli.conversation_history = []
        # session_id is accessed by the fallback skill/fuzzy-match path in
        # process_command; without it, tests that exercise `/alias args`
        # can trip an AttributeError when cross-test state leaks a skill
        # command matching the alias target.
        cli.session_id = "test-session"
        return cli

    def test_exec_command_runs_and_prints_output(self):
        cli = self._make_cli({"dn": {"type": "exec", "command": "echo daily-note"}})
        result = cli.process_command("/dn")
        assert result is True
        cli.console.print.assert_called_once()
        printed = self._printed_plain(cli.console.print.call_args[0][0])
        assert printed == "daily-note"

    def test_exec_command_can_append_user_args(self):
        cli = self._make_cli({"chart": {"type": "exec", "command": "printf '%s'", "append_args": True}})
        result = cli.process_command("/chart 2h")
        assert result is True
        cli.console.print.assert_called_once()
        printed = self._printed_plain(cli.console.print.call_args[0][0])
        assert printed == "2h"

    def test_exec_command_uses_chat_console_when_tui_is_live(self):
        cli = self._make_cli({"dn": {"type": "exec", "command": "echo daily-note"}})
        cli._app = object()
        live_console = MagicMock()

        with patch("cli.ChatConsole", return_value=live_console):
            result = cli.process_command("/dn")

        assert result is True
        live_console.print.assert_called_once()
        printed = self._printed_plain(live_console.print.call_args[0][0])
        assert printed == "daily-note"
        cli.console.print.assert_not_called()








    def test_quick_command_takes_priority_over_skill_commands(self):
        """Quick commands must be checked before skill slash commands."""
        cli = self._make_cli({"mygif": {"type": "exec", "command": "echo overridden"}})
        with patch("cli._skill_commands", {"/mygif": {"name": "gif-search"}}):
            cli.process_command("/mygif")
        cli.console.print.assert_called_once()
        printed = self._printed_plain(cli.console.print.call_args[0][0])
        assert printed == "overridden"




# ── Gateway tests ──────────────────────────────────────────────────────────

class TestGatewayQuickCommands:
    """Test quick command dispatch in GatewayRunner._handle_message."""

    def _make_event(self, command, args=""):
        event = MagicMock()
        event.get_command.return_value = command
        event.get_command_args.return_value = args
        event.text = f"/{command} {args}".strip()
        event.source = MagicMock()
        event.source.user_id = "test_user"
        event.source.user_name = "Test User"
        event.source.platform.value = "telegram"
        event.source.chat_type = "dm"
        event.source.chat_id = "123"
        return event

    @pytest.mark.asyncio
    async def test_exec_command_returns_output(self):
        from gateway.run import GatewayRunner
        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = {"quick_commands": {"limits": {"type": "exec", "command": "echo ok"}}}
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)

        event = self._make_event("limits")
        result = await runner._handle_message(event)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_exec_command_can_append_user_args(self):
        from gateway.run import GatewayRunner
        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = {"quick_commands": {"chart": {"type": "exec", "command": "printf '%s'", "append_args": True}}}
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)

        event = self._make_event("chart", "2h")
        result = await runner._handle_message(event)
        assert result == "2h"

    @pytest.mark.asyncio
    async def test_exec_command_receives_message_origin(self):
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = {
            "quick_commands": {
                "where": {
                    "type": "exec",
                    "command": (
                        "printf '%s|%s|%s' "
                        '"$HERMES_ORIGIN_PLATFORM" '
                        '"$HERMES_ORIGIN_CHAT_ID" '
                        '"$HERMES_ORIGIN_THREAD_ID"'
                    ),
                }
            }
        }
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)

        event = self._make_event("where")
        event.source.thread_id = "456"
        result = await runner._handle_message(event)

        assert result == "telegram|123|456"

    @pytest.mark.asyncio
    async def test_exec_command_allowed_origins_allows_exact_origin(self):
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = {
            "quick_commands": {
                "private": {
                    "type": "exec",
                    "command": "echo private-ok",
                    "allowed_origins": ["telegram:123"],
                }
            }
        }
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)

        result = await runner._handle_message(self._make_event("private"))

        assert result == "private-ok"

    @pytest.mark.asyncio
    async def test_exec_command_allowed_origins_denies_other_chat_before_execution(self):
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = {
            "quick_commands": {
                "private": {
                    "type": "exec",
                    "command": "echo must-not-run",
                    "allowed_origins": ["telegram:999"],
                }
            }
        }
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)

        result = await runner._handle_message(self._make_event("private"))

        assert result == "This command is not available in this chat."
        assert "must-not-run" not in result

    @pytest.mark.asyncio
    async def test_exec_command_allowed_origins_invalid_shape_fails_closed(self):
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)

        invalid_values = (
            {"telegram:123": True},
            [],
            ["telegram:123", 7],
            ["telegram:"],
            [":123"],
            [" telegram:123"],
            ["telegram:123", "telegram:123"],
        )
        for allowed_origins in invalid_values:
            runner.config = {
                "quick_commands": {
                    "private": {
                        "type": "exec",
                        "command": "echo must-not-run",
                        "allowed_origins": allowed_origins,
                    }
                }
            }
            result = await runner._handle_message(self._make_event("private"))
            assert result == "This command is not available in this chat."

    @pytest.mark.asyncio
    async def test_exec_command_allowed_origins_rejects_prefix_and_platform_mismatch(self):
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = {
            "quick_commands": {
                "private": {
                    "type": "exec",
                    "command": "echo must-not-run",
                    "allowed_origins": ["telegram:123"],
                }
            }
        }
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)

        prefix_event = self._make_event("private")
        prefix_event.source.chat_id = "1234"
        platform_event = self._make_event("private")
        platform_event.source.platform.value = "discord"
        chat_whitespace_event = self._make_event("private")
        chat_whitespace_event.source.chat_id = " 123"
        platform_whitespace_event = self._make_event("private")
        platform_whitespace_event.source.platform.value = "telegram "

        assert await runner._handle_message(prefix_event) == "This command is not available in this chat."
        assert await runner._handle_message(platform_event) == "This command is not available in this chat."
        assert await runner._handle_message(chat_whitespace_event) == "This command is not available in this chat."
        assert await runner._handle_message(platform_whitespace_event) == "This command is not available in this chat."

    @pytest.mark.asyncio
    async def test_alias_allowed_origins_denial_does_not_rewrite_event(self):
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = {
            "quick_commands": {
                "private": {
                    "type": "alias",
                    "target": "/usage",
                    "allowed_origins": ["telegram:999"],
                }
            }
        }
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)
        event = self._make_event("private", "sensitive")
        original_text = event.text

        result = await runner._handle_message(event)

        assert result == "This command is not available in this chat."
        assert event.text == original_text


    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        from gateway.run import GatewayRunner
        import asyncio
        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = {"quick_commands": {"slow": {"type": "exec", "command": "sleep 100"}}}
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)

        event = self._make_event("slow")
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await runner._handle_message(event)
        assert result is not None
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_gateway_config_object_supports_quick_commands(self):
        from gateway.config import GatewayConfig
        from gateway.run import GatewayRunner

        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = GatewayConfig(
            quick_commands={"limits": {"type": "exec", "command": "echo ok"}}
        )
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)

        event = self._make_event("limits")
        result = await runner._handle_message(event)
        assert result == "ok"
