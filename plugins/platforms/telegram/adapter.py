"""Telegram platform plugin wrapper.

Private HEL1 keeps the adapter implementation in ``gateway.platforms.telegram``
for compatibility with older tests/imports. Upstream moved Telegram to the
plugin tree; this module bridges both shapes and exposes the plugin entrypoint.
"""

from __future__ import annotations

import os
from typing import Any

from gateway.platforms.telegram import *  # noqa: F401,F403
from gateway.platforms.telegram import TelegramAdapter, check_telegram_requirements


def _build_adapter(config):
    adapter = TelegramAdapter(config)
    try:
        adapter._notifications_mode = _resolve_notifications_mode()  # type: ignore[name-defined]
    except Exception:
        adapter._notifications_mode = "important"
    return adapter


def _is_connected(config) -> bool:
    token = getattr(config, "token", None)
    if not token:
        try:
            import hermes_cli.gateway as gateway_mod

            token = gateway_mod.get_env_value("TELEGRAM_BOT_TOKEN") or ""
        except Exception:
            token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    return bool(str(token).strip())


async def _standalone_send(
    pconfig,
    target: str,
    message: str,
    *,
    media_path: str | None = None,
    **metadata: Any,
):
    adapter = _build_adapter(pconfig)
    if hasattr(adapter, "initialize"):
        await adapter.initialize()
    try:
        if media_path and hasattr(adapter, "send_file"):
            return await adapter.send_file(target, media_path, caption=message or None, metadata=metadata or None)
        return await adapter.send(target, message, metadata=metadata or None)
    finally:
        if hasattr(adapter, "cleanup"):
            await adapter.cleanup()


def _apply_yaml_config(yaml_cfg: dict, telegram_cfg: dict) -> dict | None:
    extras: dict[str, Any] = {}
    telegram_cfg = telegram_cfg or {}
    extra = telegram_cfg.get("extra") or {}
    if isinstance(extra, dict):
        extras.update(extra)
    for key, value in telegram_cfg.items():
        if key not in {
            "enabled", "token", "extra", "allow_from", "allow_admin_from",
            "dm_policy", "group_policy", "reply_prefix", "reply_in_thread",
            "reply_to_mode", "unauthorized_dm_behavior", "notice_delivery",
            "require_mention", "channel_skill_bindings", "channel_prompts",
            "gateway_restart_notification",
        }:
            extras.setdefault(key, value)
    return extras or None


def interactive_setup() -> None:  # pragma: no cover - setup helper
    print("Set TELEGRAM_BOT_TOKEN or telegram.token in config.yaml")


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system."""
    ctx.register_platform(
        name="telegram",
        label="Telegram",
        adapter_factory=_build_adapter,
        check_fn=check_telegram_requirements,
        is_connected=_is_connected,
        required_env=["TELEGRAM_BOT_TOKEN"],
        install_hint="pip install 'hermes-agent[telegram]'",
        setup_fn=interactive_setup,
        apply_yaml_config_fn=_apply_yaml_config,
        allowed_users_env="TELEGRAM_ALLOWED_USERS",
        allow_all_env="TELEGRAM_ALLOW_ALL_USERS",
        cron_deliver_env_var="TELEGRAM_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        max_message_length=4096,
        emoji="✈️",
        allow_update_command=True,
    )
