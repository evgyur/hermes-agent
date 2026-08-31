from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "packages" / "powerpack-gen2"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from powerpack_gen2 import cli, doctor  # noqa: E402


class FakeContext:
    def __init__(self, *, mode: str, variant: str = "employee", reject: str | None = None):
        self.settings = {"mode": mode, "variant": variant}
        self.reject = reject
        self.calls: list[tuple[str, str]] = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def set_config(self, key, value):
        self.settings[key] = value

    def _record(self, kind: str, name: str):
        self.calls.append((kind, name))
        return None if self.reject == f"{kind}:{name}" else object()

    def register_skill(self, name, path):
        assert Path(path).is_file()
        return self._record("skill", name)

    def register_hook(self, name, handler):
        assert callable(handler)
        return self._record("hook", name)

    def register_command(self, name, **kwargs):
        return self._record("command", name)

    def register_cli_command(self, name, **kwargs):
        return self._record("cli", name)

    def register_tool(self, *, name, **kwargs):
        return self._record("tool", name)

    def register_web_search_provider(self, provider):
        return self._record("web", provider.name)

    def register_image_gen_provider(self, provider):
        return self._record("image", provider.name)

    def register_transcription_provider(self, provider):
        return self._record("stt", provider.name)


def _reload_package():
    for name in list(sys.modules):
        if name == "powerpack_gen2" or name.startswith("powerpack_gen2."):
            del sys.modules[name]
    return importlib.import_module("powerpack_gen2")


def test_manifest_is_supported_standalone_plugin():
    manifest = yaml.safe_load((PACKAGE_ROOT / "plugin.yaml").read_text())
    metadata = doctor.load_manifest(PACKAGE_ROOT)
    assert manifest["kind"] == "standalone"
    assert metadata["version"] == manifest["version"]
    assert manifest["config_schema"]["mode"]["default"] == "disabled"
    assert manifest["config_schema"]["mode"]["choices"] == [
        "disabled",
        "compatibility",
        "gen2_only",
    ]


def test_status_reports_powerpack_and_hermes_versions_separately():
    powerpack_version = doctor.load_manifest(PACKAGE_ROOT)["version"]
    text = cli.slash_status(PACKAGE_ROOT, "employee", "gen2_only", "")

    assert f"Powerpack Gen2 v{powerpack_version}" in text
    assert f"Hermes v{cli.hermes_version()}" in text


