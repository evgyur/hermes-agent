from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "packages" / "powerpack-gen2"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from powerpack_gen2 import doctor, installer  # noqa: E402


def test_installer_atomically_materializes_and_activates_boxed_plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("H20_KEYS_BASE_URL", "http://keys.example.invalid")
    monkeypatch.setenv("H20_KEYS_API_KEY", "test-key")
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {"enabled": ["google_meet"]},
                "toolsets": ["hermes-cli"],
                "web": {"extract_backend": "parallel"},
                "stt": {"enabled": True, "provider": "local"},
                "image_gen": {"provider": "openai-codex"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    receipt = installer.install(PACKAGE_ROOT, home, variant="employee", mode="gen2_only")
    target = home / "plugins" / "human20-powerpack-gen2"
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))

    assert receipt["status"] == "installed"
    assert receipt["plugin"] == "human20-powerpack-gen2"
    assert receipt["secrets_persisted"] is False
    assert doctor._inventory_report(target)["status"] == "PASS"
    assert config["plugins"]["enabled"] == ["google_meet", "human20-powerpack-gen2"]
    assert config["plugins"]["entries"]["human20-powerpack-gen2"] == {
        "settings": {
            "mode": "gen2_only",
            "variant": "employee",
        },
        "allow_tool_override": True,
    }
    assert config["toolsets"] == ["hermes-cli", "powerpack-gen2"]
    assert config["web"]["search_backend"] == "human20-perplexity"
    assert config["web"]["extract_backend"] == "parallel"
    assert config["stt"]["provider"] == "human20-keys-groq"
    assert config["image_gen"]["provider"] == "human20-keys-openai-codex"
    persisted = json.loads((home / "powerpack" / "receipts" / "human20-powerpack-gen2-install.json").read_text())
    assert persisted["package_sha256"] == receipt["package_sha256"]


def test_installer_preserves_default_web_provider_without_h20_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("H20_KEYS_BASE_URL", raising=False)
    monkeypatch.delenv("H20_KEYS_API_KEY", raising=False)
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {"enabled": []},
                "web": {"search_backend": "human20-perplexity", "extract_backend": "parallel"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    installer.install(PACKAGE_ROOT, home, variant="employee", mode="gen2_only")
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))

    assert "search_backend" not in config["web"]
    assert config["web"]["extract_backend"] == "parallel"


def test_installer_rejects_tampered_source_without_mutating_profile(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(PACKAGE_ROOT, source, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (source / "README.md").write_text("tampered\n", encoding="utf-8")
    home = tmp_path / ".hermes"
    home.mkdir()
    original = b"plugins:\n  enabled: []\n"
    (home / "config.yaml").write_bytes(original)

    with pytest.raises(RuntimeError, match="inventory"):
        installer.install(source, home, variant="employee", mode="gen2_only")

    assert (home / "config.yaml").read_bytes() == original
    assert not (home / "plugins" / "human20-powerpack-gen2").exists()


def test_materialized_plugin_is_discovered_and_loaded_by_current_hermes(monkeypatch, tmp_path):
    from agent import image_gen_registry, transcription_registry, web_search_registry
    import hermes_cli.plugins as plugin_api
    import tools.registry as tool_registry_module

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("plugins:\n  enabled: []\n", encoding="utf-8")
    installer.install(PACKAGE_ROOT, home, variant="employee", mode="gen2_only")
    monkeypatch.setenv("HERMES_HOME", str(home))
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    monkeypatch.setattr(plugin_api, "get_bundled_plugins_dir", lambda: bundled)
    clean_registry = tool_registry_module.ToolRegistry()
    monkeypatch.setattr(tool_registry_module, "registry", clean_registry)
    clean_registry.register(
        name="mem0g",
        toolset="mem0g",
        schema={"name": "mem0g", "description": "legacy", "parameters": {"type": "object"}},
        handler=lambda args, **kwargs: "legacy",
    )
    image_gen_registry._reset_for_tests()
    transcription_registry._reset_for_tests()
    web_search_registry._reset_for_tests()

    manager = plugin_api.PluginManager()
    manager.discover_and_load()

    loaded = manager._plugins["human20-powerpack-gen2"]
    assert loaded.enabled is True
    assert loaded.error is None
    assert set(loaded.tools_registered) == {"mem0g", "continuum_host", "chipmanager_telegram"}
    assert clean_registry.get_entry("mem0g").toolset == "powerpack-gen2"
