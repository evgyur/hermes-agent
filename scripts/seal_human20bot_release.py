#!/usr/bin/env python3
"""Seal one reviewed Human20Bot candidate commit into private release artifacts."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from release_safety import (
    SafetyError,
    atomic_write_bytes,
    canonical_json_bytes,
    ensure_directory,
    hash_tree,
    read_nofollow,
    run_argv,
    sha256_bytes,
    sha256_file,
    strict_json_load,
)


def _git(repo: Path, *args: str) -> str:
    return run_argv(["git", *args], cwd=repo).stdout.decode("utf-8", "strict").strip()


def _safe_copy(src: Path, dst: Path, mode: int) -> None:
    atomic_write_bytes(dst, read_nofollow(src, max_bytes=64 * 1024 * 1024), mode=mode)


def _validate_review(review: dict[str, Any], candidate: str) -> None:
    if review.get("candidate_sha") != candidate or review.get("verdict") not in {"PASS", "GO"}:
        raise SafetyError("review is not PASS and exactly bound to candidate SHA")
    controls = review.get("reviewed_controls")
    required_controls = {"authorization-before-state", "rollback"}
    if not isinstance(controls, list) or not required_controls <= set(controls):
        raise SafetyError("review does not cover authorization-before-state and rollback")
    findings = review.get("findings", [])
    if not isinstance(findings, list):
        raise SafetyError("review findings must be a list")
    for finding in findings:
        if not isinstance(finding, dict):
            raise SafetyError("review finding must be an object")
        if str(finding.get("severity", "")).upper() in {"P0", "P1"} and str(
            finding.get("status", "open")
        ).lower() not in {"closed", "resolved", "fixed"}:
            raise SafetyError("review contains open P0/P1 finding")


def _validate_capability_contract(capability: dict[str, Any], candidate: str) -> str:
    capability_sha = capability.get("profile_capability_config_sha256")
    if (
        not isinstance(capability_sha, str)
        or len(capability_sha) != 64
        or any(ch not in "0123456789abcdef" for ch in capability_sha.lower())
        or capability.get("candidate_sha") != candidate
    ):
        raise SafetyError("P04 capability evidence missing or stale")
    required = {
        "first_class_config_families": {
            "terminal", "file", "web", "search", "delegation", "memory", "cronjob", "messaging",
        },
        "telegram_and_cli_schema_assertions": {
            "terminal", "process", "read_file", "write_file", "patch", "search_files",
            "web_search", "web_extract", "vision_analyze", "image_generate",
            "text_to_speech", "delegate_task", "memory", "cronjob",
        },
        "service_wrappers": {"human20-memory-stack", "team20-kanban"},
    }
    for key, expected in required.items():
        actual = capability.get(key)
        if not isinstance(actual, list) or not expected <= set(actual):
            raise SafetyError(f"P04 capability contract incomplete: {key}")
    missing_keys = (
        "missing_required_capabilities",
        "missing_required_schema_assertions",
        "missing_required_service_wrappers",
    )
    if any(capability.get(key) != [] for key in missing_keys):
        raise SafetyError("P04 capability contract reports missing requirements")
    return capability_sha


def _validate_overlay(profile_overlay: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    tree = hash_tree(profile_overlay, require_private=True)
    manifest = strict_json_load(profile_overlay / "MANIFEST.json")
    if manifest.get("no_secrets") is not True or manifest.get("status") not in {"staged", "pass"}:
        raise SafetyError("profile overlay is not a no-secrets staged artifact")
    files = manifest.get("files")
    if files != ["config.overlay.yaml"]:
        raise SafetyError("profile overlay file set is not minimal")
    expected = (manifest.get("output_sha256") or {}).get("config.overlay.yaml")
    actual = sha256_file(profile_overlay / "config.overlay.yaml")
    if expected != actual:
        raise SafetyError("profile overlay hash mismatch")
    return manifest, tree


def seal_release(
    repo: Path | str,
    baseline: str,
    artifact_root: Path | str,
    service: str,
    profile_overlay: Path | str,
    review_path: Path | str,
    require_clean: bool,
    live_config_path: Path | str,
) -> dict[str, Any]:
    repo = Path(repo).resolve(strict=True)
    artifact_root = ensure_directory(artifact_root, mode=0o700)
    profile_overlay = Path(profile_overlay)
    review_path = Path(review_path)
    live_config_path = Path(live_config_path)
    if not service or any(ch.isspace() for ch in service) or "/" in service:
        raise SafetyError("invalid service unit")
    canonical_review_path = artifact_root / "P05-independent-review.json"
    if review_path.resolve(strict=True) != canonical_review_path:
        raise SafetyError("review must be the canonical P05-independent-review.json artifact")

    candidate = _git(repo, "rev-parse", "HEAD")
    candidate_diff_sha = hashlib.sha256(
        run_argv(["git", "diff", "--no-ext-diff", "--binary", f"{baseline}..{candidate}"], cwd=repo).stdout
    ).hexdigest()
    _git(repo, "cat-file", "-e", f"{baseline}^{{commit}}")
    parent = _git(repo, "rev-parse", "HEAD^")
    count = _git(repo, "rev-list", "--count", f"{baseline}..{candidate}")
    if parent != baseline or count != "1":
        raise SafetyError("candidate must be exactly one direct commit above baseline")
    if require_clean and _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SafetyError("candidate repository must be clean")

    review = strict_json_load(review_path)
    _validate_review(review, candidate)
    overlay_manifest, overlay_tree = _validate_overlay(profile_overlay)
    source_lock_path = artifact_root / "P01-source-lock.json"
    source_lock = strict_json_load(source_lock_path)
    if source_lock.get("live_head") != baseline:
        raise SafetyError("source lock baseline mismatch")
    locked_service = (source_lock.get("service") or {}).get("unit")
    if locked_service != service:
        raise SafetyError("source lock service mismatch")

    p04_path = artifact_root / "P04-verification.json"
    p05_test_path = artifact_root / "P05-test-receipt.json"
    p05_scan_path = artifact_root / "P05-static-scan.json"
    p04 = strict_json_load(p04_path)
    p05_test = strict_json_load(p05_test_path)
    p05_scan = strict_json_load(p05_scan_path)
    p04_live_lock = p04.get("live_checkout") or {}
    capability = p04.get("capability_contract") or {}
    if (
        p04.get("phase_id") != "P04"
        or p04.get("candidate_sha") != candidate
        or p04.get("candidate_diff_sha256") != candidate_diff_sha
        or p04_live_lock.get("head") != baseline
    ):
        raise SafetyError("P04 live lock is not bound to baseline")
    capability_sha = _validate_capability_contract(capability, candidate)
    subject = p05_test.get("subject") or {}
    if (
        p05_test.get("phase_id") != "P05"
        or subject.get("candidate_sha") != candidate
        or subject.get("baseline_sha") != baseline
        or subject.get("worktree_subject_sha256") != candidate_diff_sha
        or (p05_test.get("tests") or {}).get("P05-CMD01", {}).get("status") != "pass"
        or (p05_test.get("tests") or {}).get("release_security", {}).get("status") != "pass"
    ):
        raise SafetyError("P05 test receipt is not exactly bound and passing")
    if (
        p05_scan.get("status") != "pass"
        or p05_scan.get("violations") != []
        or p05_scan.get("candidate_sha") != candidate
        or p05_scan.get("candidate_diff_sha256") != candidate_diff_sha
    ):
        raise SafetyError("P05 static scan is not passing")

    live_repo = Path(str((source_lock.get("live_checkout") or {}).get("path", ""))).resolve(strict=True)
    if _git(live_repo, "rev-parse", "HEAD") != baseline:
        raise SafetyError("live checkout moved from baseline before sealing")
    tracked_status = run_argv(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"], cwd=live_repo
    ).stdout
    if tracked_status:
        raise SafetyError("live tracked checkout is dirty before sealing")
    live_status = run_argv(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=live_repo
    ).stdout
    live_diff = run_argv(["git", "diff", "--binary", "HEAD"], cwd=live_repo).stdout
    live_lock = {
        "repo_path": str(live_repo),
        "branch": _git(live_repo, "branch", "--show-current"),
        "status_sha256": hashlib.sha256(live_status).hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(live_diff).hexdigest(),
    }

    deploy_src = repo / "scripts/deploy_human20bot_team_access.py"
    smoke_src = repo / "scripts/smoke_human20bot_team_access.py"
    helper_src = repo / "scripts/release_safety.py"
    for src in (deploy_src, smoke_src, helper_src):
        if src.is_symlink() or not src.is_file():
            raise SafetyError(f"required release script missing or unsafe: {src.name}")

    bundle_path = artifact_root / "human20bot-team-operator.bundle"
    driver_path = artifact_root / "deploy_human20bot_team_operator.py"
    smoke_path = artifact_root / "smoke_human20bot_team_access.py"
    helper_path = artifact_root / "release_safety.py"
    rollback_path = artifact_root / "rollback-manifest.json"
    release_path = artifact_root / "release-manifest.json"
    generated_outputs = (
        bundle_path, driver_path, smoke_path, helper_path, rollback_path, release_path,
        artifact_root / "P06-final-audit.json",
        artifact_root / "P06-activation-or-rollback.json",
        artifact_root / "P06-rollback-health.json",
    )
    for output in generated_outputs:
        try:
            info = output.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SafetyError(f"unsafe existing release output: {output.name}")
        output.unlink()

    run_argv(
        ["git", "bundle", "create", str(bundle_path), "HEAD", f"^{baseline}"],
        cwd=repo,
    )
    os.chmod(bundle_path, 0o600, follow_symlinks=False)
    run_argv(["git", "bundle", "verify", str(bundle_path)], cwd=repo)
    _safe_copy(deploy_src, driver_path, 0o700)
    _safe_copy(smoke_src, smoke_path, 0o700)
    _safe_copy(helper_src, helper_path, 0o600)

    config_sha = sha256_file(live_config_path) if live_config_path.exists() else (
        overlay_manifest.get("source_sha256") or {}
    ).get("config")
    rollback = {
        "schema_version": "1.0",
        "baseline_sha": baseline,
        "candidate_sha": candidate,
        "service": service,
        "live_config_path": str(live_config_path),
        "live_config_sha256": config_sha,
        "source_lock_sha256": sha256_file(source_lock_path),
        "actions": [
            {"argv": ["git", "reset", "--hard", baseline]},
            {"operation": "restore-config-byte-for-byte"},
            {"argv": ["systemctl", "restart", service]},
        ],
    }
    atomic_write_bytes(rollback_path, canonical_json_bytes(rollback), mode=0o600)

    manifest = {
        "schema_version": "1.0",
        "status": "sealed",
        "canonical_artifact_root": str(artifact_root.resolve(strict=True)),
        "sealed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "baseline_sha": baseline,
        "candidate_sha": candidate,
        "service": service,
        "service_lock": source_lock.get("service"),
        "review": {
            "path": review_path.name,
            "sha256": sha256_file(review_path),
            "verdict": review.get("verdict"),
        },
        "profile_overlay": {
            "path": profile_overlay.name,
            "tree_sha256": overlay_tree["sha256"],
            "manifest_sha256": sha256_file(profile_overlay / "MANIFEST.json"),
        },
        "artifacts": {
            "bundle_sha256": sha256_file(bundle_path),
            "deployment_driver_sha256": sha256_file(driver_path),
            "release_safety_sha256": sha256_file(helper_path),
            "smoke_driver_sha256": sha256_file(smoke_path),
            "rollback_manifest_sha256": sha256_file(rollback_path),
            "p04_verification_sha256": sha256_file(p04_path),
            "p05_test_receipt_sha256": sha256_file(p05_test_path),
            "p05_static_scan_sha256": sha256_file(p05_scan_path),
            "independent_review_sha256": sha256_file(review_path),
        },
        "capability_contract_sha256": capability_sha,
        "live_lock": {
            "repo_path": live_lock["repo_path"],
            "branch": live_lock["branch"],
            "status_sha256": live_lock.get("status_sha256"),
            "tracked_diff_sha256": live_lock.get("tracked_diff_sha256"),
            "config_path": str(live_config_path),
            "config_sha256": config_sha,
            "source_lock_sha256": sha256_file(source_lock_path),
            "captured_at_seal": True,
        },
        "control_state": {
            "deployment_lock_path": "/run/lock/human20bot-team-operator.lock",
            "approval_consumption_root": str(
                live_config_path.parent / "state/human20bot-team-operator"
            ),
        },
    }
    atomic_write_bytes(release_path, canonical_json_bytes(manifest), mode=0o600)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--service", required=True)
    parser.add_argument("--profile-overlay", required=True, type=Path)
    parser.add_argument("--review", "--require-review", dest="review", required=True, type=Path)
    parser.add_argument("--live-config", type=Path, default=Path.home() / ".hermes/config.yaml")
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()
    try:
        manifest = seal_release(
            args.repo, args.baseline, args.artifact_root, args.service,
            args.profile_overlay, args.review, args.require_clean, args.live_config,
        )
        os.write(1, canonical_json_bytes(manifest))
        return 0
    except SafetyError as exc:
        os.write(2, f"ERROR: {exc}\n".encode())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
