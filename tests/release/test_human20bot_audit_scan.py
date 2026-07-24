from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from release_test_helpers import git, init_repo
from audit_changed_tree import audit_repository
from release_safety import SafetyError, atomic_write_bytes, strict_json_load
from scan_human20bot_candidate import scan_candidate


def test_audit_includes_untracked_secret_and_returns_metadata_only(tmp_path: Path) -> None:
    repo, baseline = init_repo(tmp_path)
    (repo / "safe.py").write_text("VALUE = 'synthetic'\n", encoding="utf-8")
    git(repo, "add", "safe.py")
    git(repo, "commit", "-qm", "candidate")
    (repo / "untracked.env").write_text(
        "API_" + "TOKEN=synthetic-secret-value-that-must-not-ship\n", encoding="utf-8"
    )

    report = audit_repository(
        repo,
        baseline,
        forbid_private_identifiers=True,
        forbid_secrets=True,
    )

    assert report["status"] == "fail"
    assert report["changed_path_count"] == 2
    assert report["violations"][0]["path"] == "untracked.env"
    assert "synthetic-secret-value" not in json.dumps(report)


def test_audit_rejects_changed_symlink(tmp_path: Path) -> None:
    repo, baseline = init_repo(tmp_path)
    os.symlink("base.txt", repo / "alias")

    report = audit_repository(repo, baseline, True, True)

    assert report["status"] == "fail"
    assert any(item["rule"] == "symlink" for item in report["violations"])


def test_audit_allows_contract_public_bot_identity_but_rejects_private_chat_shape(
    tmp_path: Path,
) -> None:
    repo, baseline = init_repo(tmp_path)
    (repo / "identity.txt").write_text(
        "@Human20Bot id 8928336881\nprivate synthetic chat " + "-100" + "1234567890\n",
        encoding="utf-8",
    )

    report = scan_candidate(repo, baseline, True, True)

    private_hits = [v for v in report["violations"] if v["rule"] == "private_identifier"]
    assert len(private_hits) == 1
    assert private_hits[0]["path"] == "identity.txt"
    assert report["candidate_sha"] == git(repo, "rev-parse", "HEAD").decode().strip()
    assert len(report["candidate_diff_sha256"]) == 64


def test_audit_rejects_lowercase_python_secret_literal(tmp_path: Path) -> None:
    repo, baseline = init_repo(tmp_path)
    (repo / "unsafe.py").write_text(
        "to" + "ken = \"abcdefghijklmno\"\n", encoding="utf-8"
    )
    report = audit_repository(repo, baseline, True, True)
    assert report["status"] == "fail"
    assert any(item["rule"] == "secret" for item in report["violations"])


def test_audit_does_not_treat_secret_scanner_control_flag_as_credential(tmp_path: Path) -> None:
    repo, baseline = init_repo(tmp_path)
    (repo / "scanner.py").write_text(
        "forbid_secrets=forbid_secrets\n", encoding="utf-8"
    )
    report = audit_repository(repo, baseline, True, True)
    assert report["status"] == "pass"


def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a": 1, "a": 2}\n', encoding="utf-8")
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"a": NaN}\n', encoding="utf-8")

    with pytest.raises(SafetyError):
        strict_json_load(duplicate)
    with pytest.raises(SafetyError):
        strict_json_load(nonfinite)


def test_atomic_write_rejects_symlink_and_sets_private_mode(tmp_path: Path) -> None:
    target = tmp_path / "receipt.json"
    atomic_write_bytes(target, b"{}\n", mode=0o600)
    assert target.read_bytes() == b"{}\n"
    assert target.stat().st_mode & 0o777 == 0o600

    other = tmp_path / "other"
    other.write_text("untouched", encoding="utf-8")
    target.unlink()
    target.symlink_to(other)
    with pytest.raises(SafetyError):
        atomic_write_bytes(target, b"bad", mode=0o600)
    assert other.read_text(encoding="utf-8") == "untouched"
