#!/usr/bin/env python3
"""Fail-closed policy check for the Human20 Telegram Hermes gateway."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path("/home/human20team/.hermes/config.yaml")
DEFAULT_HERMES_ROOT = Path("/home/human20team/apps/hermes-agent")
AUTHORITY_ROUTING_CHAT = "-1003770669948"
DIRECT_MENTION_SHARED_CHAT = "-1003928061649"
CHIP_ONLY_SHARED_CHAT = "-1003598068116"
CHIP_TELEGRAM_ID = "617744661"
ROUTE_OWNER_TRAINING_MARKER = "HUMAN20_ROUTE_OWNER_PREFLIGHT_V1"
INSTALLED_RF_ROUTE_BROKER = Path("/usr/local/sbin/human20bot-rf-route")
INSTALLED_PROD_DEPLOY_BROKER = Path("/usr/local/sbin/human20bot-human20-prod-deploy")
PROD_DEPLOY_TRAINING_MARKER = "HUMAN20_EMAIL_DEPLOY_BROKER_V1"

BROADCAST_POLICY_MARKER = "BROADCAST-OPERATOR-POLICY-V3"
LEGACY_EMAIL_BROADCAST_POLICY_MARKERS = (
    "MCP email-инструменты не умеют массовую отправку",
    "MCP email tools do not support production mass send",
    "дай оператору ссылку https://team.20.business/broadcasts",
    "give the operator https://team.20.business/broadcasts",
)
EMAIL_BROADCAST_REQUIRED_MARKERS = (
    "human20_email_broadcast_dry_run",
    "human20_email_broadcast_test",
    "human20_email_broadcast_send",
    "requestId",
    "manifestHash",
    "approvalMessageId",
)
EMAIL_BROADCAST_IDENTIFIER_ONLY_MARKERS = (
    "accepts only requestId, manifestHash, approvalMessageId",
    "never include segmentId, subject, text, html, recipients, testEmail in send",
)
TG_VK_BROADCAST_REQUIRED_MARKERS = (
    "Telegram rail",
    "VK rail",
    "@human20salesbot",
    "@human20helperbot",
    "/srv/human20team/human20-salesbot/current/backend-v2/backend_v2/waitlist_bot/broadcast.py",
    "platform=Telegram",
    "platform=VK",
    "/sales-inbox/bot/vk-broadcast-audience",
    "peer_id",
    "frozen manifest preview",
    "test_send",
    "confirm/run",
)
BROADCAST_ROUTING_REQUIRED_MARKERS = (
    "`тг бот`/Telegram → Telegram rail",
    "`VK`/`ВК` → VK rail",
    "`email`/`почта` → Email rail",
    "Не используй Team20 email MCP для Telegram",
    "Не используй Telegram Bot API или Team20 email MCP для VK",
)


def nested(mapping: dict[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def positive_int_at_most(value: Any, limit: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= limit


def positive_int_at_least(value: Any, limit: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= limit


def string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}



def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for child in value.values():
            strings.extend(_iter_strings(child))
        return strings
    if isinstance(value, list):
        strings: list[str] = []
        for child in value:
            strings.extend(_iter_strings(child))
        return strings
    return []


def validate_broadcast_profile_contract(config: dict[str, Any]) -> list[str]:
    """Reject stale Human20Bot prompt policy that mixes Telegram, VK and email rails."""
    text = "\n".join(_iter_strings(config))
    failures: list[str] = []
    for marker in LEGACY_EMAIL_BROADCAST_POLICY_MARKERS:
        if marker in text:
            failures.append(f"legacy email broadcast policy is still present in profile: {marker}")
    for marker in EMAIL_BROADCAST_REQUIRED_MARKERS:
        if marker not in text:
            failures.append(f"profile is missing governed email broadcast marker: {marker}")
    if not all(marker in text for marker in EMAIL_BROADCAST_IDENTIFIER_ONLY_MARKERS):
        failures.append("profile is missing identifier-only email broadcast send contract")
    for marker in TG_VK_BROADCAST_REQUIRED_MARKERS:
        if marker not in text:
            failures.append(f"profile is missing Telegram/VK broadcast rail marker: {marker}")
    for marker in BROADCAST_ROUTING_REQUIRED_MARKERS:
        if marker not in text:
            failures.append(f"profile is missing channel routing guard: {marker}")
    return failures


def validate_email_broadcast_profile_contract(config: dict[str, Any]) -> list[str]:
    return validate_broadcast_profile_contract(config)

def validate(config: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    failures.extend(validate_broadcast_profile_contract(config))

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
            ("telegram.ignore_other_bot_replies_chats", ignore_other_replies),
            ("telegram.extra.team_allowed_group_chat_ids", allowed_shared_groups),
        ):
            if DIRECT_MENTION_SHARED_CHAT not in values:
                failures.append(f"{key} must include {DIRECT_MENTION_SHARED_CHAT}")
        if DIRECT_MENTION_SHARED_CHAT in reply_disabled:
            failures.append(
                "telegram.reply_trigger_disabled_chats must exclude "
                f"{DIRECT_MENTION_SHARED_CHAT}"
            )
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

        chip_only_users = (
            string_set(per_chat_allow.get(CHIP_ONLY_SHARED_CHAT))
            if isinstance(per_chat_allow, dict)
            else set()
        )
        for key, values in (
            ("telegram.require_mention_chats", require_mention),
            ("telegram.ignore_other_bot_replies_chats", ignore_other_replies),
            ("telegram.extra.team_allowed_group_chat_ids", allowed_shared_groups),
        ):
            if CHIP_ONLY_SHARED_CHAT not in values:
                failures.append(f"{key} must include {CHIP_ONLY_SHARED_CHAT}")
        if CHIP_ONLY_SHARED_CHAT in reply_disabled:
            failures.append(
                "telegram.reply_trigger_disabled_chats must exclude "
                f"{CHIP_ONLY_SHARED_CHAT}"
            )
        if CHIP_ONLY_SHARED_CHAT in free_response:
            failures.append(
                f"telegram.free_response_chats must exclude {CHIP_ONLY_SHARED_CHAT}"
            )
        if chip_only_users != {CHIP_TELEGRAM_ID}:
            failures.append(
                "telegram.per_chat_group_allow_from must allow only Chip in "
                f"{CHIP_ONLY_SHARED_CHAT}"
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
        "idempotent_no_progress": 2,
    }
    hard_stop = guard.get("hard_stop_after")
    if not isinstance(hard_stop, dict):
        failures.append("tool_loop_guardrails.hard_stop_after section is required")
    else:
        for key, limit in limits.items():
            if not positive_int_at_most(hard_stop.get(key), limit):
                failures.append(f"tool_loop_guardrails.hard_stop_after.{key} must be between 1 and {limit}")
        if not positive_int_at_least(hard_stop.get("same_tool_failure"), 1000):
            failures.append(
                "tool_loop_guardrails.hard_stop_after.same_tool_failure must be at least 1000; "
                "varying arguments are not a loop"
            )

    return failures


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def validate_queued_media_delivery(root: Path) -> list[str]:
    """Fail closed if an update drops queued native-media delivery safety."""
    source = root / "gateway" / "run.py"
    try:
        text = source.read_text()
        tree = ast.parse(text)
    except Exception as exc:
        return [f"cannot inspect queued media runtime: {type(exc).__name__}"]

    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    failures: list[str] = []

    strip_fn = functions.get("_strip_response_attachments_for_direct_send")
    if strip_fn is None or not any(
        isinstance(node, ast.Call) and _call_name(node) == "extract_media"
        for node in ast.walk(strip_fn) if strip_fn is not None
    ):
        failures.append("queued media runtime must extract MEDIA markers before direct text send")

    deliver_fn = functions.get("_deliver_queued_first_response")
    if deliver_fn is None:
        failures.append("queued media runtime is missing _deliver_queued_first_response")
    else:
        params = {arg.arg for arg in (*deliver_fn.args.args, *deliver_fn.args.kwonlyargs)}
        for required in ("text_already_delivered", "deliver_media"):
            if required not in params:
                failures.append(f"queued media helper is missing {required}")
        calls = {_call_name(node) for node in ast.walk(deliver_fn) if isinstance(node, ast.Call)}
        for required in (
            "_strip_response_attachments_for_direct_send",
            "_deliver_media_from_response",
        ):
            if required not in calls:
                failures.append(f"queued media helper is missing {required}")

    policy_fn = functions.get("_queued_first_response_delivery_policy")
    if policy_fn is None:
        failures.append("queued media runtime is missing failed-turn delivery policy")
    else:
        try:
            isolated = ast.Module(body=[policy_fn], type_ignores=[])
            ast.fix_missing_locations(isolated)
            namespace: dict[str, Any] = {}
            exec(compile(isolated, str(source), "exec"), namespace)
            policy = namespace["_queued_first_response_delivery_policy"]
            expected = {
                (False, False): (False, True),
                (True, False): (True, True),
                (False, True): (False, False),
                (True, True): (False, False),
            }
            for (final_text_delivered, failed), result in expected.items():
                if policy(
                    final_text_delivered=final_text_delivered,
                    failed=failed,
                ) != result:
                    failures.append("queued media failed-turn delivery policy is unsafe")
                    break
        except Exception as exc:
            failures.append(f"cannot evaluate queued media delivery policy: {type(exc).__name__}")

    run_agent_candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    run_agent = next(
        (
            node
            for node in run_agent_candidates
            if any(
                isinstance(call, ast.Call)
                and _call_name(call) == "_deliver_queued_first_response"
                for call in ast.walk(node)
            )
        ),
        None,
    )
    if run_agent is None:
        failures.append("queued media runtime is missing queued fallback call site")
        return failures

    policy_call_ok = False
    delivery_call_ok = False
    for node in ast.walk(run_agent):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if name == "_queued_first_response_delivery_policy":
            final_value = keywords.get("final_text_delivered")
            failed_value = keywords.get("failed")
            policy_call_ok = (
                isinstance(final_value, ast.Name)
                and final_value.id == "_already_streamed"
                and failed_value is not None
                and ast.unparse(failed_value) == "bool(_delivery_result.get('failed'))"
            )
        elif name == "_deliver_queued_first_response":
            text_value = keywords.get("text_already_delivered")
            media_value = keywords.get("deliver_media")
            if (
                isinstance(text_value, ast.Name)
                and text_value.id == "_text_already_delivered"
                and isinstance(media_value, ast.Name)
                and media_value.id == "_deliver_media"
            ):
                delivery_call_ok = True
        elif name == "send" and node.args:
            if isinstance(node.args[0], ast.Name) and node.args[0].id == "first_response":
                failures.append("queued fallback directly sends raw first_response")

    if not policy_call_ok:
        failures.append("queued fallback does not bind streaming state to failed-turn policy")
    if not delivery_call_ok:
        failures.append("queued fallback bypasses the guarded text/media delivery decision")
    return failures


def validate_broadcast_format_contract(root: Path) -> list[str]:
    """Fail closed if an update drops Telegram formatting/link safeguards."""
    skill = (
        root
        / "human20bot"
        / "skills"
        / "project"
        / "human20-broadcast-operator"
        / "SKILL.md"
    )
    verifier = skill.parent / "scripts" / "verify_telegram_broadcast_readback.py"
    failures: list[str] = []
    try:
        skill_text = skill.read_text()
    except Exception as exc:
        return [f"cannot inspect broadcast operator skill: {type(exc).__name__}"]
    try:
        verifier_text = verifier.read_text()
    except Exception as exc:
        return [f"cannot inspect broadcast readback verifier: {type(exc).__name__}"]

    for marker in (
        "inline URL button alone does not satisfy",
        "Do not proceed to approval or mass send when this validator fails",
        "scripts/verify_telegram_broadcast_readback.py",
        "human20_email_broadcast_send",
        "registered_no_workshop_no_ready_agent",
        "manifest_sha256: <manifestHash>",
        "subject: <exact email subject>",
        "Do not add, remove, reorder, or contradict",
        "ОТПРАВИТЬ <manifestHash>",
        "do not fall back to the generic REST tool or direct provider calls",
        PROD_DEPLOY_TRAINING_MARKER,
        "sudo -n /usr/local/sbin/human20bot-human20-prod-deploy",
        "Ask no one to remind you to continue",
    ):
        if marker not in skill_text:
            failures.append(f"broadcast operator skill is missing format contract: {marker}")

    approval_card = (
        "```text\n"
        "РАССЫЛКА <requestId> · AWAITING_APPROVAL\n"
        "subject: <exact email subject>\n"
        "segment: <segmentId>\n"
        "recipient_count: <count>\n"
        "manifest_sha256: <manifestHash>\n"
        "```"
    )
    if approval_card not in skill_text:
        failures.append("broadcast operator skill is missing the exact approval card")

    email_section = skill_text.split("## Email broadcasts through Team20 MCP", 1)[-1].lower()
    for forbidden in (
        "no production mass-send tool",
        "production mail send is forbidden",
        "no mass-send tool",
    ):
        if forbidden in email_section:
            failures.append(f"broadcast operator skill has contradictory send contract: {forbidden}")

    for marker in (
        "require_text_link_url",
        "button alone does not satisfy the hyperlink contract",
        "return 0 if result[\"ok\"] else 2",
    ):
        if marker not in verifier_text:
            failures.append(f"broadcast readback verifier is missing fail-closed marker: {marker}")
    return failures


def validate_route_owner_training(root: Path, config: dict[str, Any]) -> list[str]:
    """Keep the live Human20 route-owner training and broker fail-closed."""
    failures: list[str] = []
    skill_root = root / "human20bot" / "skills" / "project"
    required = {
        skill_root / "human20-app" / "SKILL.md": ROUTE_OWNER_TRAINING_MARKER,
        skill_root / "human20-public-route-ops" / "SKILL.md": ROUTE_OWNER_TRAINING_MARKER,
        skill_root / "human20-public-route-ops" / "references" / "regressions.md": "orphaned-runtime-artifact",
    }
    for path, marker in required.items():
        try:
            text = path.read_text()
        except Exception as exc:
            failures.append(f"cannot inspect route-owner training {path}: {type(exc).__name__}")
            continue
        if marker not in text:
            failures.append(f"route-owner training {path} is missing {marker}")

    source_broker = root / "human20bot" / "ops" / "human20bot-rf-route"
    try:
        source_bytes = source_broker.read_bytes()
        installed_bytes = INSTALLED_RF_ROUTE_BROKER.read_bytes()
    except Exception as exc:
        failures.append(f"cannot inspect installed RF route broker: {type(exc).__name__}")
    else:
        if source_bytes != installed_bytes:
            failures.append("installed RF route broker does not match reviewed repository source")

    prompts = nested(config, "telegram", "channel_prompts")
    prompt = prompts.get(AUTHORITY_ROUTING_CHAT, "") if isinstance(prompts, dict) else ""
    if ROUTE_OWNER_TRAINING_MARKER not in str(prompt):
        failures.append(
            f"telegram.channel_prompts[{AUTHORITY_ROUTING_CHAT}] must include {ROUTE_OWNER_TRAINING_MARKER}"
        )
    return failures


def validate_prod_deploy_broker(root: Path) -> list[str]:
    """Keep the approval-bound RF deploy broker installed, exact, and runnable."""
    source = root / "human20bot" / "ops" / "human20bot_human20_prod_deploy.py"
    failures: list[str] = []
    try:
        source_bytes = source.read_bytes()
        installed_bytes = INSTALLED_PROD_DEPLOY_BROKER.read_bytes()
        installed_stat = INSTALLED_PROD_DEPLOY_BROKER.stat()
    except Exception as exc:
        return [f"cannot inspect installed Human20 prod deploy broker: {type(exc).__name__}"]
    if source_bytes != installed_bytes:
        failures.append("installed Human20 prod deploy broker does not match reviewed repository source")
    if installed_stat.st_uid != 0 or installed_stat.st_mode & 0o022:
        failures.append("installed Human20 prod deploy broker must be root-owned and not group/world-writable")
    try:
        proc = subprocess.run(
            ["sudo", "-n", str(INSTALLED_PROD_DEPLOY_BROKER), "--self-test"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except Exception as exc:
        failures.append(f"cannot execute Human20 prod deploy broker self-test: {type(exc).__name__}")
    else:
        if proc.returncode != 0 or payload.get("ok") is not True:
            failures.append("Human20 prod deploy broker self-test failed through sudo policy")
        if payload.get("mode") != "approval-bound-exact-sha":
            failures.append("Human20 prod deploy broker mode is not approval-bound-exact-sha")
        if payload.get("fixed_branch") != "origin/prod":
            failures.append("Human20 prod deploy broker is not pinned to origin/prod")
        if payload.get("deploy_user") != "human20deploy":
            failures.append("Human20 prod deploy broker is not isolated under human20deploy")
        if payload.get("git_user") != "human20git" or payload.get("credential_isolation") is not True:
            failures.append("Human20 prod deploy broker lost Git/RF credential separation")
        if payload.get("minimal_environment") is not True or payload.get("single_attempt") is not True:
            failures.append("Human20 prod deploy broker lost minimal-env or single-attempt safety")
        if any(
            payload.get(marker) is not True
            for marker in (
                "sealed_worktree",
                "root_pinned_script",
                "root_guarded_receipts",
                "root_guarded_lock",
                "installed_path_validated",
                "sudo_scope_validated",
                "systemd_cgroup_execution",
            )
        ):
            failures.append("Human20 prod deploy broker lost source or receipt immutability")
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

    # Plugin-era Hermes has one bundled Telegram owner. Validate that exact
    # runtime surface instead of requiring the removed legacy gateway adapter.
    routing_surfaces = {
        root / "plugins" / "platforms" / "telegram" / "adapter.py": {
            "def _should_ignore_foreign_bot_reply",
            "def _telegram_has_scoped_mention_gate",
            "def _telegram_require_mention_chats",
            "ignore_other_bot_replies_chats",
            "require_mention_chats",
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
    failures.extend(validate_queued_media_delivery(root))
    failures.extend(validate_broadcast_format_contract(root))
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

    failures = (
        validate(loaded)
        + validate_runtime(args.hermes_root)
        + validate_route_owner_training(args.hermes_root, loaded)
        + validate_prod_deploy_broker(args.hermes_root)
    )
    if failures:
        print("human20bot-loop-policy=fail")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(
        "human20bot-loop-policy=ok max_turns<=200 exact_failure<=2 "
        "same_tool_cross_args_halt>=1000 no_progress<=2 telegram_progress=off "
        "queued_media=native-fail-closed broadcast_format=entities+body-link-fail-closed "
        "email_broadcast=telegram-manifest-approval+idempotent-send "
        "email_audience_repair=approval-bound-rf-deploy+auto-resume "
        "route_owner_preflight=v1 "
        f"authority_chat={AUTHORITY_ROUTING_CHAT} reply_triggers=enabled "
        f"mention_or_reply_shared_chat={DIRECT_MENTION_SHARED_CHAT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
