"""Regression contract for the canonical bundled hermes-agent hub skill."""

from __future__ import annotations

import re
from pathlib import Path

from agent.skill_utils import parse_frontmatter


ROOT = Path(__file__).resolve().parents[2] / "skills" / "autonomous-ai-agents" / "hermes-agent"
SKILL = ROOT / "SKILL.md"


def test_hermes_agent_root_stays_within_progressive_disclosure_budget():
    size = SKILL.stat().st_size
    assert 8_000 <= size <= 12_000


def test_hermes_agent_frontmatter_trigger_is_narrow_and_prompt_safe():
    frontmatter, _ = parse_frontmatter(SKILL.read_text(encoding="utf-8"))
    description = str(frontmatter["description"])
    assert len(description) <= 60
    assert "Hermes Agent" in description
    assert "code" not in description.lower()
    assert "bug" not in description.lower()


def test_every_root_reference_and_template_link_exists():
    text = SKILL.read_text(encoding="utf-8")
    links = set(re.findall(r"`((?:references|templates)/[^`]+)`", text))
    assert links
    missing = [relative for relative in sorted(links) if not (ROOT / relative).is_file()]
    assert missing == []


def test_representative_tasks_route_to_cold_references():
    text = SKILL.read_text(encoding="utf-8")
    expected = {
        "| CLI commands": "references/cli-reference.md",
        "| Provider setup": "references/providers-and-models.md",
        "| config.yaml sections": "references/configuration.md",
        "| Delegation, cron, curator, kanban": "references/background-systems.md",
        "| MCP servers": "references/native-mcp.md",
        "| A desktop app UI element": "references/desktop-plugins.md",
        "| Debugging: voice, tools missing, gateway": "references/troubleshooting.md",
        "| Contributing code": "references/contributor-guide.md",
    }
    for task_fragment, reference in expected.items():
        line = next(line for line in text.splitlines() if task_fragment in line)
        assert reference in line


def test_root_preserves_docs_and_safety_boundaries():
    text = SKILL.read_text(encoding="utf-8")
    for required in (
        "https://hermes-agent.nousresearch.com/docs/",
        "Check the live repository and official docs",
        "Never break prompt caching",
        "Secrets in `.env`, settings in `config.yaml`",
        "get_hermes_home()",
    ):
        assert required in text
