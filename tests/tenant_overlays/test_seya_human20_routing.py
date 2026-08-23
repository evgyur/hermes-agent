"""Provider-free contract checks for Seya's tenant-local Human20 routing."""

from pathlib import Path

import yaml

from agent.prompt_builder import build_context_files_prompt


OVERLAY = (
    Path(__file__).resolve().parents[2] / "tenant-overlays" / "seya" / "AGENTS.md"
)
CONFIG = OVERLAY.with_name("candidate-config.yaml")


def _render_overlay(tmp_path: Path) -> str:
    (tmp_path / "AGENTS.md").write_text(OVERLAY.read_text(encoding="utf-8"), encoding="utf-8")
    prompt = build_context_files_prompt(cwd=str(tmp_path), skip_soul=True)
    return " ".join(prompt.split())


def test_workshop_and_sreda_requests_are_mcp_first(tmp_path: Path) -> None:
    prompt = _render_overlay(tmp_path)

    assert "mcp__seya__*" in prompt
    assert "workshop, lesson, meeting, transcript, digest, progress, or homework" in prompt
    assert "Sreda content, resource, or prompt request" in prompt
    assert "answer from MCP" in prompt


def test_complete_mcp_result_forbids_external_research(tmp_path: Path) -> None:
    prompt = _render_overlay(tmp_path)

    assert "do not call generic web search, a browser, or Computer Use" in prompt
    assert "a request that MCP fully answers stops after MCP" in prompt


def test_missing_external_fact_keeps_one_bounded_fallback(tmp_path: Path) -> None:
    prompt = _render_overlay(tmp_path)

    assert "bounded web search only for that missing external or current fact" in prompt
    assert "one focused external-research pass" in prompt


def test_overlay_preserves_employee_telegram_isolation(tmp_path: Path) -> None:
    prompt = _render_overlay(tmp_path)

    assert "telegram-chip/scripts/probe_identity.py" in prompt
    assert "Never connect to any personal Telegram runtime" in prompt
    assert "Telegram Desktop, Telegram Web" in prompt


def test_candidate_config_is_seya_only_and_has_exact_deadlines() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    assert config["tenant"] == "milolika"
    assert config["host"] == "hel1"
    assert config["service"] == "milolika-hermes-gateway.service"
    assert config["release_policy"]["install_scope"] == "seya-only"
    assert config["release_policy"]["require_explicit_production_approval"] is True
    assert config["config_patch"]["agent"]["run_budget_seconds"] == 300
    assert config["config_patch"]["environment"]["HERMES_AGENT_NOTIFY_INTERVAL"] == "75"
    assert config["rollback"]["release"].startswith(
        "59a840eb7165c4a2d9d169e8039cbd822df650ee"
    )