def test_private_descendant_keeps_supported_upstream_ancestry(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    git("init")
    git("config", "user.name", "Powerpack Test")
    git("config", "user.email", "powerpack@example.invalid")
    (repo / "upstream.txt").write_text("upstream\n", encoding="utf-8")
    git("add", "upstream.txt")
    git("commit", "-m", "upstream")
    upstream = git("rev-parse", "HEAD")
    (repo / "private.txt").write_text("registered private patch\n", encoding="utf-8")
    git("add", "private.txt")
    git("commit", "-m", "private")

    report = doctor._git_host_report(repo, upstream)

    assert report["clean"] is True
    assert report["upstream_is_ancestor"] is True
    assert report["core_matches_upstream"] is False


@pytest.mark.skipif(os.name == "nt", reason="host doctor is POSIX-only")
def test_gen2_host_accepts_clean_private_descendant(monkeypatch, tmp_path):
    import getpass

    repo = tmp_path / "repo"
    venv = repo / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("managed runtime", encoding="utf-8")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    monkeypatch.setattr(
        doctor,
        "_git_host_report",
        lambda *_args: {
            "head": "candidate",
            "clean": True,
            "upstream_commit_present": True,
            "upstream_is_ancestor": True,
            "core_matches_upstream": False,
            "core_changed_count": 1,
            "core_changed_sample": ["registered-private.py"],
        },
    )
    monkeypatch.setattr(
        doctor,
        "_systemd_host_report",
        lambda _service: {
            "status": "PASS",
            "properties": {
                "Restart": "always",
                "RestartPreventExitStatus": "78",
                "RestartForceExitStatus": "75",
            },
            "process": {"cwd": str(repo), "exe": str(venv / "bin/python")},
            "contracts": [],
        },
    )
    monkeypatch.setattr(
        doctor,
        "_credential_report",
        lambda _mode, **_kwargs: {"status": "PASS", "present": {}, "values_exposed": False},
    )
    monkeypatch.setattr(
        doctor,
        "_sqlite_runtime_report",
        lambda *_args, **_kwargs: {
            "status": "PASS",
            "version": "3.53.1",
            "minimum": "3.53.1",
        },
    )
    monkeypatch.setattr(
        doctor,
        "_state_handle_report",
        lambda *_args, **_kwargs: {"status": "PASS", "deleted_count": 0, "kinds": []},
    )
    monkeypatch.setattr(
        doctor,
        "_operational_config_report",
        lambda *_args, **_kwargs: {
            "status": "PASS",
            "stt_drift": [],
            "cron_self_delivery": [],
        },
    )
    monkeypatch.setattr(
        doctor,
        "_process_pythonpath_report",
        lambda *_args, **_kwargs: {
            "status": "PASS",
            "present": False,
            "entry_count": 0,
            "outside_candidate_count": 0,
            "values_exposed": False,
        },
    )
    monkeypatch.setattr(
        doctor,
        "_process_runtime_env_report",
        lambda *_args, **_kwargs: {
            "status": "PASS",
            "virtual_env_exact": True,
            "venv_bin_first": True,
            "values_exposed": False,
        },
    )

    report = doctor.run_doctor(
        root=PACKAGE_ROOT,
        variant="employee",
        mode="gen2_only",
        upstream_sha=cli.PIN,
        ci=True,
        host=True,
        repo_root=repo,
        expected_user=getpass.getuser(),
        active_plugin_root=PACKAGE_ROOT,
    )
    checks = {item["name"]: item for item in report["checks"]}

    assert report["ok"] is True
    assert checks["host_supported_upstream_ancestor"]["status"] == "PASS"
    assert checks["host_core_matches_upstream"]["status"] == "FAIL"
    assert checks["host_core_matches_upstream"]["required"] is False
    assert checks["host_service_failure_policy"]["status"] == "PASS"
    assert checks["host_sqlite_runtime"]["status"] == "PASS"
    assert checks["host_deleted_state_handles"]["status"] == "PASS"
    assert checks["host_operational_config"]["status"] == "PASS"
    assert checks["host_pythonpath_identity"]["status"] == "PASS"
    assert checks["host_runtime_env_identity"]["status"] == "PASS"


def test_service_failure_policy_requires_upstream_restart_contract():
    passing = doctor._service_failure_policy_report(
        {
            "Restart": "always",
            "RestartPreventExitStatus": "78",
            "RestartForceExitStatus": "75",
        }
    )
    missing_fatal_stop = doctor._service_failure_policy_report(
        {
            "Restart": "on-failure",
            "RestartPreventExitStatus": "",
            "RestartForceExitStatus": "75",
        }
    )

    assert passing["status"] == "PASS"
    assert missing_fatal_stop["status"] == "FAIL"
    assert missing_fatal_stop["fatal_config_stops"] is False


def test_pythonpath_identity_rejects_stale_checkout_without_exposing_path(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    report = doctor._pythonpath_identity_report(
        b"PYTHONPATH=/srv/hermes-old\0OTHER=value\0",
        repository=candidate,
        process_cwd=candidate,
    )

    assert report == {
        "status": "FAIL",
        "present": True,
        "entry_count": 1,
        "outside_candidate_count": 1,
        "values_exposed": False,
    }
    assert "/srv/hermes-old" not in json.dumps(report)


def test_pythonpath_identity_accepts_empty_or_candidate_scoped_value(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    empty = doctor._pythonpath_identity_report(
        b"PYTHONPATH=\0",
        repository=candidate,
        process_cwd=candidate,
    )
    scoped = doctor._pythonpath_identity_report(
        f"PYTHONPATH={candidate}\0".encode(),
        repository=candidate,
        process_cwd=candidate,
    )

    assert empty["status"] == "PASS"
    assert scoped["status"] == "PASS"
    assert scoped["outside_candidate_count"] == 0


def test_runtime_env_identity_requires_exact_candidate_venv_first_on_path(tmp_path):
    del tmp_path
    venv = Path("/srv/hermes-candidate/venv")

    stale = doctor._runtime_env_identity_report(
        b"VIRTUAL_ENV=/srv/hermes-old/venv\0PATH=/usr/bin:/bin\0",
        expected_venv=venv,
        path_separator=":",
    )
    exact = doctor._runtime_env_identity_report(
        f"VIRTUAL_ENV={venv}\0PATH={venv / 'bin'}:/usr/bin:/bin\0".encode(),
        expected_venv=venv,
        path_separator=":",
    )

    assert stale == {
        "status": "FAIL",
        "virtual_env_exact": False,
        "venv_bin_first": False,
        "values_exposed": False,
    }
    assert exact == {
        "status": "PASS",
        "virtual_env_exact": True,
        "venv_bin_first": True,
        "values_exposed": False,
    }
    assert "/srv/hermes-old" not in json.dumps(stale)


def test_gen2_credentials_route_perplexity_through_h20_keys(monkeypatch):
    monkeypatch.setenv("H20_KEYS_BASE_URL", "https://keys.example.invalid/v1")
    monkeypatch.setenv("H20_KEYS_API_KEY", "test-key")

    fallback = doctor._credential_report("gen2_only", require_perplexity=False)
    selected = doctor._credential_report("gen2_only", require_perplexity=True)

    assert fallback["status"] == "PASS"
    assert selected["status"] == "PASS"
    assert selected["perplexity_via_h20_keys"] is True
    assert "PERPLEXITY_API_KEY" not in selected["present"]
    assert "PPLX_API_KEY" not in selected["present"]


def test_direct_perplexity_key_does_not_satisfy_gen2_credentials(monkeypatch):
    monkeypatch.delenv("H20_KEYS_BASE_URL", raising=False)
    monkeypatch.delenv("H20_KEYS_API_KEY", raising=False)
    monkeypatch.delenv("H20_KEYS_STT_API_KEY", raising=False)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "direct-key-must-not-be-used")

    selected = doctor._credential_report("gen2_only", require_perplexity=True)

    assert selected["status"] == "FAIL"
    assert selected["perplexity_via_h20_keys"] is False


def test_sqlite_runtime_floor_rejects_wal_reset_vulnerable_version():
    assert doctor._sqlite_version_report("3.53.1")["status"] == "PASS"
    report = doctor._sqlite_version_report("3.50.4")
    assert report["status"] == "FAIL"
    assert report["minimum"] == "3.53.1"


def test_deleted_state_handle_classifier_is_bounded_and_redacted():
    report = doctor._classify_deleted_state_handles(
        [
            "/private/profile/state.db-wal (deleted)",
            "/private/profile/state.db-shm (deleted)",
            "/tmp/unrelated (deleted)",
        ]
    )

    assert report == {
        "status": "FAIL",
        "deleted_count": 2,
        "kinds": ["shm", "wal"],
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv symlink contract")
def test_process_identity_accepts_exact_managed_python_symlink(tmp_path):
    repository = tmp_path / "repo"
    venv_bin = repository / "venv" / "bin"
    runtime = tmp_path / "managed" / "python3.12"
    venv_bin.mkdir(parents=True)
    runtime.parent.mkdir()
    runtime.write_text("runtime", encoding="utf-8")
    (venv_bin / "python").symlink_to(runtime)

    report = doctor._process_identity_report(
        repository=repository,
        venv=repository / "venv",
        process_cwd=str(repository),
        process_exe=str(runtime),
    )

    assert report["status"] == "PASS"
    assert report["cwd_matches"] is True
    assert report["executable_matches"] is True


def test_operational_config_rejects_stt_drift_and_self_cron(tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "stt": {
                    "enabled": True,
                    "provider": "human20-keys-groq",
                    "human20-keys-groq": {"model": "whisper-large-v3"},
                }
            }
        ),
        encoding="utf-8",
    )
    profile = tmp_path / "profiles" / "dev"
    (profile / "cron").mkdir(parents=True)
    (profile / "config.yaml").write_text(
        yaml.safe_dump({"stt": {"enabled": True, "provider": "groq"}}),
        encoding="utf-8",
    )
    (profile / "cron" / "jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {"id": "self-job", "enabled": True, "deliver": "bot-chat"},
                    {"id": "other-job", "enabled": True, "deliver": "bot-chat:research"},
                    {"id": "disabled", "enabled": False, "deliver": "bot-chat"},
                ]
            }
        ),
        encoding="utf-8",
    )

    report = doctor._operational_config_report(tmp_path)

    assert report["status"] == "FAIL"
    assert report["stt_drift"] == ["dev"]
    assert report["cron_self_delivery"] == ["dev:self-job"]


