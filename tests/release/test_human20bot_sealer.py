from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from release_test_helpers import git, init_repo
from release_safety import SafetyError, canonical_json_bytes, sha256_file
from seal_human20bot_release import seal_release

SERVICE = "synthetic-gateway.service"


def write_json(path: Path, value: dict) -> None:
    path.write_bytes(canonical_json_bytes(value))
    path.chmod(0o600)


def prepare_inputs(tmp_path: Path) -> tuple[Path, str, str, Path, Path]:
    repo, baseline = init_repo(tmp_path)
    scripts = repo / "scripts"
    scripts.mkdir()
    here = Path(__file__).resolve()
    source_root = here.parents[1] if (here.parents[1] / "scripts/release_safety.py").is_file() else here.parents[2]
    for name in (
        "deploy_human20bot_team_access.py",
        "smoke_human20bot_team_access.py",
        "release_safety.py",
    ):
        path = scripts / name
        path.write_bytes((source_root / "scripts" / name).read_bytes())
        path.chmod(0o700)
    (repo / "candidate.py").write_text("ENABLED = True\n", encoding="utf-8")
    git(repo, "add", "scripts", "candidate.py")
    git(repo, "commit", "-qm", "candidate")
    candidate = git(repo, "rev-parse", "HEAD").decode().strip()
    candidate_diff_sha = hashlib.sha256(
        git(repo, "diff", "--no-ext-diff", "--binary", f"{baseline}..{candidate}")
    ).hexdigest()

    artifact = tmp_path / "artifacts"
    artifact.mkdir(mode=0o700)
    overlay = artifact / "profile-overlay"
    overlay.mkdir(mode=0o700)
    overlay_text = b"schema_version: '1.0'\ntelegram:\n  require_mention: false\n"
    (overlay / "config.overlay.yaml").write_bytes(overlay_text)
    (overlay / "config.overlay.yaml").chmod(0o600)
    overlay_manifest = {
        "schema_version": "1.0",
        "status": "staged",
        "no_secrets": True,
        "source_sha256": {"config": hashlib.sha256(b"live-config\n").hexdigest()},
        "output_sha256": {
            "config.overlay.yaml": hashlib.sha256(overlay_text).hexdigest()
        },
        "files": ["config.overlay.yaml"],
    }
    write_json(overlay / "MANIFEST.json", overlay_manifest)

    live = tmp_path / "live"
    git(repo, "worktree", "add", "--detach", str(live), baseline)
    (live / "preexisting-backup.txt").write_text("preserve\n", encoding="utf-8")
    source_lock = {
        "schema_version": "1.0",
        "live_head": baseline,
        "live_checkout": {
            "path": str(live),
            "head": baseline,
            "branch": "deploy/synthetic",
            "status_sha256": "0" * 64,
            "tracked_diff_sha256": hashlib.sha256(b"").hexdigest(),
        },
        "service": {
            "unit": SERVICE,
            "active": True,
            "user": "synthetic",
            "group": "synthetic",
            "working_directory": str(live),
            "restart_count": 0,
        },
    }
    write_json(artifact / "P01-source-lock.json", source_lock)
    write_json(artifact / "P03-replay-audit.json", {"status": "pass", "violations": []})
    write_json(
        artifact / "P04-verification.json",
        {
            "phase_id": "P04",
            "candidate_sha": candidate,
            "candidate_diff_sha256": candidate_diff_sha,
            "tests": {"gate": "pass"},
            "live_checkout": {
                "head": baseline,
                "path": str(live),
                "status_sha256": "1" * 64,
                "tracked_diff_sha256": hashlib.sha256(b"").hexdigest(),
            },
            "capability_contract": {
                "candidate_sha": candidate,
                "profile_capability_config_sha256": "f" * 64,
                "first_class_config_families": [
                    "terminal", "file", "web", "search", "delegation", "memory", "cronjob", "messaging",
                ],
                "telegram_and_cli_schema_assertions": [
                    "terminal", "process", "read_file", "write_file", "patch", "search_files",
                    "web_search", "web_extract", "vision_analyze", "image_generate",
                    "text_to_speech", "delegate_task", "memory", "cronjob",
                ],
                "service_wrappers": ["human20-memory-stack", "team20-kanban"],
                "missing_required_capabilities": [],
                "missing_required_schema_assertions": [],
                "missing_required_service_wrappers": [],
            },
        },
    )
    write_json(
        artifact / "P05-test-receipt.json",
        {
            "phase_id": "P05",
            "subject": {
                "candidate_sha": candidate,
                "baseline_sha": baseline,
                "worktree_subject_sha256": candidate_diff_sha,
            },
            "tests": {
                "P05-CMD01": {"status": "pass"},
                "release_security": {"status": "pass"},
            },
        },
    )
    write_json(
        artifact / "P05-static-scan.json",
        {
            "status": "pass",
            "violations": [],
            "candidate_sha": candidate,
            "candidate_diff_sha256": candidate_diff_sha,
        },
    )

    review = artifact / "P05-independent-review.json"
    write_json(
        review,
        {
            "schema_version": "1.0",
            "candidate_sha": candidate,
            "verdict": "PASS",
            "reviewed_controls": ["authorization-before-state", "rollback"],
            "findings": [],
        },
    )
    return repo, baseline, candidate, artifact, review


