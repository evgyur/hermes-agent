from typing import Any, cast

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_source(), message_id="m1")


def _runner():
    from gateway.run import GatewayRunner

    runner = cast(Any, object.__new__(GatewayRunner))
    runner.config = {}
    runner.adapters = {}
    runner._session_model_overrides = {}
    runner._agent_cache_lock = None
    runner._agent_cache = {}
    runner._session_db = None
    runner._pending_model_notes = {}
    return runner


async def test_model_global_persists_api_mode(tmp_path, monkeypatch):
    """Global /model switches must persist the resolved transport too.

    Regression target: switching from Codex Responses to MiniMax via
    `/model MiniMax-M3 --provider minimax --global` updated provider/model/base_url
    but left `model.api_mode: codex_responses`, so the next gateway restart could
    route MiniMax through the wrong transport.
    """
    import gateway.run as gateway_run
    from hermes_cli.model_switch import ModelSwitchResult

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    (tmp_path / "config.yaml").write_text(
        "model:\n"
        "  default: gpt-5.5\n"
        "  provider: openai-codex\n"
        "  base_url: https://chatgpt.com/backend-api/codex\n"
        "  api_mode: codex_responses\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "model": {
                "default": "gpt-5.5",
                "provider": "openai-codex",
                "base_url": "https://chatgpt.com/backend-api/codex",
                "api_mode": "codex_responses",
            }
        },
    )

    switch_result = ModelSwitchResult(
        success=True,
        new_model="MiniMax-M3",
        target_provider="minimax",
        provider_label="MiniMax",
        base_url="https://api.minimax.io/anthropic",
        api_mode="anthropic_messages",
        is_global=True,
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.switch_model",
        lambda **_kwargs: switch_result,
    )

    saved = {}
    monkeypatch.setattr("hermes_cli.config.save_config", lambda cfg: saved.update(cfg))

    result = await _runner()._handle_model_command(
        _event("/model MiniMax-M3 --provider minimax --global")
    )

    assert result is not None
    assert "MiniMax-M3" in result
    assert saved["model"]["default"] == "MiniMax-M3"
    assert saved["model"]["provider"] == "minimax"
    assert saved["model"]["base_url"] == "https://api.minimax.io/anthropic"
    assert saved["model"]["api_mode"] == "anthropic_messages"


pytestmark = pytest.mark.asyncio
