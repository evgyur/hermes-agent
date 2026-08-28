"""Tests for gateway /verbose command (config-gated tool progress cycling)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(
    text="/verbose",
    platform=Platform.TELEGRAM,
    user_id="12345",
    chat_id="12345",
    *,
    chat_type="dm",
    business_connection_id=None,
):
    """Build a MessageEvent for testing."""
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        chat_type=chat_type,
        user_name="testuser",
        business_connection_id=business_connection_id,
    )
    return MessageEvent(text=text, source=source)


def _make_runner():
    """Create a bare GatewayRunner without calling __init__."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._show_reasoning = False
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.hooks.loaded_hooks = []
    runner._session_db = None
    runner.config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                enabled=True,
                token="test",
                extra={"allow_admin_from": ["12345"]},
            )
        }
    )
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    return runner


class TestVerboseCommand:
    """Tests for _handle_verbose_command in the gateway."""

    @pytest.mark.asyncio
    async def test_disabled_by_default(self, tmp_path, monkeypatch):
        """When tool_progress_command is false, /verbose returns an info message."""
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text("display:\n  tool_progress: all\n", encoding="utf-8")

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        runner = _make_runner()
        result = await runner._handle_verbose_command(_make_event())

        assert "not enabled" in result.lower()
        assert "tool_progress_command" in result

    @pytest.mark.asyncio
    async def test_enabled_cycles_mode(self, tmp_path, monkeypatch):
        """When enabled, /verbose cycles tool_progress mode per-platform."""
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text(
            "display:\n  tool_progress_command: true\n  tool_progress: all\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        runner = _make_runner()
        result = await runner._handle_verbose_command(_make_event())

        # all -> verbose
        assert "VERBOSE" in result
        assert "telegram" in result.lower()  # per-platform feedback

        # Verify config was saved to display.platforms.telegram
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["display"]["platforms"]["telegram"]["tool_progress"] == "verbose"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "event",
        [
            _make_event(chat_id="-100123", chat_type="group"),
            _make_event(
                chat_id="777",
                chat_type="dm",
                business_connection_id="biz-external",
            ),
        ],
        ids=["public-group", "telegram-business"],
    )
    async def test_verbose_public_telegram_route_cannot_enable_platform_progress(
        self, tmp_path, monkeypatch, event
    ):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        original = "display:\n  tool_progress_command: true\n  tool_progress: all\n"
        config_path.write_text(original, encoding="utf-8")

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        runner = _make_runner()
        await runner._handle_verbose_command(event)

        assert config_path.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_verbose_non_owner_dm_cannot_mutate_profile_setting(
        self, tmp_path, monkeypatch
    ):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        original = "display:\n  tool_progress_command: true\n  tool_progress: all\n"
        config_path.write_text(original, encoding="utf-8")
        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        runner = _make_runner()
        result = await runner._handle_verbose_command(
            _make_event(user_id="99999", chat_id="99999")
        )

        assert "owner" in result.lower() or "admin" in result.lower()
        assert config_path.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_quoted_false_keeps_command_disabled(self, tmp_path, monkeypatch):
        """Quoted false must not enable the /verbose gateway command."""
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        config_path = hermes_home / "config.yaml"
        config_path.write_text(
            'display:\n  tool_progress_command: "false"\n  tool_progress: all\n',
            encoding="utf-8",
        )

        monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

        runner = _make_runner()
        result = await runner._handle_verbose_command(_make_event())

        assert "not enabled" in result.lower()
        assert "tool_progress_command" in result
