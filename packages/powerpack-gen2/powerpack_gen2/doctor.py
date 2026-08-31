"""Deterministic package validation and opt-in host certification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
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
MINIMUM_SQLITE_VERSION = (3, 53, 1)
MANAGED_STT_PROVIDER = "human20-keys-groq"
MANAGED_STT_MODEL = "whisper-large-v3"
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
            "-p",
            "Restart",
            "-p",
            "RestartPreventExitStatus",
            "-p",
            "RestartForceExitStatus",
            "-p",
            "TimeoutStopUSec",
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


def _pythonpath_identity_report(
    raw_environment: bytes,
    *,
    repository: Path,
    process_cwd: Path,
) -> dict[str, Any]:
    """Validate PYTHONPATH scope without returning any environment value."""

    value: bytes | None = None
    for row in raw_environment.split(b"\0"):
        if row.startswith(b"PYTHONPATH="):
            value = row.split(b"=", 1)[1]
            break
    if value is None or value == b"":
        return {
            "status": "PASS",
            "present": value is not None,
            "entry_count": 0,
            "outside_candidate_count": 0,
            "values_exposed": False,
        }

    repository = repository.resolve()
    process_cwd = process_cwd.resolve()
    entries = [item for item in os.fsdecode(value).split(os.pathsep) if item]
    outside = 0
    for item in entries:
        path = Path(item)
        if not path.is_absolute():
            path = process_cwd / path
        resolved = path.resolve()
        if resolved != repository and repository not in resolved.parents:
            outside += 1
    return {
        "status": "PASS" if outside == 0 else "FAIL",
        "present": True,
        "entry_count": len(entries),
        "outside_candidate_count": outside,
        "values_exposed": False,
    }


def _process_pythonpath_report(
    pid: int,
    *,
    repository: Path,
    process_cwd: Path,
) -> dict[str, Any]:
    try:
        raw_environment = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {
            "status": "FAIL",
            "present": False,
            "entry_count": 0,
            "outside_candidate_count": 0,
            "values_exposed": False,
            "readable": False,
        }
    return _pythonpath_identity_report(
        raw_environment,
        repository=repository,
        process_cwd=process_cwd,
    )


def _runtime_env_identity_report(
    raw_environment: bytes,
    *,
    expected_venv: Path,
    path_separator: str = os.pathsep,
) -> dict[str, Any]:
    """Prove child tool processes inherit the same venv as the gateway.

    Only booleans are returned: service environments can contain secrets and
    must never be copied into doctor receipts.
    """

    values: dict[bytes, bytes] = {}
    for row in raw_environment.split(b"\0"):
        if b"=" not in row:
            continue
        key, value = row.split(b"=", 1)
        if key in {b"VIRTUAL_ENV", b"PATH"}:
            values[key] = value

    expected_venv = expected_venv.resolve()
    expected_bin = (expected_venv / "bin").resolve()
    try:
        configured_venv = Path(os.fsdecode(values.get(b"VIRTUAL_ENV", b""))).resolve()
    except OSError:
        configured_venv = Path()
    path_entries = [
        Path(item).resolve()
        for item in os.fsdecode(values.get(b"PATH", b"")).split(path_separator)
        if item
    ]
    virtual_env_exact = configured_venv == expected_venv
    venv_bin_first = bool(path_entries) and path_entries[0] == expected_bin
    return {
        "status": "PASS" if virtual_env_exact and venv_bin_first else "FAIL",
        "virtual_env_exact": virtual_env_exact,
        "venv_bin_first": venv_bin_first,
        "values_exposed": False,
    }


def _process_runtime_env_report(pid: int, *, expected_venv: Path) -> dict[str, Any]:
    try:
        raw_environment = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {
            "status": "FAIL",
            "virtual_env_exact": False,
            "venv_bin_first": False,
            "values_exposed": False,
            "readable": False,
        }
    return _runtime_env_identity_report(
        raw_environment,
        expected_venv=expected_venv,
    )


def _exit_status_contains(raw: str, expected: int) -> bool:
    tokens = {part.strip() for part in re.split(r"[\s,]+", raw) if part.strip()}
    return str(expected) in tokens


def _service_failure_policy_report(properties: dict[str, str]) -> dict[str, Any]:
    restart = properties.get("Restart", "")
    restarts = restart in {"always", "on-failure"}
    fatal_config_stops = _exit_status_contains(properties.get("RestartPreventExitStatus", ""), 78)
    forced_restart = _exit_status_contains(properties.get("RestartForceExitStatus", ""), 75)
    return {
        "status": "PASS" if restarts and fatal_config_stops and forced_restart else "FAIL",
        "restart": restart,
        "restarts": restarts,
        "fatal_config_stops": fatal_config_stops,
        "forced_restart": forced_restart,
    }


def _parse_version(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in value.strip().split("."):
        match = re.match(r"\d+", part)
        if not match:
            break
        parts.append(int(match.group(0)))
    return tuple(parts)


def _sqlite_version_report(version: str) -> dict[str, Any]:
    minimum = ".".join(str(part) for part in MINIMUM_SQLITE_VERSION)
    parsed = _parse_version(version)
    return {
        "status": "PASS" if parsed >= MINIMUM_SQLITE_VERSION else "FAIL",
        "version": version,
        "minimum": minimum,
    }


def _sqlite_runtime_report(executable: str | None = None) -> dict[str, Any]:
    if executable:
        proc = _run(
            [
                executable,
                "-c",
                "import json,sqlite3; print(json.dumps({'version': sqlite3.sqlite_version}))",
            ]
        )
        if proc.returncode != 0:
            return {
                "status": "FAIL",
                "version": None,
                "minimum": ".".join(str(part) for part in MINIMUM_SQLITE_VERSION),
                "probe_failed": True,
            }
        try:
            version = str(json.loads(proc.stdout)["version"])
        except (KeyError, TypeError, ValueError):
            return {
                "status": "FAIL",
                "version": None,
                "minimum": ".".join(str(part) for part in MINIMUM_SQLITE_VERSION),
                "probe_failed": True,
            }
        return _sqlite_version_report(version)
    return _sqlite_version_report(sqlite3.sqlite_version)


def _classify_deleted_state_handles(targets: Iterable[str]) -> dict[str, Any]:
    kinds: set[str] = set()
    for target in targets:
        normalized = target.lower()
        if "(deleted)" not in normalized:
            continue
        if "state.db-wal" in normalized:
            kinds.add("wal")
        if "state.db-shm" in normalized:
            kinds.add("shm")
    return {
        "status": "PASS" if not kinds else "FAIL",
        "deleted_count": len(kinds),
        "kinds": sorted(kinds),
    }


def _state_handle_report(pid: int) -> dict[str, Any]:
    targets: list[str] = []
    fd_root = Path(f"/proc/{pid}/fd")
    if pid <= 0 or not fd_root.is_dir():
        return {"status": "FAIL", "deleted_count": 0, "kinds": [], "probe_failed": True}
    try:
        for descriptor in fd_root.iterdir():
            try:
                targets.append(os.readlink(descriptor))
            except OSError:
                continue
    except OSError:
        return {"status": "FAIL", "deleted_count": 0, "kinds": [], "probe_failed": True}
    return _classify_deleted_state_handles(targets)


def _venv_interpreter_paths(venv: Path) -> set[str]:
    bin_root = venv / "bin"
    if not bin_root.is_dir():
        return set()
    paths: set[str] = set()
    for candidate in bin_root.glob("python*"):
        if candidate.is_file():
            paths.add(str(candidate.resolve()))
    return paths


def _process_identity_report(
    *,
    repository: Path,
    venv: Path,
    process_cwd: str | None,
    process_exe: str | None,
) -> dict[str, Any]:
    interpreters = _venv_interpreter_paths(venv)
    cwd_matches = process_cwd == str(repository)
    executable_matches = isinstance(process_exe, str) and process_exe in interpreters
    return {
        "status": "PASS" if cwd_matches and executable_matches else "FAIL",
        "cwd_matches": cwd_matches,
        "executable_matches": executable_matches,
        "interpreter_count": len(interpreters),
    }


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml

        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _stt_is_managed(config: dict[str, Any]) -> bool:
    stt = config.get("stt")
    if not isinstance(stt, dict) or stt.get("enabled") is not True:
        return False
    provider = stt.get("provider")
    provider_config = stt.get(MANAGED_STT_PROVIDER)
    return (
        provider == MANAGED_STT_PROVIDER
        and isinstance(provider_config, dict)
        and provider_config.get("model") == MANAGED_STT_MODEL
    )


def _cron_self_delivery(profile_id: str, profile_home: Path) -> list[str]:
    findings: list[str] = []
    cron_root = profile_home / "cron"
    if not cron_root.is_dir():
        return findings
    for path in sorted(cron_root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        if not isinstance(jobs, list):
            continue
        for index, job in enumerate(jobs):
            if not isinstance(job, dict) or job.get("enabled") is False:
                continue
            if job.get("deliver") != "bot-chat":
                continue
            job_id = str(job.get("id") or f"{path.stem}:{index}")
            findings.append(f"{profile_id}:{job_id}")
    return findings


def _operational_config_report(hermes_home: Path) -> dict[str, Any]:
    homes: list[tuple[str, Path]] = [("root", hermes_home)]
    profiles = hermes_home / "profiles"
    if profiles.is_dir():
        homes.extend((path.name, path) for path in sorted(profiles.iterdir()) if path.is_dir())

    stt_drift: list[str] = []
    cron_self_delivery: list[str] = []
    for profile_id, profile_home in homes:
        config_path = profile_home / "config.yaml"
        config = _load_mapping(config_path)
        if config_path.is_file() and (profile_id == "root" or "stt" in config) and not _stt_is_managed(config):
            stt_drift.append(profile_id)
        cron_self_delivery.extend(_cron_self_delivery(profile_id, profile_home))
    return {
        "status": "PASS" if not stt_drift and not cron_self_delivery else "FAIL",
        "stt_drift": stt_drift,
        "cron_self_delivery": cron_self_delivery,
    }


def _credential_report(mode: str, *, require_perplexity: bool = True) -> dict[str, Any]:
    names = ("H20_KEYS_BASE_URL", "H20_KEYS_STT_API_KEY", "H20_KEYS_API_KEY")
    present = {name: bool(os.environ.get(name)) for name in names}
    h20_stt = present["H20_KEYS_BASE_URL"] and (
        present["H20_KEYS_STT_API_KEY"] or present["H20_KEYS_API_KEY"]
    )
    h20_perplexity = present["H20_KEYS_BASE_URL"] and present["H20_KEYS_API_KEY"]
    return {
        "status": "PASS"
        if mode != "gen2_only"
        or (h20_stt and (h20_perplexity or not require_perplexity))
        else "FAIL",
        "present": present,
        "perplexity_required": require_perplexity,
        "perplexity_via_h20_keys": h20_perplexity,
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
    hermes_home: Path | None = None,
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
        configured_home = (hermes_home or Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")).resolve()
        git_report = _git_host_report(repository, upstream_sha)
        systemd_report = _systemd_host_report(service)
        service_policy = _service_failure_policy_report(systemd_report.get("properties", {}))
        root_config = _load_mapping(configured_home / "config.yaml")
        web_config = root_config.get("web") if isinstance(root_config.get("web"), dict) else {}
        credentials = _credential_report(
            mode,
            require_perplexity=web_config.get("search_backend") == "human20-perplexity",
        )
        plugin_manifest = root / "plugin.yaml"
        expected_uid = pwd.getpwnam(expected_user).pw_uid
        venv = Path(os.environ.get("VIRTUAL_ENV") or repository / "venv")
        venv_owner = venv.stat().st_uid if venv.exists() else None
        active_root = (active_plugin_root or root).resolve()
        active_inventory = _inventory_report(active_root)
        process = systemd_report.get("process", {})
        process_cwd = process.get("cwd")
        process_exe = process.get("exe")
        sqlite_runtime = _sqlite_runtime_report(process_exe if isinstance(process_exe, str) else None)
        state_handles = _state_handle_report(int(process.get("pid") or 0))
        operational_config = _operational_config_report(configured_home)
        process_identity = _process_identity_report(
            repository=repository,
            venv=venv,
            process_cwd=process_cwd if isinstance(process_cwd, str) else None,
            process_exe=process_exe if isinstance(process_exe, str) else None,
        )
        pythonpath_identity = _process_pythonpath_report(
            int(process.get("pid") or 0),
            repository=repository,
            process_cwd=Path(process_cwd) if isinstance(process_cwd, str) else repository,
        )
        runtime_env_identity = _process_runtime_env_report(
            int(process.get("pid") or 0),
            expected_venv=venv,
        )
        process_identity_ok = process_identity["status"] == "PASS"
        host_report = {
            "repository": git_report,
            "systemd": systemd_report,
            "credentials": credentials,
            "service_failure_policy": service_policy,
            "sqlite_runtime": sqlite_runtime,
            "deleted_state_handles": state_handles,
            "operational_config": operational_config,
            "active_plugin_manifest": {
                "path": str(active_root / "plugin.yaml"),
                "sha256": _sha256(active_root / "plugin.yaml") if (active_root / "plugin.yaml").is_file() else None,
                "package_sha256": active_inventory.get("aggregate_sha256"),
            },
            "candidate_plugin_manifest": {"path": str(plugin_manifest), "sha256": _sha256(plugin_manifest)},
            "venv": {"path": str(venv), "owner_uid": venv_owner, "expected_uid": expected_uid},
            "process_identity_ok": process_identity_ok,
            "process_identity": process_identity,
            "pythonpath_identity": pythonpath_identity,
            "runtime_env_identity": runtime_env_identity,
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
        _check(checks, "host_service_failure_policy", service_policy["status"] == "PASS", required=mode == "gen2_only", evidence=service_policy)
        _check(checks, "host_sqlite_runtime", sqlite_runtime["status"] == "PASS", required=mode == "gen2_only", evidence=sqlite_runtime)
        _check(checks, "host_deleted_state_handles", state_handles["status"] == "PASS", required=mode == "gen2_only", evidence=state_handles)
        _check(checks, "host_operational_config", operational_config["status"] == "PASS", required=mode == "gen2_only", evidence=operational_config)
        _check(checks, "host_process_loaded_identity", process_identity_ok, evidence={"cwd": process_cwd, "exe": process_exe})
        _check(
            checks,
            "host_pythonpath_identity",
            pythonpath_identity["status"] == "PASS",
            required=mode == "gen2_only",
            evidence=pythonpath_identity,
        )
        _check(
            checks,
            "host_runtime_env_identity",
            runtime_env_identity["status"] == "PASS",
            required=mode == "gen2_only",
            evidence=runtime_env_identity,
        )
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
