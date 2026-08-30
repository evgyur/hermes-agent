from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-powerpack.sh"


def test_project_version_matches_locked_root_package():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    root_packages = [
        package
        for package in lock["package"]
        if package["name"] == project["project"]["name"]
        and package.get("source") == {"editable": "."}
    ]

    assert len(root_packages) == 1
    assert root_packages[0]["version"] == project["project"]["version"]


def test_release_version_matches_project_and_cli():
    from hermes_cli import __version__ as cli_version

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    release = json.loads(
        (ROOT / "powerpack" / "release.json").read_text(encoding="utf-8")
    )

    assert cli_version == project["project"]["version"] == release["version"]


def test_locked_sync_preserves_the_configured_messaging_runtime():
    installer = INSTALLER.read_text(encoding="utf-8")

    assert '"$uv_bin" sync --project "$INSTALL_DIR" --extra all --extra messaging --locked' in installer
    assert "UV_NO_CACHE=1" in installer
    assert '"$HERMES_HOME/bin/uv"' in installer
    assert '"$HOME/.local/bin/uv"' in installer


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash, "Git Bash/bash is required for the installer contract test"
    installer = str(INSTALLER).replace("\\", "/")
    result = subprocess.run(
        [bash, installer, *args],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            # Contract tests operate on temporary checkouts.  They must never
            # observe, stop, or require --restart for a real host gateway.
            "HERMES_GATEWAY_SERVICE": "hermes-powerpack-test.invalid.service",
        },
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"installer exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()


def _write_source_contract(repo: Path, version: str = "0.21.0") -> None:
    (repo / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8")
    (repo / "hermes_cli").mkdir(exist_ok=True)
    (repo / "hermes_cli" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "hermes-agent"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, str, str]:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(source)], check=True)
    _git(source, "config", "user.email", "powerpack-tests@example.invalid")
    _git(source, "config", "user.name", "Powerpack Tests")
    _write_source_contract(source)
    _git(source, "add", ".")
    _git(source, "commit", "-m", "base")
    base = _git(source, "rev-parse", "HEAD")

    install = tmp_path / "install"
    subprocess.run(["git", "clone", str(source), str(install)], check=True)
    (source / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    _git(source, "add", "candidate.txt")
    _git(source, "commit", "-m", "candidate")
    candidate = _git(source, "rev-parse", "HEAD")

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("telegram-chip: configured\n", encoding="utf-8")
    (home / "state.db").write_bytes(b"immutable-state-db")
    (home / "telegram-chip.session").write_bytes(b"immutable-session")
    return source, install, home, base, candidate


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dry_run_is_read_only_and_reports_exact_candidate(tmp_path: Path):
    source, install, home, base, candidate = _fixture(tmp_path)
    before = {
        name: _digest(home / name)
        for name in ("config.yaml", "state.db", "telegram-chip.session")
    }

    result = _run(
        "--dry-run",
        "--source-dir",
        str(source),
        "--repo-url",
        str(source),
        "--dir",
        str(install),
        "--hermes-home",
        str(home),
    )

    assert f"candidate_sha={candidate}" in result.stdout
    assert "action=upgrade" in result.stdout
    assert "data_action=preserve" in result.stdout
    assert _git(install, "rev-parse", "HEAD") == base
    assert _git(install, "status", "--porcelain") == ""
    assert before == {name: _digest(home / name) for name in before}


def test_dry_run_accepts_source_git_worktree(tmp_path: Path):
    source, install, home, _base, candidate = _fixture(tmp_path)
    source_worktree = tmp_path / "source-worktree"
    _git(source, "worktree", "add", "--detach", str(source_worktree), candidate)

    result = _run(
        "--dry-run",
        "--source-dir",
        str(source_worktree),
        "--repo-url",
        str(source),
        "--dir",
        str(install),
        "--hermes-home",
        str(home),
    )

    assert f"candidate_sha={candidate}" in result.stdout
    assert "action=upgrade" in result.stdout
    assert "data_action=preserve" in result.stdout


def test_upgrade_switches_code_and_preserves_data(tmp_path: Path):
    source, install, home, base, candidate = _fixture(tmp_path)
    before = {
        name: _digest(home / name)
        for name in ("config.yaml", "state.db", "telegram-chip.session")
    }

    result = _run(
        "--no-sync",
        "--source-dir",
        str(source),
        "--repo-url",
        str(source),
        "--dir",
        str(install),
        "--hermes-home",
        str(home),
    )

    assert "result=PASS" in result.stdout
    assert _git(install, "rev-parse", "HEAD") == candidate
    backup_refs = _git(
        install,
        "for-each-ref",
        "--format=%(objectname)",
        "refs/heads/backup",
    ).splitlines()
    assert base in backup_refs
    assert before == {name: _digest(home / name) for name in before}


def test_install_receipt_records_release_component_pins(tmp_path: Path):
    source, install, home, _base, _candidate = _fixture(tmp_path)
    pin = {
        "repository": "https://example.invalid/component.git",
        "commit": "a" * 40,
        "asset_path": "assets/example",
        "deployment": "preserve_profile_use_pinned_component",
    }
    manifest = source / "powerpack" / "release.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps({"component_pins": {"example": pin}}) + "\n",
        encoding="utf-8",
    )
    _git(source, "add", "powerpack/release.json")
    _git(source, "commit", "-m", "pin release component")

    result = _run(
        "--no-sync",
        "--source-dir",
        str(source),
        "--repo-url",
        str(source),
        "--dir",
        str(install),
        "--hermes-home",
        str(home),
    )

    receipt_text = next(
        line.removeprefix("receipt=")
        for line in result.stdout.splitlines()
        if line.startswith("receipt=")
    )
    # The installer deliberately prints its normalized shell path.  Under
    # Git Bash that is an MSYS ``/tmp/...`` path, which native pathlib would
    # otherwise misread as ``C:\\tmp\\...`` instead of the Windows temp
    # directory where the receipt was actually written.
    if os.name == "nt" and receipt_text.startswith("/"):
        bash = shutil.which("bash")
        assert bash
        receipt_text = subprocess.check_output(
            [bash, "-lc", 'cygpath -w "$1"', "--", receipt_text],
            text=True,
        ).strip()
    receipt = Path(receipt_text)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["component_pins"] == {"example": pin}


