from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from hermes_cli import power
from hermes_cli.power_variants import (
    employee_env_values,
    install_rentals_bundle,
    load_employee_overlay,
    validate_h20_identity,
)


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps({"customer_id": "employee-1", "status": "active"}).encode()


def test_employee_overlay_contains_mila_routing_without_secrets():
    overlay = load_employee_overlay(Path(__file__).resolve().parents[2])
    assert overlay["model"]["default"] == "h20-gpt"
    assert overlay["fallback_providers"][0]["model"] == "mmfast"
    assert overlay["delegation"]["model"] == "mmfast"
    assert overlay["web"]["backend"] == "perplexity"
    assert overlay["stt"]["groq"]["model"] == "whisper-large-v3-turbo"
    assert overlay["agent"]["max_turns"] == 200
    rendered = yaml.safe_dump(overlay)
    assert "CUSTOMER_KEY_PLACEHOLDER" not in rendered
    assert "sk-" not in rendered


def test_employee_env_values_use_one_customer_key_for_compatibility_aliases():
    values = employee_env_values("customer-key", "https://keys.human20.app/")
    assert values["H20_KEYS_API_KEY"] == "customer-key"
    assert values["PERPLEXITY_API_KEY"] == "customer-key"
    assert values["GROQ_API_KEY"] == "customer-key"
    assert values["PERPLEXITY_API_URL"].endswith("/v1/chat/completions")
    assert values["GROQ_BASE_URL"].endswith("/proxy/groq/openai/v1")


def test_validate_h20_identity_fails_closed_on_wrong_customer(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Response())
    with __import__("pytest").raises(ValueError, match="different customer"):
        validate_h20_identity("customer-key", expected_customer_id="employee-2")


def test_install_rentals_bundle_copies_same_payload_for_variants(tmp_path):
    project = tmp_path / "project"
    skill = project / "bundles" / "rentals" / "skills" / "sample"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: sample\n---\n", encoding="utf-8")
    receipt = install_rentals_bundle(project, tmp_path / "home")
    assert receipt["skill_roots"] == ["sample"]
    assert (tmp_path / "home" / "skills" / "sample" / "SKILL.md").exists()


def test_powerpack_bundle_never_grants_tenant_access_to_chipcr_telegram():
    project = Path(__file__).resolve().parents[2]
    assert not (project / "bundles" / "rentals" / "skills" / "telegram-chip").exists()


def test_employee_install_validates_before_config_and_never_prints_key(monkeypatch, capsys):
    events = []
    monkeypatch.setattr(power, "validate_h20_identity", lambda *a, **k: events.append("validate") or {"customer_id": "employee-1"})
    monkeypatch.setattr(power, "apply_power_preset", lambda **k: events.append("config") or {"toolsets": []})
    monkeypatch.setattr(power, "install_rentals_bundle", lambda *a, **k: {"skill_roots": ["sample"]})
    monkeypatch.setattr(power, "save_employee_credentials", lambda *a, **k: events.append("credentials") or ["H20_KEYS_API_KEY"])
    monkeypatch.setattr(power, "get_project_root", lambda: Path("/project"))
    monkeypatch.setattr(power, "get_hermes_home", lambda: Path("/home/employee/.hermes"))
    monkeypatch.setattr(power.sys, "stdin", io.StringIO("customer-key\n"))
    args = SimpleNamespace(
        preset="employee",
        dry_run=False,
        json=False,
        employee_id="employee-1",
        h20_key_stdin=True,
        h20_base_url="https://keys.human20.app",
    )
    assert power.run_install(args) == 0
    assert events == ["validate", "config", "credentials"]
    output = capsys.readouterr().out
    assert "customer-key" not in output
    assert "employee-1" in output


def test_default_alias_resolves_to_rentals(monkeypatch, capsys):
    monkeypatch.setattr(power, "apply_power_preset", lambda **k: {"toolsets": []})
    monkeypatch.setattr(power, "install_rentals_bundle", lambda *a, **k: {"skill_roots": []})
    monkeypatch.setattr(power, "get_project_root", lambda: Path("/project"))
    monkeypatch.setattr(power, "get_hermes_home", lambda: Path("/tmp/hermes"))
    args = SimpleNamespace(preset="default", dry_run=False, json=True)
    assert power.run_install(args) == 0
    assert json.loads(capsys.readouterr().out)["preset"] == "rentals"
