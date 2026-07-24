#!/usr/bin/env python3
"""Human20Bot candidate scanner: narrow wrapper around changed-tree audit."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from typing import Any

from audit_changed_tree import audit_repository
from release_safety import SafetyError, atomic_write_bytes, canonical_json_bytes, run_argv


def scan_candidate(
    repo: Path | str,
    baseline: str,
    forbid_private_identifiers: bool = True,
    forbid_secrets: bool = True,
) -> dict[str, Any]:
    repo = Path(repo).resolve(strict=True)
    report = audit_repository(
        repo,
        baseline,
        forbid_private_identifiers=forbid_private_identifiers,
        forbid_secrets=forbid_secrets,
    )
    candidate = run_argv(["git", "rev-parse", "HEAD"], cwd=repo).stdout.decode().strip()
    diff = run_argv(
        ["git", "diff", "--no-ext-diff", "--binary", f"{baseline}..{candidate}"], cwd=repo
    ).stdout
    report["candidate_sha"] = candidate
    report["candidate_diff_sha256"] = hashlib.sha256(diff).hexdigest()
    report["scanner"] = "human20bot-candidate-v1"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", "--json-out", dest="output", type=Path)
    parser.add_argument("--forbid-private-identifiers", action="store_true")
    parser.add_argument("--forbid-secrets", action="store_true")
    args = parser.parse_args()
    try:
        report = scan_candidate(
            args.repo,
            args.baseline,
            args.forbid_private_identifiers,
            args.forbid_secrets,
        )
        encoded = canonical_json_bytes(report)
        if args.output:
            atomic_write_bytes(args.output, encoded, mode=0o600)
        else:
            os.write(1, encoded)
        return 0 if report["status"] == "pass" else 1
    except SafetyError as exc:
        os.write(2, f"ERROR: {exc}\n".encode())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