def test_operational_config_accepts_managed_stt_and_cross_profile_cron(tmp_path):
    (tmp_path / "cron").mkdir()
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "stt": {
                    "enabled": True,
                    "provider": "human20-keys-groq",
                    "human20-keys-groq": {"model": "whisper-large-v3"},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "cron" / "jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {"id": "cross-profile", "enabled": True, "deliver": "bot-chat:research"},
                    {"id": "telegram", "enabled": True, "deliver": "telegram"},
                ]
            }
        ),
        encoding="utf-8",
    )
    inherited = tmp_path / "profiles" / "inherited"
    inherited.mkdir(parents=True)
    (inherited / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": "openai-codex/gpt-5.6-luna"}}),
        encoding="utf-8",
    )

    report = doctor._operational_config_report(tmp_path)

    assert report["status"] == "PASS"
    assert report["stt_drift"] == []
    assert report["cron_self_delivery"] == []


def test_default_doctor_pin_matches_certified_fresh_upstream():
    release = json.loads(
        (PACKAGE_ROOT.parents[1] / "powerpack" / "release.json").read_text(
            encoding="utf-8"
        )
    )
    assert cli.PIN == release["upstream_base"]
    assert cli.PIN in doctor.load_manifest(PACKAGE_ROOT)["supported_upstream_shas"]


