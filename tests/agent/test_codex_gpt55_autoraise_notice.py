"""Codex gpt-5.5 compaction autoraise should not spam gateway chats."""

from unittest.mock import patch

from run_agent import AIAgent


def test_codex_gpt55_autoraise_is_cli_only_not_gateway_warning():
    """The 85% threshold autoraise is useful, but its notice is informational.

    Gateway sessions create separate agents for many chats/topics, so storing
    this as ``_compression_warning`` makes Telegram/Discord receive the same
    status line repeatedly. Real compression warnings still use that replay
    path; this notice must not.
    """
    cfg = {
        "compression": {
            "enabled": True,
            "threshold": 0.50,
            "codex_gpt55_autoraise": True,
        },
        "agent": {},
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
        "tools": {},
    }

    with patch("hermes_cli.config.load_config", return_value=cfg):
        agent = AIAgent(
            model="gpt-5.5",
            provider="openai-codex",
            api_key="test",
            base_url="https://chatgpt.com/backend-api/codex",
            quiet_mode=True,
            skip_memory=True,
            skip_context_files=True,
            enabled_toolsets=[],
        )

    assert getattr(agent, "_compression_threshold_autoraised") == {"from": 0.50, "to": 0.85}
    assert getattr(agent, "context_compressor").threshold_percent == 0.85
    assert getattr(agent, "_compression_warning") is None