def test_sealer_creates_bound_private_artifacts_and_executable_driver(tmp_path: Path) -> None:
    repo, baseline, candidate, artifact, review = prepare_inputs(tmp_path)

    manifest = seal_release(
        repo=repo,
        baseline=baseline,
        artifact_root=artifact,
        service=SERVICE,
        profile_overlay=artifact / "profile-overlay",
        review_path=review,
        require_clean=True,
        live_config_path=tmp_path / "live-config.yaml",
    )

    release_path = artifact / "release-manifest.json"
    rollback_path = artifact / "rollback-manifest.json"
    bundle_path = artifact / "human20bot-team-operator.bundle"
    driver_path = artifact / "deploy_human20bot_team_operator.py"
    smoke_path = artifact / "smoke_human20bot_team_access.py"
    helper_path = artifact / "release_safety.py"
    assert manifest["candidate_sha"] == candidate
    assert release_path.stat().st_mode & 0o777 == 0o600
    assert rollback_path.stat().st_mode & 0o777 == 0o600
    assert bundle_path.stat().st_mode & 0o777 == 0o600
    assert driver_path.stat().st_mode & 0o777 == 0o700
    assert smoke_path.stat().st_mode & 0o777 == 0o700
    assert helper_path.stat().st_mode & 0o777 == 0o600
    assert manifest["artifacts"]["bundle_sha256"] == sha256_file(bundle_path)
    assert manifest["artifacts"]["deployment_driver_sha256"] == sha256_file(driver_path)
    assert manifest["artifacts"]["independent_review_sha256"] == sha256_file(review)
    assert manifest["live_lock"]["captured_at_seal"] is True
    current_status = git(live := Path(manifest["live_lock"]["repo_path"]), "status", "--porcelain=v1", "--untracked-files=all")
    assert manifest["live_lock"]["status_sha256"] == hashlib.sha256(current_status).hexdigest()
    assert manifest["live_lock"]["status_sha256"] not in {"0" * 64, "1" * 64}
    assert git(repo, "bundle", "verify", str(bundle_path)) is not None

    clean_env = {"PATH": os.environ["PATH"], "PYTHONPATH": ""}
    for packaged in (driver_path, smoke_path):
        proc = __import__("subprocess").run(
            [str(packaged), "--help"], cwd="/", env=clean_env,
            stdout=__import__("subprocess").PIPE,
            stderr=__import__("subprocess").PIPE, check=False,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


def test_sealer_rejects_review_with_open_p1(tmp_path: Path) -> None:
    repo, baseline, _, artifact, review = prepare_inputs(tmp_path)
    value = json.loads(review.read_text(encoding="utf-8"))
    value["findings"] = [{"severity": "P1", "status": "open"}]
    write_json(review, value)

    with pytest.raises(SafetyError, match="P0/P1"):
        seal_release(
            repo,
            baseline,
            artifact,
            SERVICE,
            artifact / "profile-overlay",
            review,
            True,
            tmp_path / "live-config.yaml",
        )


def test_sealer_rejects_capability_contract_with_missing_requirements(tmp_path: Path) -> None:
    repo, baseline, _, artifact, review = prepare_inputs(tmp_path)
    p04_path = artifact / "P04-verification.json"
    value = json.loads(p04_path.read_text(encoding="utf-8"))
    value["capability_contract"]["missing_required_capabilities"] = ["terminal"]
    write_json(p04_path, value)

    with pytest.raises(SafetyError, match="missing requirements"):
        seal_release(
            repo, baseline, artifact, SERVICE, artifact / "profile-overlay",
            review, True, tmp_path / "live-config.yaml",
        )


def test_sealer_requires_exactly_one_direct_clean_commit(tmp_path: Path) -> None:
    repo, baseline, _, artifact, review = prepare_inputs(tmp_path)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(SafetyError, match="clean"):
        seal_release(
            repo,
            baseline,
            artifact,
            SERVICE,
            artifact / "profile-overlay",
            review,
            True,
            tmp_path / "live-config.yaml",
        )


def test_sealer_rejects_overlay_symlink(tmp_path: Path) -> None:
    repo, baseline, _, artifact, review = prepare_inputs(tmp_path)
    overlay_file = artifact / "profile-overlay/config.overlay.yaml"
    content = overlay_file.read_bytes()
    overlay_file.unlink()
    external = tmp_path / "external.yaml"
    external.write_bytes(content)
    overlay_file.symlink_to(external)

    with pytest.raises(SafetyError, match="symlink"):
        seal_release(
            repo,
            baseline,
            artifact,
            SERVICE,
            artifact / "profile-overlay",
            review,
            True,
            tmp_path / "live-config.yaml",
        )