def test_disabled_mode_registers_nothing():
    package = _reload_package()
    ctx = FakeContext(mode="disabled")
    assert package.register(ctx) == {"mode": "disabled", "variant": "employee", "skills": 0, "tools": 0}
    assert ctx.calls == []


def test_compatibility_mode_registers_only_non_effectful_namespaced_surfaces():
    package = _reload_package()
    ctx = FakeContext(mode="compatibility")
    result = package.register(ctx)
    assert result["tools"] == 0
    assert ("command", "powerpack-gen2") in ctx.calls
    assert ("cli", "powerpack-gen2") in ctx.calls
    assert not any(kind in {"tool", "web", "image", "stt"} for kind, _ in ctx.calls)


def test_gen2_only_registers_unique_effectful_surfaces():
    package = _reload_package()
    ctx = FakeContext(mode="gen2_only")
    result = package.register(ctx)
    assert result["tools"] == 3
    assert {name for kind, name in ctx.calls if kind == "tool"} == {
        "mem0g",
        "continuum_host",
        "chipmanager_telegram",
    }
    assert ("web", "human20-perplexity") in ctx.calls
    assert ("image", "human20-keys-openai-codex") in ctx.calls
    assert ("stt", "human20-keys-groq") in ctx.calls
    assert not ({name for _, name in ctx.calls} & doctor.BUILTIN_COLLISION_NAMES)


def test_collision_fails_closed():
    package = _reload_package()
    ctx = FakeContext(mode="gen2_only", reject="tool:continuum_host")
    with pytest.raises(RuntimeError, match="continuum_host"):
        package.register(ctx)


