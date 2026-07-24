from __future__ import annotations

import copy
import hashlib
import os
import pwd
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from release_test_helpers import git, init_repo
from deploy_human20bot_team_access import (
    DeploymentError,
    DeploymentEngine,
    _acquire_deployment_lock,
    _bounded_identity_read,
    _consume_approval_scope,
    _read_service_process_identity,
    _service_interpreter_from_cmdline,
    merge_overlay_config,
    smoke_environment_for_uid,
    validate_approval_pair,
    wait_for_stable_service_process,
)


class FakeCommandRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        self.calls.append(tuple(argv))
        if argv[0] == "systemctl":
            if argv[1] == "is-active":
                return subprocess.CompletedProcess(argv, 0, b"active\n", b"")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return subprocess.run(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )


def test_overlay_merge_preserves_secrets_and_unrelated_keys() -> None:
    current = {
        "telegram": {
            "bot_token": "synthetic-secret-preserved",
            "require_mention": True,
            "unrelated": {"keep": 7},
        },
        "providers": {"private": "unchanged"},
    }
    overlay = {
        "schema_version": "1.0",
        "telegram": {
            "require_mention": False,
            "group_sessions_per_user": True,
            "extra": {"team_membership_positive_ttl_seconds": 30},
        },
    }
    before = copy.deepcopy(current)

    merged = merge_overlay_config(current, overlay)

    assert merged["telegram"]["bot_token"] == before["telegram"]["bot_token"]
    assert merged["telegram"]["unrelated"] == before["telegram"]["unrelated"]
    assert merged["providers"] == before["providers"]
    assert merged["telegram"]["require_mention"] is False
    assert current == before


def approval_pair(now: datetime) -> tuple[dict, dict, dict]:
    manifest = {
        "schema_version": "1.0",
        "candidate_sha": "a" * 40,
        "service": "synthetic-gateway.service",
        "profile_overlay": {"tree_sha256": "b" * 64},
        "artifacts": {"rollback_manifest_sha256": "c" * 64},
        "sealed_at": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }
    binding = {
        "schema_version": "1.0",
        "status": "approved",
        "candidate_sha": manifest["candidate_sha"],
        "profile_overlay_sha256": "b" * 64,
        "service": manifest["service"],
        "rollback_manifest_sha256": "c" * 64,
        "release_manifest_sha256": "d" * 64,
        "canary_destinations": {
            "owner_dm": "synthetic-owner",
            "test_chat": "synthetic-chat",
            "test_thread": "synthetic-thread",
        },
        "issued_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "not_before": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "not_after": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
    }
    app3 = {**binding, "approval_id": "APP-003", "class_name": "production"}
    app4 = {
        **binding,
        "approval_id": "APP-004",
        "class_name": "destructive-if-live",
    }
    return manifest, app3, app4


def test_approval_pair_must_be_fresh_and_exactly_bound() -> None:
    now = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
    manifest, app3, app4 = approval_pair(now)

    scope_hash = validate_approval_pair(
        manifest,
        app3,
        app4,
        release_manifest_sha256="d" * 64,
        now=now,
    )
    assert len(scope_hash) == 64

    stale = dict(app4)
    stale["not_after"] = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    with pytest.raises(DeploymentError, match="window"):
        validate_approval_pair(manifest, app3, stale, "d" * 64, now)

    wrong = dict(app3)
    wrong["candidate_sha"] = "e" * 40
    with pytest.raises(DeploymentError, match="binding"):
        validate_approval_pair(manifest, wrong, app4, "d" * 64, now)


def test_apply_failure_rolls_back_code_and_config_and_restarts_service(tmp_path: Path) -> None:
    repo, baseline = init_repo(tmp_path)
    (repo / "feature.py").write_text("ENABLED = True\n", encoding="utf-8")
    git(repo, "add", "feature.py")
    git(repo, "commit", "-qm", "candidate")
    candidate = git(repo, "rev-parse", "HEAD").decode().strip()
    git(repo, "reset", "--hard", baseline)

    config = tmp_path / "config.yaml"
    original = (
        "telegram:\n  bot_token: synthetic-secret-preserved\n  require_mention: true\n"
        "unrelated:\n  keep: true\n"
    ).encode()
    config.write_bytes(original)
    config.chmod(0o600)
    overlay = {
        "schema_version": "1.0",
        "telegram": {"require_mention": False, "group_sessions_per_user": True},
    }
    runner = FakeCommandRunner()
    engine = DeploymentEngine(repo, config, "synthetic-gateway.service", runner)

    rollback_checks: list[str] = []
    with pytest.raises(DeploymentError, match="rolled back"):
        engine.apply(
            baseline=baseline,
            candidate=candidate,
            overlay=overlay,
            smoke=lambda: (_ for _ in ()).throw(RuntimeError("synthetic smoke failure")),
            auto_rollback=True,
            rollback_health=lambda: rollback_checks.append("runtime") or {"status": "pass"},
        )

    assert rollback_checks == ["runtime"]
    assert git(repo, "rev-parse", "HEAD").decode().strip() == baseline
    assert config.read_bytes() == original
    assert config.stat().st_mode & 0o777 == 0o600
    restart_calls = [c for c in runner.calls if c[:2] == ("systemctl", "restart")]
    assert len(restart_calls) == 2
    assert all(call[2] == "synthetic-gateway.service" for call in restart_calls)


