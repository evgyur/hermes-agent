"""Deterministic package validation and opt-in host certification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

SETTINGS_QUOTA_BYTES = 4096
INVENTORY_PATH = "metadata/powerpack-gen2-files.json"
IGNORED_PARTS = frozenset({".git", "archives", "__pycache__", ".pytest_cache"})
SECRET_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{16,}"),
    re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{30,}\b"),
)
VALID_MODES = frozenset({"disabled", "compatibility", "gen2_only"})
VALID_VARIANTS = frozenset({"rentals", "employee"})
BUILTIN_COLLISION_NAMES = frozenset(
    {
        "power",
        "powerpack",
        "perplexity",
        "local",
        "local_command",
        "groq",
        "openai",
        "mistral",
        "xai",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(args: list[str], *, cwd: Path | None = None, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def load_manifest(root: Path) -> dict[str, Any]:
    value = json.loads((root / "metadata" / "powerpack-gen2.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("powerpack manifest must be an object")
    return value


def validate_settings(settings: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        encoded = json.dumps(settings, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return [f"settings are not JSON-serializable: {exc}"]
    if len(encoded) > SETTINGS_QUOTA_BYTES:
        errors.append(f"settings exceed {SETTINGS_QUOTA_BYTES} bytes")
    if settings.get("variant", "rentals") not in VALID_VARIANTS:
        errors.append("variant must be rentals or employee")
    if settings.get("mode", "disabled") not in VALID_MODES:
        errors.append("mode must be disabled, compatibility, or gen2_only")
    return errors


def _skill_candidates(root: Path) -> list[dict[str, Any]]:
    source = json.loads((root / "metadata" / "skills.json").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in source["skills"]:
        path = root / item["path"]
        rows.append({"slug": item["name"], "base_name": item["name"], "path": path})
    return rows


def skill_entries(root: Path, variant: str) -> list[dict[str, Any]]:
    candidates = _skill_candidates(root)
    counts: dict[str, int] = {}
    for row in candidates:
        counts[row["base_name"]] = counts.get(row["base_name"], 0) + 1
    entries: list[dict[str, Any]] = []
    used: set[str] = set()
    for row in candidates:
        name = row["base_name"] if counts[row["base_name"]] == 1 else f"{row['slug']}-{row['base_name']}"
        if name in used:
            name = f"{row['slug']}-{name}"
        used.add(name)
        entries.append({"name": name, "path": row["path"]})
    if variant == "employee":
        entries.append(
            {
                "name": "telegram-chip",
                "path": root / "overlays" / "employee" / "skills" / "telegram-chip" / "SKILL.md",
            }
        )
    return entries


def _package_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or IGNORED_PARTS.intersection(path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if relative == INVENTORY_PATH or path.suffix == ".pyc":
            continue
        files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def build_inventory(root: Path) -> dict[str, Any]:
    rows = [
        {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in _package_files(root)
    ]
    aggregate = hashlib.sha256()
    for row in rows:
        aggregate.update(row["path"].encode("utf-8") + b"\0")
        aggregate.update(row["sha256"].encode("ascii") + b"\0")
        aggregate.update(str(row["bytes"]).encode("ascii") + b"\n")
    manifest = load_manifest(root)
    return {
        "schema_version": 2,
        "package": manifest["id"],
        "version": manifest["version"],
        "inventory_excludes": [INVENTORY_PATH, "**/__pycache__/**", "**/*.pyc"],
        "file_count": len(rows),
        "aggregate_sha256": aggregate.hexdigest(),
        "files": rows,
    }


def _inventory_report(root: Path) -> dict[str, Any]:
    path = root / INVENTORY_PATH
    actual = build_inventory(root)
    try:
        declared = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "FAIL", "error": str(exc), "actual": actual}
    return {
        "status": "PASS" if declared == actual else "FAIL",
        "declared_sha256": _sha256(path),
        "aggregate_sha256": actual["aggregate_sha256"],
        "file_count": actual["file_count"],
        "mismatch": declared != actual,
    }


def write_inventory(root: Path) -> dict[str, Any]:
    inventory = build_inventory(root)
    path = root / INVENTORY_PATH
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return inventory


def _secret_scan(root: Path) -> list[str]:
    findings: list[str] = []
    extensions = {".py", ".md", ".json", ".yaml", ".yml", ".txt", ".csv", ".sh"}
    for path in _package_files(root):
        if path.suffix.lower() not in extensions:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            findings.append(path.relative_to(root).as_posix())
    return findings


def _runtime_report(root: Path, ci: bool) -> list[dict[str, Any]]:
    manifest = load_manifest(root)
    reports: list[dict[str, Any]] = []
    for runtime in manifest.get("managed_runtimes", []):
        executable = Path(os.path.expandvars(os.path.expanduser(runtime["executable"])))
        item: dict[str, Any] = {
            "id": runtime["id"],
            "executable": str(executable),
            "required": bool(runtime.get("required", False)),
        }
        if not executable.exists():
            item["status"] = "optional-missing" if ci or not item["required"] else "missing"
            reports.append(item)
            continue
        proc = _run([str(executable), *runtime.get("version_args", ["--version"])], timeout=runtime.get("timeout_seconds", 10))
        output = (proc.stdout + proc.stderr).strip()
        item.update(status="ok" if proc.returncode == 0 else "failed", version_output=output[:500])
        reports.append(item)
    return reports


def _git_host_report(repo_root: Path, upstream_sha: str) -> dict[str, Any]:
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    status = _run(["git", "status", "--porcelain"], cwd=repo_root)
    upstream = _run(["git", "cat-file", "-e", f"{upstream_sha}^{{commit}}"], cwd=repo_root)
    ancestor = _run(
        ["git", "merge-base", "--is-ancestor", upstream_sha, "HEAD"],
        cwd=repo_root,
    ) if upstream.returncode == 0 else subprocess.CompletedProcess([], 1, "", "upstream commit unavailable")
    changed = _run(
        ["git", "diff", "--name-only", upstream_sha, "HEAD", "--", ".", ":(exclude)packages/powerpack-gen2"],
        cwd=repo_root,
    ) if upstream.returncode == 0 else subprocess.CompletedProcess([], 1, "", "upstream commit unavailable")
    non_package = [line for line in changed.stdout.splitlines() if line.strip()]
    return {
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "clean": status.returncode == 0 and not status.stdout.strip(),
        "upstream_commit_present": upstream.returncode == 0,
        "upstream_is_ancestor": ancestor.returncode == 0,
        "core_matches_upstream": upstream.returncode == 0 and changed.returncode == 0 and not non_package,
        "core_changed_count": len(non_package),
        "core_changed_sample": non_package[:20],
    }


def _systemd_host_report(service: str) -> dict[str, Any]:
    show = _run(
        [
            "systemctl",
            "show",
            service,
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "MainPID",
            "-p",
            "ExecStart",
            "-p",
            "FragmentPath",
            "-p",
            "DropInPaths",
        ]
    )
    values: dict[str, str] = {}
    for line in show.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    pid = values.get("MainPID", "0")
    process: dict[str, Any] = {"pid": int(pid) if pid.isdigit() else 0}
    if pid.isdigit() and int(pid) > 0:
        for field in ("exe", "cwd"):
            try:
                process[field] = str(Path(f"/proc/{pid}/{field}").resolve())
            except OSError:
                process[field] = None
    paths = [values.get("FragmentPath", ""), *values.get("DropInPaths", "").split()]
    contracts = []
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            contracts.append({"path": str(path), "sha256": _sha256(path)})
    return {
        "status": "PASS" if show.returncode == 0 and values.get("ActiveState") == "active" else "FAIL",
        "properties": values,
        "process": process,
        "contracts": contracts,
    }


def _credential_report(mode: str) -> dict[str, Any]:
    names = ("H20_KEYS_BASE_URL", "H20_KEYS_STT_API_KEY", "H20_KEYS_API_KEY", "PERPLEXITY_API_KEY", "PPLX_API_KEY")
    present = {name: bool(os.environ.get(name)) for name in names}
    h20 = present["H20_KEYS_BASE_URL"] and (present["H20_KEYS_STT_API_KEY"] or present["H20_KEYS_API_KEY"])
    perplexity = present["PERPLEXITY_API_KEY"] or present["PPLX_API_KEY"]
    return {
        "status": "PASS" if mode != "gen2_only" or (h20 and perplexity) else "FAIL",
        "present": present,
        "values_exposed": False,
    }


def _check(checks: list[dict[str, Any]], name: str, passed: bool, *, required: bool = True, evidence: Any = None) -> None:
    checks.append({"name": name, "status": "PASS" if passed else "FAIL", "required": required, "evidence": evidence})


def run_doctor(
    *,
    root: Path,
    variant: str,
    upstream_sha: str,
    ci: bool,
    mode: str = "compatibility",
    host: bool = False,
    repo_root: Path | None = None,
    service: str = "human20team-hermes-gateway.service",
    expected_user: str = "human20team",
    active_plugin_root: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    manifest = load_manifest(root)
    checks: list[dict[str, Any]] = []
    settings_errors = validate_settings({"variant": variant, "mode": mode})
    _check(checks, "settings", not settings_errors, evidence=settings_errors)
    _check(checks, "manifest_kind", manifest.get("plugin_kind") == "standalone", evidence=manifest.get("plugin_kind"))
    _check(
        checks,
        "supported_upstream_sha",
        upstream_sha in manifest.get("supported_upstream_shas", []),
        evidence=upstream_sha,
    )

    skills = skill_entries(root, variant)
    missing = [str(item["path"].relative_to(root)) for item in skills if not item["path"].is_file()]
    names = [item["name"] for item in skills]
    _check(checks, "skills_present_unique", not missing and len(names) == len(set(names)), evidence={"missing": missing, "count": len(names)})

    declared_surfaces = set(manifest.get("surface_names", []))
    collisions = sorted(declared_surfaces & BUILTIN_COLLISION_NAMES)
    _check(checks, "collision_surface", not collisions, evidence={"collisions": collisions, "declared": sorted(declared_surfaces)})

    inventory = _inventory_report(root)
    _check(checks, "complete_package_inventory", inventory["status"] == "PASS", evidence=inventory)
    secrets = _secret_scan(root)
    _check(checks, "tracked_secret_scan", not secrets, evidence={"files": secrets})

    runtimes = _runtime_report(root, ci)
    runtime_failures = [item["id"] for item in runtimes if item["status"] in {"missing", "failed"}]
    _check(checks, "managed_runtimes", not runtime_failures, evidence=runtimes)

    host_report: dict[str, Any] | None = None
    if host:
        try:
            import pwd
        except ImportError as exc:  # pragma: no cover - Windows has no host certification
            raise RuntimeError("host certification requires a POSIX runtime") from exc
        repository = (repo_root or root.parents[1]).resolve()
        git_report = _git_host_report(repository, upstream_sha)
        systemd_report = _systemd_host_report(service)
        credentials = _credential_report(mode)
        plugin_manifest = root / "plugin.yaml"
        expected_uid = pwd.getpwnam(expected_user).pw_uid
        venv = Path(os.environ.get("VIRTUAL_ENV") or repository / "venv")
        venv_owner = venv.stat().st_uid if venv.exists() else None
        active_root = (active_plugin_root or root).resolve()
        active_inventory = _inventory_report(active_root)
        process = systemd_report.get("process", {})
        process_cwd = process.get("cwd")
        process_exe = process.get("exe")
        process_identity_ok = (
            process_cwd == str(repository)
            and isinstance(process_exe, str)
            and process_exe.startswith(str(venv.resolve()) + os.sep)
        )
        host_report = {
            "repository": git_report,
            "systemd": systemd_report,
            "credentials": credentials,
            "active_plugin_manifest": {
                "path": str(active_root / "plugin.yaml"),
                "sha256": _sha256(active_root / "plugin.yaml") if (active_root / "plugin.yaml").is_file() else None,
                "package_sha256": active_inventory.get("aggregate_sha256"),
            },
            "candidate_plugin_manifest": {"path": str(plugin_manifest), "sha256": _sha256(plugin_manifest)},
            "venv": {"path": str(venv), "owner_uid": venv_owner, "expected_uid": expected_uid},
            "process_identity_ok": process_identity_ok,
        }
        _check(checks, "host_repository_clean", git_report["clean"], required=mode == "gen2_only", evidence=git_report)
        _check(
            checks,
            "host_supported_upstream_ancestor",
            git_report["upstream_is_ancestor"],
            required=mode == "gen2_only",
            evidence=git_report,
        )
        _check(
            checks,
            "host_core_matches_upstream",
            git_report["core_matches_upstream"],
            required=False,
            evidence=git_report,
        )
        _check(checks, "host_service_process", systemd_report["status"] == "PASS", evidence=systemd_report)
        _check(checks, "host_process_loaded_identity", process_identity_ok, evidence={"cwd": process_cwd, "exe": process_exe})
        _check(checks, "host_venv_ownership", venv_owner == expected_uid, evidence=host_report["venv"])
        _check(
            checks,
            "host_active_plugin_identity",
            active_inventory.get("status") == "PASS"
            and active_inventory.get("aggregate_sha256") == inventory.get("aggregate_sha256"),
            evidence=host_report["active_plugin_manifest"],
        )
        _check(checks, "host_credentials_present", credentials["status"] == "PASS", required=mode == "gen2_only", evidence=credentials)

    required_failures = [item["name"] for item in checks if item["required"] and item["status"] != "PASS"]
    return {
        "schema_version": 2,
        "version": manifest["version"],
        "ok": not required_failures,
        "status": "PASS" if not required_failures else "FAIL",
        "mode": mode,
        "variant": variant,
        "supported_upstream_sha": upstream_sha,
        "registered_skill_count": len(skills),
        "package_sha256": inventory.get("aggregate_sha256"),
        "checks": checks,
        "required_failures": required_failures,
        "host": host_report,
        "state_bytes": 0,
    }
