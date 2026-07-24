#!/usr/bin/env python3
"""Fail-closed audit of files changed from a locked Git baseline."""
from __future__ import annotations

import argparse
import os
import re
import stat
from pathlib import Path
from typing import Any

from release_safety import SafetyError, atomic_write_bytes, canonical_json_bytes, read_nofollow, run_argv, sha256_bytes

_PUBLIC_IDENTIFIERS = {"8928336881", "@Human20Bot", "Human20Bot"}
_PRIVATE_ID = re.compile(rb"(?<!\d)-100\d{10,}(?!\d)")
_SECRET = re.compile(
    rb"(?im)^\s*(?:[\"']?)(?:export\s+)?((?:[A-Z0-9]+_)*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY)(?:_[A-Z0-9]+)*)\s*[:=]\s*[\"']?([A-Za-z0-9_./+=:-]{8,})"
)


def _untracked_paths(repo: Path) -> set[str]:
    raw_paths = run_argv(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repo
    ).stdout.split(b"\0")
    return {raw.decode("utf-8", "strict") for raw in raw_paths if raw}


def _added_content(repo: Path, baseline: str, rel: str) -> bytes:
    diff = run_argv(
        ["git", "diff", "--no-ext-diff", "--unified=0", baseline, "--", rel], cwd=repo
    ).stdout
    lines: list[bytes] = []
    for line in diff.splitlines():
        if line.startswith(b"+") and not line.startswith(b"+++"):
            lines.append(line[1:])
    return b"\n".join(lines)


def _git_paths(repo: Path, baseline: str) -> list[str]:
    tracked = run_argv(
        ["git", "diff", "--name-only", "-z", baseline, "--"], cwd=repo
    ).stdout.split(b"\0")
    untracked = run_argv(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repo
    ).stdout.split(b"\0")
    values: set[str] = set()
    for raw in tracked + untracked:
        if not raw:
            continue
        try:
            rel = raw.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise SafetyError("changed path is not UTF-8") from exc
        part = Path(rel)
        if part.is_absolute() or ".." in part.parts or not rel:
            raise SafetyError(f"unsafe changed path: {rel!r}")
        values.add(part.as_posix())
    return sorted(values)


def _violation(path: str, rule: str, detail: str) -> dict[str, str]:
    return {"path": path, "rule": rule, "detail": detail}


def audit_repository(
    repo: Path | str,
    baseline: str,
    forbid_private_identifiers: bool = True,
    forbid_secrets: bool = True,
) -> dict[str, Any]:
    repo = Path(repo).resolve(strict=True)
    if not (repo / ".git").exists():
        # Worktrees use a .git file, normal repositories a directory.
        raise SafetyError(f"git repository required: {repo}")
    run_argv(["git", "cat-file", "-e", f"{baseline}^{{commit}}"], cwd=repo)
    paths = _git_paths(repo, baseline)
    untracked = _untracked_paths(repo)
    violations: list[dict[str, str]] = []
    files: list[dict[str, Any]] = []
    for rel in paths:
        path = repo / rel
        try:
            info = path.lstat()
        except FileNotFoundError:
            # Deleted tracked file is legitimate metadata and has no content to scan.
            files.append({"path": rel, "kind": "deleted"})
            continue
        if stat.S_ISLNK(info.st_mode):
            violations.append(_violation(rel, "symlink", "changed symlink is forbidden"))
            files.append({"path": rel, "kind": "symlink"})
            continue
        if not stat.S_ISREG(info.st_mode):
            violations.append(_violation(rel, "file_type", "changed path must be a regular file"))
            continue
        data = read_nofollow(path)
        scan_data = data if rel in untracked else _added_content(repo, baseline, rel)
        files.append({"path": rel, "kind": "file", "size": len(data), "sha256": sha256_bytes(data)})
        if forbid_secrets:
            for match in _SECRET.finditer(scan_data):
                key = match.group(1).decode("ascii", "ignore")
                value = match.group(2).decode("ascii", "ignore")
                upper_key = key.upper()
                lower_value = value.lower()
                is_rule_declaration = (
                    any(marker in upper_key for marker in ("PATTERN", "REGEX", "REDACT"))
                    or upper_key.endswith("_RE")
                    or upper_key.startswith("NO_SECRET")
                    or lower_value == "re.compile"
                    or lower_value.startswith(("match.", "args.", "getattr", "os.", "self."))
                )
                if not is_rule_declaration:
                    violations.append(_violation(rel, "secret", "credential-shaped assignment detected"))
                    break
        if forbid_private_identifiers:
            for match in _PRIVATE_ID.finditer(scan_data):
                token = match.group(0).decode("ascii", "ignore")
                if token not in _PUBLIC_IDENTIFIERS:
                    violations.append(_violation(rel, "private_identifier", "private chat-shaped identifier detected"))
                    break
    return {
        "schema_version": "1.0",
        "status": "pass" if not violations else "fail",
        "baseline": baseline,
        "changed_path_count": len(paths),
        "changed_files": files,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--forbid-private-identifiers", action="store_true")
    parser.add_argument("--forbid-secrets", action="store_true")
    args = parser.parse_args()
    try:
        report = audit_repository(
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
