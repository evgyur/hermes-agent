"""Executable Human20Bot full-power team-operator contract for P04."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from gateway.config import Platform
from gateway.session import SessionSource, build_session_key
from toolsets import resolve_toolset


CAPABILITY_CONFIG_SHA256 = "82f73b50867a7e2ccf5b20f002c43cbec26f397fa3db28dc6b081e79bfe8317c"
REQUIRED_FIRST_CLASS_TOOLS = {
    "terminal",
    "file",
    "web",
    "search",
    "delegation",
    "memory",
    "cronjob",
    "messaging",
}
REQUIRED_TELEGRAM_SCHEMA_TOOLS = {
    "terminal",
    "process",
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "web_search",
    "web_extract",
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "vision_analyze",
    "image_generate",
    "text_to_speech",
    "delegate_task",
    "memory",
    "cronjob",
}
REQUIRED_SERVICE_WRAPPERS = {"human20-memory-stack", "team20-kanban"}
DOC = Path(__file__).resolve().parents[2] / "docs/human20bot-team-operator.md"


def _profile_config() -> dict:
    path = Path.home() / ".hermes/config.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(value, dict)
    return value


def _capability_hash(config: dict) -> str:
    value = {
        "tools": config.get("tools"),
        "toolsets": config.get("toolsets"),
        "plugins": config.get("plugins"),
        "mcp_servers": config.get("mcp_servers"),
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source(*, actor: str, chat: str, thread: str | None, profile: str = "human20team") -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=chat,
        thread_id=thread,
        user_id=actor,
        chat_type="forum" if thread else "group",
        profile=profile,
    )


def test_authorized_profile_retains_sealed_full_capability_surface() -> None:
    config = _profile_config()

    assert _capability_hash(config) == CAPABILITY_CONFIG_SHA256
    assert REQUIRED_FIRST_CLASS_TOOLS <= set(config["tools"]["enabled"])
    assert config["tools"]["tool_search"]["enabled"] == "auto"
    assert config["toolsets"]
    assert config["plugins"]["enabled"]
    assert REQUIRED_TELEGRAM_SCHEMA_TOOLS <= set(resolve_toolset("hermes-telegram"))
    assert REQUIRED_TELEGRAM_SCHEMA_TOOLS <= set(resolve_toolset("hermes-cli"))
    assert REQUIRED_SERVICE_WRAPPERS <= set(config["mcp_servers"])


def test_group_topic_and_profile_session_keys_isolate_members() -> None:
    alpha = _source(actor="actor-alpha", chat="team-chat", thread="team-thread")
    beta = _source(actor="actor-beta", chat="team-chat", thread="team-thread")
    other_profile = _source(
        actor="actor-alpha",
        chat="team-chat",
        thread="team-thread",
        profile="other-profile",
    )

    alpha_key = build_session_key(
        alpha,
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
        profile=alpha.profile,
    )
    beta_key = build_session_key(
        beta,
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
        profile=beta.profile,
    )
    other_profile_key = build_session_key(
        other_profile,
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
        profile=other_profile.profile,
    )

    assert alpha_key != beta_key
    assert alpha_key != other_profile_key
    assert beta_key != other_profile_key


def test_dm_session_keys_do_not_collapse_members() -> None:
    alpha = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="dm-alpha",
        user_id="actor-alpha",
        chat_type="dm",
        profile="human20team",
    )
    beta = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="dm-beta",
        user_id="actor-beta",
        chat_type="dm",
        profile="human20team",
    )

    assert build_session_key(alpha, profile=alpha.profile) != build_session_key(
        beta,
        profile=beta.profile,
    )


def test_profile_config_keeps_per_user_group_and_topic_isolation() -> None:
    telegram = _profile_config()["telegram"]

    assert telegram["group_sessions_per_user"] is True
    assert telegram["thread_sessions_per_user"] is True


def test_operator_contract_separates_shared_project_memory_from_raw_dm() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "Shared project memory" in text
    assert "Raw DM turns" in text
    assert "must not be copied into shared artifacts" in text


def test_team_artifact_ownership_and_revocation_are_explicit() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "Cron and Goal artifacts explicitly created as team work are owned by the team profile" in text
    assert "They survive that member leaving" in text
    assert "Personal continuations remain actor-scoped and stop on revocation" in text
    assert "membership denial happens before agent or tool execution" in text


def test_full_tools_do_not_waive_protected_effect_approvals() -> None:
    text = DOC.read_text(encoding="utf-8")

    for protected_effect in (
        "payments and financial transfers",
        "access grants or membership changes",
        "production code, config, service or routing mutations",
        "mass, channel, public, or external sends",
    ):
        assert protected_effect in text
    assert "Full tools do not waive approval boundaries" in text


def test_contract_forbids_reduced_tier_and_parallel_control_planes() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "does not select a reduced member tier" in text
    assert "custom router, second scheduler, or alternate memory system" in text