def test_apply_uses_fast_forward_and_never_shell(tmp_path: Path) -> None:
    repo, baseline = init_repo(tmp_path)
    (repo / "feature.py").write_text("ENABLED = True\n", encoding="utf-8")
    git(repo, "add", "feature.py")
    git(repo, "commit", "-qm", "candidate")
    candidate = git(repo, "rev-parse", "HEAD").decode().strip()
    git(repo, "reset", "--hard", baseline)
    config = tmp_path / "config.yaml"
    config.write_text("telegram:\n  require_mention: true\n", encoding="utf-8")
    config.chmod(0o600)
    runner = FakeCommandRunner()
    engine = DeploymentEngine(repo, config, "synthetic-gateway.service", runner)

    result = engine.apply(
        baseline,
        candidate,
        {"schema_version": "1.0", "telegram": {"require_mention": False}},
        smoke=lambda: {"status": "pass"},
        auto_rollback=True,
    )

    assert result["status"] == "applied"
    assert git(repo, "rev-parse", "HEAD").decode().strip() == candidate
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["telegram"]["require_mention"] is False
    assert any(call[:3] == ("git", "merge", "--ff-only") for call in runner.calls)
    assert all(isinstance(call, tuple) for call in runner.calls)


def test_service_interpreter_preserves_virtualenv_argv0(tmp_path: Path) -> None:
    interpreter = tmp_path / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"")
    cmdline = os.fsencode(interpreter) + b"\0service-entrypoint\0gateway\0"

    assert _service_interpreter_from_cmdline(cmdline) == str(interpreter)


def test_service_interpreter_rejects_missing_or_relative_argv0(tmp_path: Path) -> None:
    with pytest.raises(DeploymentError, match="command line unavailable"):
        _service_interpreter_from_cmdline(b"")
    with pytest.raises(DeploymentError, match="interpreter identity unavailable"):
        _service_interpreter_from_cmdline(b"python\0service-entrypoint\0")
    missing = tmp_path / "venv" / "bin" / "python"
    with pytest.raises(DeploymentError, match="interpreter identity unavailable"):
        _service_interpreter_from_cmdline(os.fsencode(missing) + b"\0service-entrypoint\0")


def test_wait_for_stable_service_process_skips_launcher_and_requires_two_samples() -> None:
    launcher = {"pid": 20, "executable": "/usr/bin/sh", "cmdline_sha256": "x" * 64}
    gateway = {"pid": 21, "executable": "/usr/bin/python3", "cmdline_sha256": "d" * 64}
    states = [launcher, gateway, gateway]
    sleeps: list[float] = []

    result = wait_for_stable_service_process(
        "synthetic.service", prior_pid=10, expected_executable="/usr/bin/python3",
        expected_cmdline_sha256="d" * 64, timeout_seconds=1,
        sample_seconds=0, reader=lambda _service: states.pop(0), sleeper=sleeps.append,
    )

    assert result == gateway
    assert len(sleeps) == 2


def test_wait_for_stable_service_process_rejects_activation_pid() -> None:
    activation = {"pid": 21, "executable": "/usr/bin/python3", "cmdline_sha256": "d" * 64}
    with pytest.raises(DeploymentError, match="did not stabilize"):
        wait_for_stable_service_process(
            "synthetic.service", prior_pid=21, expected_executable="/usr/bin/python3",
            expected_cmdline_sha256="d" * 64, timeout_seconds=0.02,
            sample_seconds=0, reader=lambda _service: activation, sleeper=lambda _seconds: None,
        )


def test_bounded_identity_read_hard_bounds_a_blocked_reader() -> None:
    def blocked(_service: str) -> dict:
        time.sleep(0.15)
        return {"pid": 99}

    started = time.monotonic()
    with pytest.raises(DeploymentError, match="timed out"):
        _bounded_identity_read(blocked, "synthetic.service", 0.02)
    assert time.monotonic() - started < 0.1


def test_service_identity_normalizes_malformed_main_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    def malformed(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, b"not-a-pid\n", b"")

    monkeypatch.setattr(subprocess, "run", malformed)
    with pytest.raises(DeploymentError, match="identity unavailable"):
        _read_service_process_identity("synthetic.service", timeout_seconds=0.01)


def test_smoke_environment_switches_to_target_account_home(monkeypatch: pytest.MonkeyPatch) -> None:
    account = pwd.getpwuid(os.getuid())
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setenv("HERMES_HOME", "/root/.hermes")
    monkeypatch.setenv("SUDO_USER", "root")
    monkeypatch.setenv("SUDO_COMMAND", "synthetic")

    env = smoke_environment_for_uid(os.getuid())

    assert env["HOME"] == account.pw_dir
    assert env["USER"] == account.pw_name
    assert env["LOGNAME"] == account.pw_name
    assert env["XDG_CONFIG_HOME"] == str(Path(account.pw_dir) / ".config")
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "HERMES_HOME" not in env
    assert not any(key.startswith("SUDO_") for key in env)
