"""Fail-closed tests for the opt-in lean skill-routing canary."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.skill_routing import (
    CONSERVATIVE,
    LEAN_CANARY,
    PROTECTED_BOUNDARIES,
    agent_skill_routing_boundaries,
    protected_boundary_guidance,
    resolve_agent_skill_routing_policy,
    resolve_skill_routing_policy,
    skill_routing_guidance,
)


def _resolve(**overrides):
    values = {
        "config_policy": LEAN_CANARY,
        "task_override": LEAN_CANARY,
        "risk_class": "low",
        "protected_boundaries": (),
    }
    values.update(overrides)
    return resolve_skill_routing_policy(**values)


def test_lean_requires_every_task_scoped_gate():
    assert _resolve() == LEAN_CANARY
    assert _resolve(config_policy=CONSERVATIVE) == CONSERVATIVE
    assert _resolve(task_override=None) == CONSERVATIVE
    assert _resolve(risk_class=None) == CONSERVATIVE
    assert _resolve(protected_boundaries=None) == CONSERVATIVE
    assert _resolve(config_policy="unknown") == CONSERVATIVE
    assert _resolve(task_override="unknown") == CONSERVATIVE


def test_process_canary_env_needs_all_gates():
    agent = SimpleNamespace()
    base = {
        "HERMES_SKILL_ROUTING_CANARY_CONFIG": LEAN_CANARY,
        "HERMES_SKILL_ROUTING_TASK_OVERRIDE": LEAN_CANARY,
        "HERMES_SKILL_ROUTING_RISK_CLASS": "low",
    }
    assert resolve_agent_skill_routing_policy(agent, config_policy=CONSERVATIVE, environ=base) == CONSERVATIVE
    enabled = dict(base)
    enabled["HERMES_SKILL_ROUTING_PROTECTED_BOUNDARIES"] = ""
    assert resolve_agent_skill_routing_policy(agent, config_policy=CONSERVATIVE, environ=enabled) == LEAN_CANARY
    enabled["HERMES_SKILL_ROUTING_PROTECTED_BOUNDARIES"] = "auth"
    assert resolve_agent_skill_routing_policy(agent, config_policy=CONSERVATIVE, environ=enabled) == CONSERVATIVE


def test_explicit_boundary_context_is_fail_closed_and_prompt_safe():
    agent = SimpleNamespace()
    env = {"HERMES_SKILL_ROUTING_PROTECTED_BOUNDARIES": "auth,future-boundary"}
    boundaries = agent_skill_routing_boundaries(agent, environ=env)
    assert boundaries == ("auth", "future_boundary")
    guidance = protected_boundary_guidance(boundaries)
    assert "authentication/authorization security specialist" in guidance
    assert "the relevant protected specialist" in guidance
    assert "Do not substitute a generic workflow" in guidance
    assert "future-boundary" not in guidance


def test_protected_builder_guidance_does_not_change_registry(tmp_path, monkeypatch):
    from agent import prompt_builder as pb

    home = tmp_path / ".hermes"
    skill = home / "skills" / "demo" / "shaw"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: shaw\ndescription: coding governor\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    pb.clear_skills_system_prompt_cache()
    ordinary = pb.build_skills_system_prompt(routing_policy=CONSERVATIVE)
    protected = pb.build_skills_system_prompt(
        routing_policy=CONSERVATIVE,
        protected_boundaries=("auth",),
    )
    assert "Explicit protected-boundary gate" not in ordinary
    assert "authentication/authorization security specialist" in protected
    pattern = re.compile(r"<available_skills>.*?</available_skills>", re.DOTALL)
    ordinary_match = pattern.search(ordinary)
    protected_match = pattern.search(protected)
    assert ordinary_match is not None and protected_match is not None
    assert ordinary_match.group(0) == protected_match.group(0)


def test_explicit_agent_attributes_override_process_canary_env():
    agent = SimpleNamespace(
        skill_routing_policy_override=LEAN_CANARY,
        skill_routing_risk_class="protected",
        skill_routing_protected_boundaries=(),
    )
    env = {
        "HERMES_SKILL_ROUTING_CANARY_CONFIG": LEAN_CANARY,
        "HERMES_SKILL_ROUTING_TASK_OVERRIDE": LEAN_CANARY,
        "HERMES_SKILL_ROUTING_RISK_CLASS": "low",
        "HERMES_SKILL_ROUTING_PROTECTED_BOUNDARIES": "",
    }
    assert resolve_agent_skill_routing_policy(agent, config_policy=CONSERVATIVE, environ=env) == CONSERVATIVE


@pytest.mark.parametrize("boundary", sorted(PROTECTED_BOUNDARIES))
def test_every_protected_boundary_forces_conservative(boundary):
    assert _resolve(protected_boundaries=(boundary,)) == CONSERVATIVE


def test_unknown_boundary_label_fails_conservative():
    assert _resolve(protected_boundaries=("future_boundary",)) == CONSERVATIVE


def test_default_guidance_preserves_the_legacy_conservative_contract():
    guidance = skill_routing_guidance(CONSERVATIVE)
    assert guidance.startswith("## Skills (mandatory)\n")
    assert "even partially relevant" in guidance
    assert "Err on the side of loading" in guidance
    assert skill_routing_guidance("unknown") == guidance


def test_lean_guidance_deconflicts_related_metadata_and_generic_words():
    guidance = skill_routing_guidance(LEAN_CANARY)
    assert "explicit trigger is independently satisfied" in guidance
    assert "Related-skill metadata is discovery-only" in guidance
    assert "bug, fix, commit, push, ship, done, fully, or non-trivial" in guidance
    assert "without loading a coding skill" in guidance
    assert "Do not load the Shaw body merely because" in guidance
    assert "conservative specialist policy" in guidance


def test_router_policy_changes_guidance_not_registry(tmp_path, monkeypatch):
    from agent import prompt_builder as pb

    home = tmp_path / ".hermes"
    skill = home / "skills" / "demo" / "shaw"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: shaw\ndescription: coding governor\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    pb.clear_skills_system_prompt_cache()

    conservative = pb.build_skills_system_prompt(
        persist_snapshot=False, routing_policy=CONSERVATIVE
    )
    lean = pb.build_skills_system_prompt(
        persist_snapshot=False, routing_policy=LEAN_CANARY
    )
    pattern = re.compile(r"<available_skills>.*?</available_skills>", re.DOTALL)

    conservative_match = pattern.search(conservative)
    lean_match = pattern.search(lean)
    assert conservative_match is not None and lean_match is not None
    assert conservative_match.group(0) == lean_match.group(0)
    assert conservative != lean
    assert "shaw" in conservative and "shaw" in lean


def test_routing_fixture_matrix_is_complete_and_fail_closed():
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "skill_routing_matrix.json"
    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert len(fixtures) == 13
    assert len({fixture["id"] for fixture in fixtures}) == len(fixtures)

    for fixture in fixtures:
        required = set(fixture["required"])
        optional = set(fixture["optional"])
        forbidden = set(fixture["forbidden"])
        assert not (required & optional or required & forbidden or optional & forbidden)
        effective = resolve_skill_routing_policy(
            config_policy=LEAN_CANARY,
            task_override=LEAN_CANARY,
            risk_class=fixture["risk_class"],
            protected_boundaries=fixture["protected_boundaries"],
        )
        assert effective == fixture["canary_policy"], fixture["id"]
        if fixture["protected_boundaries"]:
            assert effective == CONSERVATIVE


def test_task_override_does_not_leak_between_resolutions():
    assert _resolve() == LEAN_CANARY
    assert resolve_skill_routing_policy(
        config_policy=LEAN_CANARY,
        task_override=None,
        risk_class="low",
        protected_boundaries=(),
    ) == CONSERVATIVE