def test_current_plugin_context_registers_exact_gen2_owners(monkeypatch, tmp_path):
    """The boxed plugin must work with Hermes' real PluginContext contract."""
    from agent import image_gen_registry, transcription_registry, web_search_registry
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
    import tools.registry as tool_registry_module

    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {
                    "entries": {
                        "human20-powerpack-gen2": {
                            "settings": {
                                "mode": "gen2_only",
                                "variant": "employee",
                            },
                            "allow_tool_override": True,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    clean_registry = tool_registry_module.ToolRegistry()
    monkeypatch.setattr(tool_registry_module, "registry", clean_registry)
    image_gen_registry._reset_for_tests()
    transcription_registry._reset_for_tests()
    web_search_registry._reset_for_tests()

    package = _reload_package()
    manager = PluginManager()
    ctx = PluginContext(
        PluginManifest(
            name="human20-powerpack-gen2",
            key="human20-powerpack-gen2",
            source="user",
        ),
        manager,
    )
    result = package.register(ctx)

    assert result == {"mode": "gen2_only", "variant": "employee", "skills": 2, "tools": 3}
    for name, handler in {
        "mem0g": package.tools.handle_mem0g,
        "continuum_host": package.tools.handle_continuum,
        "chipmanager_telegram": package.tools.handle_chipmanager,
    }.items():
        entry = clean_registry.get_entry(name)
        assert entry is not None
        assert entry.handler is handler
        assert entry.toolset == "powerpack-gen2"
    assert web_search_registry.get_provider("human20-perplexity") is not None
    assert image_gen_registry.get_provider("human20-keys-openai-codex") is not None
    assert transcription_registry.get_provider("human20-keys-groq") is not None


def test_current_plugin_context_accepts_legacy_root_settings(monkeypatch, tmp_path):
    """A v2.3 package remains loadable on profiles written by v2.2."""
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": {
                    "entries": {
                        "human20-powerpack-gen2": {
                            "mode": "gen2_only",
                            "variant": "employee",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    ctx = PluginContext(
        PluginManifest(
            name="human20-powerpack-gen2",
            key="human20-powerpack-gen2",
            source="user",
        ),
        PluginManager(),
    )
    package = _reload_package()

    assert package._setting(ctx, "mode", "disabled", package.MODES) == "gen2_only"
    assert package._setting(ctx, "variant", "rentals", package.VARIANTS) == "employee"


def test_complete_inventory_detects_any_package_tamper(tmp_path):
    target = tmp_path / "package"
    shutil.copytree(PACKAGE_ROOT, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    before = doctor.run_doctor(
        root=target,
        variant="employee",
        mode="compatibility",
        upstream_sha="d5632392c73f9418b2a4a90f4b351c8c21d152ff",
        ci=True,
    )
    assert before["ok"] is True
    (target / "README.md").write_text("tampered\n")
    after = doctor.run_doctor(
        root=target,
        variant="employee",
        mode="compatibility",
        upstream_sha="d5632392c73f9418b2a4a90f4b351c8c21d152ff",
        ci=True,
    )
    assert after["ok"] is False
    assert "complete_package_inventory" in after["required_failures"]


def test_compatibility_install_owns_plugin_config_without_claiming_stt(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    stt_calls: list[bool] = []
    monkeypatch.setattr(cli, "_configure_managed_stt", lambda: stt_calls.append(True))
    ctx = FakeContext(mode="disabled", variant="rentals")
    args = argparse.Namespace(power_command="install", variant="employee", mode="compatibility")
    assert cli.handle_cli(args, PACKAGE_ROOT, "rentals", "disabled", ctx=ctx) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "compatibility"
    assert ctx.settings == {"mode": "compatibility", "variant": "employee"}
    assert stt_calls == []
    receipt = json.loads(Path(payload["receipt"]).read_text())
    assert receipt["mode"] == "compatibility"
    assert receipt["secrets_persisted"] is False
