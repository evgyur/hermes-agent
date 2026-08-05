#!/usr/bin/env python3
"""Fail-closed policy check for the Human20 Telegram Hermes gateway."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path("/home/human20team/.hermes/config.yaml")
DEFAULT_HERMES_ROOT = Path("/home/human20team/apps/hermes-agent")
AUTHORITY_ROUTING_CHAT = "-1003770669948"
DIRECT_MENTION_SHARED_CHAT = "-1003928061649"


def nested(mapping: dict[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def positive_int_at_most(value: Any, limit: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= limit


def string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def validate(config: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    max_turns = nested(config, "agent", "max_turns")
    if not positive_int_at_most(max_turns, 200):
        failures.append("agent.max_turns must be between 1 and 200")

    progress = nested(config, "display", "platforms", "telegram", "tool_progress")
    if not isinstance(progress, str) or progress.strip().lower() != "off":
        failures.append("display.platforms.telegram.tool_progress must be the string 'off'")

    telegram = config.get("telegram")
    if not isinstance(telegram, dict):
        failures.append("telegram section is required")
    else:
        require_mention = string_set(telegram.get("require_mention_chats"))
        reply_disabled = string_set(telegram.get("reply_trigger_disabled_chats"))
        ignore_other_replies = string_set(telegram.get("ignore_other_bot_replies_chats"))
        free_response = string_set(telegram.get("free_response_chats"))
        if AUTHORITY_ROUTING_CHAT not in require_mention:
            failures.append(
                f"telegram.require_mention_chats must include {AUTHORITY_ROUTING_CHAT}"
            )
        if AUTHORITY_ROUTING_CHAT in reply_disabled:
            failures.append(
                "telegram.reply_trigger_disabled_chats must exclude "
                f"{AUTHORITY_ROUTING_CHAT}"
            )
        if AUTHORITY_ROUTING_CHAT not in ignore_other_replies:
            failures.append(
                "telegram.ignore_other_bot_replies_chats must include "
                f"{AUTHORITY_ROUTING_CHAT}"
            )
        if AUTHORITY_ROUTING_CHAT in free_response:
            failures.append(
                f"telegram.free_response_chats must exclude {AUTHORITY_ROUTING_CHAT}"
            )

        allowed_shared_groups = string_set(
            nested(telegram, "extra", "team_allowed_group_chat_ids")
        )
        per_chat_allow = telegram.get("per_chat_group_allow_from")
        direct_chat_users = (
            string_set(per_chat_allow.get(DIRECT_MENTION_SHARED_CHAT))
            if isinstance(per_chat_allow, dict)
            else set()
        )
        required_direct_users = {"617744661", "268754981"}
        for key, values in (
            ("telegram.require_mention_chats", require_mention),
            ("telegram.reply_trigger_disabled_chats", reply_disabled),
            ("telegram.ignore_other_bot_replies_chats", ignore_other_replies),
            ("telegram.extra.team_allowed_group_chat_ids", allowed_shared_groups),
        ):
            if DIRECT_MENTION_SHARED_CHAT not in values:
                failures.append(f"{key} must include {DIRECT_MENTION_SHARED_CHAT}")
        if DIRECT_MENTION_SHARED_CHAT in free_response:
            failures.append(
                f"telegram.free_response_chats must exclude {DIRECT_MENTION_SHARED_CHAT}"
            )
        if not required_direct_users.issubset(direct_chat_users):
            failures.append(
                "telegram.per_chat_group_allow_from must allow Chip and Vlad in "
                f"{DIRECT_MENTION_SHARED_CHAT}"
            )
        prompts = telegram.get("channel_prompts")
        if not isinstance(prompts, dict) or not str(
            prompts.get(DIRECT_MENTION_SHARED_CHAT, "")
        ).strip():
            failures.append(
                f"telegram.channel_prompts must include {DIRECT_MENTION_SHARED_CHAT}"
            )

    guard = config.get("tool_loop_guardrails")
    if not isinstance(guard, dict):
        failures.append("tool_loop_guardrails section is required")
        return failures

    if guard.get("warnings_enabled") is not True:
        failures.append("tool_loop_guardrails.warnings_enabled must be true")
    if guard.get("hard_stop_enabled") is not True:
        failures.append("tool_loop_guardrails.hard_stop_enabled must be true")

    limits = {
        "exact_failure": 2,
        "same_tool_failure": 4,
        "idempotent_no_progress": 2,
    }
    hard_stop = guard.get("hard_stop_after")
    if not isinstance(hard_stop, dict):
        failures.append("tool_loop_guardrails.hard_stop_after section is required")
    else:
        for key, limit in limits.items():
            if not positive_int_at_most(hard_stop.get(key), limit):
                failures.append(f"tool_loop_guardrails.hard_stop_after.{key} must be between 1 and {limit}")

    return failures


def validate_runtime(root: Path) -> list[str]:
    source = root / "agent" / "tool_guardrails.py"
    try:
        tree = ast.parse(source.read_text())
    except Exception as exc:
        return [f"cannot inspect runtime tool guardrails: {type(exc).__name__}"]

    names: set[str] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "IDEMPOTENT_TOOL_NAMES"
            for target in node.targets
        ):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and value.args
        ):
            try:
                names = set(ast.literal_eval(value.args[0]))
            except Exception:
                names = None
        break

    if names is None:
        return ["cannot parse IDEMPOTENT_TOOL_NAMES from runtime"]

    required = {"skill_view", "skills_list"}
    missing = sorted(required - names)
    failures = [f"runtime IDEMPOTENT_TOOL_NAMES is missing {name}" for name in missing]

    routing_surfaces = {
        root / "gateway" / "config.py": {
            "TELEGRAM_IGNORE_OTHER_BOT_REPLIES_CHATS",
            "reply_trigger_disabled_chats",
            "require_mention_chats",
        },
        root / "gateway" / "platforms" / "telegram.py": {
            "_other_bot_reply_excludes_self",
            "_telegram_reply_trigger_disabled_chats",
            "_telegram_require_mention_chats",
            "team_allowed_group_chat_ids",
            "bot_command",
        },
        root / "plugins" / "platforms" / "telegram" / "adapter.py": {
            "_other_bot_reply_excludes_self",
            "_telegram_reply_trigger_disabled_chats",
            "_telegram_require_mention_chats",
            "team_allowed_group_chat_ids",
            "bot_command",
        },
    }
    for surface, markers in routing_surfaces.items():
        try:
            text = surface.read_text()
        except Exception as exc:
            failures.append(f"cannot inspect routing surface {surface}: {type(exc).__name__}")
            continue
        for marker in sorted(markers):
            if marker not in text:
                failures.append(f"routing surface {surface} is missing {marker}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--hermes-root", type=Path, default=DEFAULT_HERMES_ROOT)
    args = parser.parse_args()

    try:
        loaded = yaml.safe_load(args.config.read_text()) or {}
    except Exception as exc:
        print(f"human20bot-loop-policy=fail reason=config-read-error type={type(exc).__name__}")
        return 1

    if not isinstance(loaded, dict):
        print("human20bot-loop-policy=fail reason=config-root-not-mapping")
        return 1

    failures = validate(loaded) + validate_runtime(args.hermes_root)
    if failures:
        print("human20bot-loop-policy=fail")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(
        "human20bot-loop-policy=ok max_turns<=200 exact_failure<=2 "
        "same_tool<=4 no_progress<=2 telegram_progress=off "
        f"authority_chat={AUTHORITY_ROUTING_CHAT} reply_triggers=enabled "
        f"direct_mention_shared_chat={DIRECT_MENTION_SHARED_CHAT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