def test_diverged_checkout_fails_closed(tmp_path: Path):
    source, install, home, _base, _candidate = _fixture(tmp_path)
    _git(install, "config", "user.email", "powerpack-tests@example.invalid")
    _git(install, "config", "user.name", "Powerpack Tests")
    (install / "local.txt").write_text("local commit\n", encoding="utf-8")
    _git(install, "add", "local.txt")
    _git(install, "commit", "-m", "local divergence")
    divergent = _git(install, "rev-parse", "HEAD")

    result = _run(
        "--dry-run",
        "--source-dir",
        str(source),
        "--repo-url",
        str(source),
        "--dir",
        str(install),
        "--hermes-home",
        str(home),
        check=False,
    )

    assert result.returncode != 0
    assert "not an ancestor" in result.stderr
    assert _git(install, "rev-parse", "HEAD") == divergent


def test_registered_powerpack_predecessor_can_upgrade(tmp_path: Path):
    source, install, home, _base, _candidate = _fixture(tmp_path)
    _git(install, "config", "user.email", "powerpack-tests@example.invalid")
    _git(install, "config", "user.name", "Powerpack Tests")
    (install / "previous-powerpack.txt").write_text("old private tail\n", encoding="utf-8")
    _git(install, "add", "previous-powerpack.txt")
    _git(install, "commit", "-m", "previous powerpack")
    predecessor = _git(install, "rev-parse", "HEAD")

    manifest = source / "powerpack" / "release.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps({"accepted_predecessors": [predecessor]}) + "\n",
        encoding="utf-8",
    )
    _git(source, "add", "powerpack/release.json")
    _git(source, "commit", "-m", "register predecessor")
    candidate = _git(source, "rev-parse", "HEAD")

    result = _run(
        "--no-sync",
        "--source-dir",
        str(source),
        "--repo-url",
        str(source),
        "--dir",
        str(install),
        "--hermes-home",
        str(home),
    )

    assert "result=PASS" in result.stdout
    assert _git(install, "rev-parse", "HEAD") == candidate


def test_remote_mismatch_rolls_back_code_and_origin(tmp_path: Path):
    source, install, home, base, _candidate = _fixture(tmp_path)
    original_origin = _git(install, "remote", "get-url", "origin")
    wrong_remote = tmp_path / "wrong-remote"
    subprocess.run(["git", "clone", str(install), str(wrong_remote)], check=True)

    result = _run(
        "--no-sync",
        "--source-dir",
        str(source),
        "--repo-url",
        str(wrong_remote),
        "--dir",
        str(install),
        "--hermes-home",
        str(home),
        check=False,
    )

    assert result.returncode != 0
    assert "does not match packaged candidate" in result.stderr
    assert _git(install, "rev-parse", "HEAD") == base
    assert _git(install, "remote", "get-url", "origin") == original_origin


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="POSIX non-root permission semantics required",
)
def test_read_only_changed_path_fails_before_checkout_or_backup(tmp_path: Path):
    source, install, home, base, _candidate = _fixture(tmp_path)
    locked = install / "locked"
    locked.mkdir()
    (locked / "value.txt").write_text("base\n", encoding="utf-8")
    _git(install, "add", "locked/value.txt")
    _git(install, "config", "user.email", "powerpack-tests@example.invalid")
    _git(install, "config", "user.name", "Powerpack Tests")
    _git(install, "commit", "-m", "locked base")
    predecessor = _git(install, "rev-parse", "HEAD")

    _git(source, "fetch", str(install), predecessor)
    _git(source, "merge", "--no-ff", "-m", "merge registered predecessor", predecessor)
    (source / "locked" / "value.txt").write_text("candidate\n", encoding="utf-8")
    _git(source, "add", "locked/value.txt")
    _git(source, "commit", "-m", "change locked path")
    locked.chmod(0o555)
    try:
        result = _run(
            "--dry-run",
            "--source-dir",
            str(source),
            "--repo-url",
            str(source),
            "--dir",
            str(install),
            "--hermes-home",
            str(home),
            check=False,
        )
    finally:
        locked.chmod(0o755)

    assert result.returncode != 0
    assert "not writable" in result.stderr
    assert _git(install, "rev-parse", "HEAD") == predecessor
    assert _git(install, "status", "--porcelain") == ""
    assert _git(install, "branch", "--list", "backup/powerpack-*") == ""
