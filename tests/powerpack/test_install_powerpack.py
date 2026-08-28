from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-powerpack.sh"


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash, "Git Bash/bash is required for the installer contract test"
    installer = str(INSTALLER).replace("\\", "/")
    return subprocess.run(
        [bash, installer, *args],
        check=check,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )


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
