#!/usr/bin/env python3
"""Approval-bound Human20Bot deployment engine with automatic rollback."""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import os
import pwd
import queue
import stat
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from release_safety import (
    SafetyError,
    atomic_write_bytes,
    canonical_json_bytes,
    hash_tree,
    read_nofollow,
    sha256_file,
    strict_json_load,
)


class DeploymentError(SafetyError):
    pass


def _deep_merge(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(current)
    for key, value in patch.items():
        if key == "schema_version":
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def merge_overlay_config(current: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(current, dict) or not isinstance(overlay, dict):
        raise DeploymentError("config and overlay must be mappings")
    if str(overlay.get("schema_version")) != "1.0":
        raise DeploymentError("unsupported overlay schema")
    forbidden = {"bot_token", "token", "password", "secret", "api_key", "providers", "tools", "toolsets", "plugins", "mcp_servers"}
    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in forbidden or any(mark in lowered for mark in ("token", "password", "secret", "credential")):
                raise DeploymentError(f"overlay contains forbidden capability/credential key: {'.'.join(path + (str(key),))}")
            walk(child, path + (str(key),))
    walk({k: v for k, v in overlay.items() if k != "schema_version"})
    return _deep_merge(current, overlay)


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise DeploymentError("approval timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeploymentError("invalid approval timestamp") from exc
    if parsed.tzinfo is None:
        raise DeploymentError("approval timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def validate_approval_pair(
    manifest: dict[str, Any],
    app3: dict[str, Any],
    app4: dict[str, Any],
    release_manifest_sha256: str,
    now: datetime | None = None,
) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expected_classes = {"APP-003": "production", "APP-004": "destructive-if-live"}
    expected = {
        "candidate_sha": manifest.get("candidate_sha"),
        "profile_overlay_sha256": (manifest.get("profile_overlay") or {}).get("tree_sha256"),
        "service": manifest.get("service"),
        "rollback_manifest_sha256": (manifest.get("artifacts") or {}).get("rollback_manifest_sha256"),
        "release_manifest_sha256": release_manifest_sha256,
    }
    destination_binding: dict[str, Any] | None = None
    seen_ids: set[str] = set()
    for approval in (app3, app4):
        aid = approval.get("approval_id")
        if not isinstance(aid, str) or aid in seen_ids:
            raise DeploymentError("approval ids must be distinct strings")
        seen_ids.add(aid)
        if aid not in expected_classes or approval.get("class_name") != expected_classes[aid]:
            raise DeploymentError("approval class/id mismatch")
        if approval.get("status") != "approved":
            raise DeploymentError("approval is not approved")
        for key, value in expected.items():
            if approval.get(key) != value:
                raise DeploymentError(f"approval binding mismatch: {key}")
        if not (_time(approval.get("not_before")) <= now <= _time(approval.get("not_after"))):
            raise DeploymentError("approval window is not active")
        if _time(approval.get("issued_at")) < _time(manifest.get("sealed_at")):
            raise DeploymentError("approval predates sealed release")
        destinations = approval.get("canary_destinations")
        if not isinstance(destinations, dict) or set(destinations) != {"owner_dm", "test_chat", "test_thread"}:
            raise DeploymentError("approval canary destinations are incomplete")
        if destination_binding is None:
            destination_binding = destinations
        elif destinations != destination_binding:
            raise DeploymentError("approval destination binding mismatch")
    scope = {"expected": expected, "destinations": destination_binding, "approval_ids": ["APP-003", "APP-004"]}
    return hashlib.sha256(canonical_json_bytes(scope)).hexdigest()


class DeploymentEngine:
    def __init__(
        self,
        repo: Path | str,
        config_path: Path | str,
        service: str,
        runner: Callable[[list[str], Path | None], subprocess.CompletedProcess] | None = None,
    ) -> None:
        self.repo = Path(repo)
        self.config_path = Path(config_path)
        self.service = service
        self.runner = runner or self._run

    def _run(self, argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        kwargs: dict[str, Any] = {}
        if argv and argv[0] == "git" and os.geteuid() == 0:
            owner = (cwd or self.repo).stat()
            kwargs.update(user=owner.st_uid, group=owner.st_gid)
        return subprocess.run(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            **kwargs,
        )

    def _call(self, argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        proc = self.runner(argv, cwd)
        if proc.returncode:
            stderr = (proc.stderr or b"").decode("utf-8", "replace")[:300]
            raise DeploymentError(f"command failed: {argv[0]}: {stderr}")
        return proc

    def _git_text(self, *args: str) -> str:
        return (self._call(["git", *args], self.repo).stdout or b"").decode().strip()

    def preflight(self, baseline: str, candidate: str) -> dict[str, Any]:
        if self._git_text("rev-parse", "HEAD") != baseline:
            raise DeploymentError("live HEAD does not match baseline lock")
        if self._git_text("status", "--porcelain=v1", "--untracked-files=no"):
            raise DeploymentError("live tracked checkout is dirty")
        self._call(["git", "merge-base", "--is-ancestor", baseline, candidate], self.repo)
        if not self.config_path.is_file() or self.config_path.is_symlink():
            raise DeploymentError("safe live config file required")
        active = self._call(["systemctl", "is-active", self.service]).stdout.decode().strip()
        if active != "active":
            raise DeploymentError("declared service is not active")
        return {"status": "preflight-pass", "baseline": baseline, "candidate": candidate, "service": self.service}

    def apply(
        self,
        baseline: str,
        candidate: str,
        overlay: dict[str, Any],
        smoke: Callable[[], dict[str, Any]],
        auto_rollback: bool = True,
        rollback_health: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.preflight(baseline, candidate)
        original = read_nofollow(self.config_path)
        config_info = self.config_path.stat()
        original_mode = config_info.st_mode & 0o777
        original_uid = config_info.st_uid
        original_gid = config_info.st_gid
        code_changed = False
        config_changed = False
        try:
            current = yaml.safe_load(original) or {}
            if not isinstance(current, dict):
                raise DeploymentError("live config root must be a mapping")
            merged = merge_overlay_config(current, overlay)
            rendered = yaml.safe_dump(merged, allow_unicode=True, sort_keys=False).encode("utf-8")
            self._call(["git", "merge", "--ff-only", candidate], self.repo)
            code_changed = True
            atomic_write_bytes(self.config_path, rendered, mode=original_mode)
            os.chown(self.config_path, original_uid, original_gid, follow_symlinks=False)
            config_changed = True
            self._call(["systemctl", "restart", self.service])
            if self._call(["systemctl", "is-active", self.service]).stdout.decode().strip() != "active":
                raise DeploymentError("service inactive after restart")
            smoke_result = smoke()
            if not isinstance(smoke_result, dict) or smoke_result.get("status") != "pass":
                raise DeploymentError("smoke did not return pass")
            return {"status": "applied", "candidate_sha": candidate, "smoke": smoke_result}
        except Exception as exc:
            if not auto_rollback:
                raise DeploymentError(f"deployment failed without rollback: {exc}") from exc
            rollback_errors: list[str] = []
            try:
                if code_changed:
                    self._call(["git", "reset", "--hard", baseline], self.repo)
            except Exception as rollback_exc:
                rollback_errors.append(f"code:{rollback_exc}")
            try:
                if config_changed:
                    atomic_write_bytes(self.config_path, original, mode=original_mode)
                    os.chown(self.config_path, original_uid, original_gid, follow_symlinks=False)
            except Exception as rollback_exc:
                rollback_errors.append(f"config:{rollback_exc}")
            try:
                self._call(["systemctl", "restart", self.service])
                if self._git_text("rev-parse", "HEAD") != baseline:
                    raise DeploymentError("rollback HEAD verification failed")
                if read_nofollow(self.config_path) != original:
                    raise DeploymentError("rollback config verification failed")
                restored_info = self.config_path.stat()
                if (
                    stat.S_IMODE(restored_info.st_mode) != original_mode
                    or restored_info.st_uid != original_uid
                    or restored_info.st_gid != original_gid
                ):
                    raise DeploymentError("rollback config metadata verification failed")
                active = self._call(["systemctl", "is-active", self.service]).stdout.decode().strip()
                if active != "active":
                    raise DeploymentError("rollback service verification failed")
                if rollback_health is not None:
                    health = rollback_health()
                    if not isinstance(health, dict) or health.get("status") != "pass":
                        raise DeploymentError("rollback runtime health verification failed")
            except Exception as rollback_exc:
                rollback_errors.append(f"service-or-readback:{rollback_exc}")
            if rollback_errors:
                raise DeploymentError(
                    f"deployment failed and rollback incomplete: {type(exc).__name__}; "
                    f"rollback errors={rollback_errors}"
                ) from exc
            raise DeploymentError(f"deployment failed and rolled back: {exc}") from exc


def _write_owned_json(path: Path, value: dict[str, Any]) -> None:
    parent_info = path.parent.stat()
    atomic_write_bytes(path, canonical_json_bytes(value), mode=0o600)
    os.chown(path, parent_info.st_uid, parent_info.st_gid, follow_symlinks=False)


def _verify_manifest_preflight(
    manifest_path: Path,
    manifest: dict[str, Any],
    app3: dict[str, Any],
    app4: dict[str, Any],
) -> tuple[DeploymentEngine, dict[str, Any], str]:
    manifest_hash = sha256_file(manifest_path)
    scope_hash = validate_approval_pair(manifest, app3, app4, manifest_hash)
    live_lock = manifest.get("live_lock") or {}
    repo = Path(str(live_lock.get("repo_path", "")))
    config = Path(str(live_lock.get("config_path", "")))
    engine = DeploymentEngine(repo, config, str(manifest.get("service", "")))
    preflight = engine.preflight(str(manifest.get("baseline_sha")), str(manifest.get("candidate_sha")))

    status = engine._call(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], repo
    ).stdout or b""
    tracked_diff = engine._call(["git", "diff", "--binary", "HEAD"], repo).stdout or b""
    if hashlib.sha256(status).hexdigest() != live_lock.get("status_sha256"):
        raise DeploymentError("live status hash changed from source lock")
    if hashlib.sha256(tracked_diff).hexdigest() != live_lock.get("tracked_diff_sha256"):
        raise DeploymentError("live tracked-diff hash changed from source lock")
    if sha256_file(config) != live_lock.get("config_sha256"):
        raise DeploymentError("live config hash changed from release lock")
    index_lock = engine._git_text("rev-parse", "--git-path", "index.lock")
    lock_path = Path(index_lock)
    if not lock_path.is_absolute():
        lock_path = repo / lock_path
    if lock_path.exists() or lock_path.is_symlink():
        raise DeploymentError("another Git writer appears active")

    service_lock = manifest.get("service_lock") or {}
    for prop, key in (("User", "user"), ("Group", "group"), ("WorkingDirectory", "working_directory")):
        value = (engine._call(["systemctl", "show", engine.service, f"--property={prop}", "--value"]).stdout or b"").decode().strip()
        if value != str(service_lock.get(key, "")):
            raise DeploymentError(f"service identity mismatch: {prop}")
    preflight.update({"scope_sha256": scope_hash, "release_manifest_sha256": manifest_hash})
    return engine, preflight, scope_hash


def _acquire_deployment_lock(path: Path) -> int:
    path = Path(path)
    if not path.is_absolute():
        raise DeploymentError("deployment lock path must be absolute")
    parent = path.parent.resolve(strict=True)
    if parent != path.parent.absolute():
        raise DeploymentError("deployment lock parent must not traverse symlinks")
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)):
        raise DeploymentError("unsafe deployment lock path")
    fd = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd)
        raise DeploymentError("another deployment writer is active")


def _consume_approval_scope(
    control_root: Path, scope_hash: str, candidate: str,
    *, owner_uid: int | None = None, owner_gid: int | None = None,
) -> Path:
    if (owner_uid is None) != (owner_gid is None):
        raise DeploymentError("approval control owner must include uid and gid")
    control_root = Path(control_root)
    try:
        control_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise DeploymentError("approval control root unavailable") from exc
    if control_root.resolve(strict=True) != control_root.absolute():
        raise DeploymentError("approval control root must not traverse symlinks")
    root_info = control_root.lstat()
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise DeploymentError("private real approval control root required")
    os.chmod(control_root, 0o700)
    if owner_uid is not None and owner_gid is not None:
        if os.geteuid() != 0 and (root_info.st_uid, root_info.st_gid) != (owner_uid, owner_gid):
            raise DeploymentError("approval control owner mismatch")
        os.chown(control_root, owner_uid, owner_gid)
    approvals = control_root / "approval-consumption"
    try:
        approvals.mkdir(exist_ok=True, mode=0o700)
    except OSError as exc:
        raise DeploymentError("private real approvals directory required") from exc
    info = approvals.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DeploymentError("private real approvals directory required")
    os.chmod(approvals, 0o700)
    if owner_uid is not None and owner_gid is not None:
        if os.geteuid() != 0 and (info.st_uid, info.st_gid) != (owner_uid, owner_gid):
            raise DeploymentError("approval consumption owner mismatch")
        os.chown(approvals, owner_uid, owner_gid)
        info = approvals.lstat()
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise DeploymentError("private real approvals directory required")
    path = approvals / f"consumed-{scope_hash}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise DeploymentError("approval scope already consumed") from exc
    try:
        payload = canonical_json_bytes(
            {
                "schema_version": "1.0",
                "scope_sha256": scope_hash,
                "candidate_sha": candidate,
                "consumed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        )
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, 0o600)
        os.fchown(fd, info.st_uid, info.st_gid)
    finally:
        os.close(fd)
    dir_fd = os.open(approvals, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return path


def _service_interpreter_from_cmdline(cmdline: bytes) -> str:
    if not cmdline:
        raise DeploymentError("service process command line unavailable")
    argv0_raw = cmdline.split(b"\0", 1)[0]
    try:
        interpreter = Path(os.fsdecode(argv0_raw)).resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise DeploymentError("service interpreter identity unavailable") from exc
    if not interpreter.is_absolute() or not interpreter.exists():
        raise DeploymentError("service interpreter identity unavailable")
    # Preserve the original venv argv[0] rather than /proc/<pid>/exe. Invoking
    # the resolved system binary would silently lose the service virtualenv.
    return os.fsdecode(argv0_raw)


def _read_service_process_identity(
    service: str, timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    try:
        pid_proc = subprocess.run(
            ["systemctl", "show", service, "--property=MainPID", "--value"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False,
            timeout=max(0.001, timeout_seconds),
        )
    except subprocess.TimeoutExpired as exc:
        raise DeploymentError("service process identity read timed out") from exc
    try:
        pid = int((pid_proc.stdout or b"0").decode("ascii", "strict").strip() or "0")
    except (UnicodeDecodeError, ValueError) as exc:
        raise DeploymentError("service process identity unavailable") from exc
    if pid_proc.returncode or pid <= 1:
        raise DeploymentError("service process identity unavailable")
    proc_root = Path(f"/proc/{pid}")
    cmdline = (proc_root / "cmdline").read_bytes()
    interpreter_argv0 = _service_interpreter_from_cmdline(cmdline)
    return {
        "pid": pid,
        "executable": str((proc_root / "exe").resolve(strict=True)),
        "interpreter": interpreter_argv0,
        "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
    }


def _bounded_identity_read(
    reader: Callable[..., dict[str, Any]], service: str, timeout_seconds: float,
) -> dict[str, Any]:
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            try:
                value = reader(service, timeout_seconds=timeout_seconds)
            except TypeError as exc:
                # Test doubles and compatibility readers may expose the legacy
                # one-argument signature. Do not mask TypeError raised inside
                # the production reader itself.
                if reader is _read_service_process_identity:
                    raise
                value = reader(service)
            results.put((True, value))
        except BaseException as exc:  # normalized by the caller
            results.put((False, exc))

    worker = threading.Thread(target=invoke, daemon=True, name="service-identity-read")
    worker.start()
    try:
        ok, value = results.get(timeout=max(0.001, timeout_seconds))
    except queue.Empty as exc:
        raise DeploymentError("service process identity read timed out") from exc
    if not ok:
        if isinstance(value, (OSError, DeploymentError)):
            raise value
        raise DeploymentError("service process identity unavailable") from value
    if not isinstance(value, dict):
        raise DeploymentError("service process identity unavailable")
    return value


def smoke_environment_for_uid(uid: int) -> dict[str, str]:
    account = pwd.getpwuid(uid)
    home = account.pw_dir
    if not home or not Path(home).is_absolute():
        raise DeploymentError("smoke runner home unavailable")
    env = dict(os.environ)
    env.update({
        "HOME": home,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "XDG_CONFIG_HOME": str(Path(home) / ".config"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    for key in list(env):
        if key == "HERMES_HOME" or key.startswith("SUDO_"):
            env.pop(key, None)
    return env


def wait_for_stable_service_process(
    service: str, *, prior_pid: int, expected_executable: str,
    expected_cmdline_sha256: str, timeout_seconds: float = 30.0,
    sample_seconds: float = 1.0,
    reader: Callable[..., dict[str, Any]] = _read_service_process_identity,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait past systemd launcher/exec transitions before live smoke."""
    deadline = time.monotonic() + timeout_seconds
    previous: dict[str, Any] | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            current = _bounded_identity_read(reader, service, remaining)
        except (OSError, DeploymentError):
            current = {}
        valid = (
            int(current.get("pid", 0)) > 1
            and current.get("pid") != prior_pid
            and current.get("executable") == expected_executable
            and current.get("cmdline_sha256") == expected_cmdline_sha256
        )
        if valid and previous == current:
            return current
        previous = current if valid else None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sleeper(min(sample_seconds, remaining))
    raise DeploymentError("service process did not stabilize before smoke")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", "--release-manifest", dest="manifest", required=True, type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--rollback-approval", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--auto-rollback", action="store_true")
    args = parser.parse_args()
    lock_fd: int | None = None
    try:
        if args.preflight_only == args.apply:
            raise DeploymentError("choose exactly one of --preflight-only or --apply")
        if not args.approval or not args.rollback_approval:
            raise DeploymentError("APP-003 and APP-004 approvals are required")
        manifest = strict_json_load(args.manifest)
        app3 = strict_json_load(args.approval)
        app4 = strict_json_load(args.rollback_approval)
        manifest_parent = args.manifest.parent.resolve(strict=True)
        canonical_root_raw = manifest.get("canonical_artifact_root")
        if not isinstance(canonical_root_raw, str) or not canonical_root_raw:
            raise DeploymentError("canonical artifact root missing from release manifest")
        artifact_root = Path(canonical_root_raw).resolve(strict=True)
        if manifest_parent != artifact_root:
            raise DeploymentError("release manifest is outside its canonical artifact root")
        control_state = manifest.get("control_state") or {}
        lock_path = Path(str(control_state.get("deployment_lock_path", "")))
        consumption_root = Path(str(control_state.get("approval_consumption_root", "")))
        if not lock_path.is_absolute() or not consumption_root.is_absolute():
            raise DeploymentError("canonical global control-state paths missing")
        lock_fd = _acquire_deployment_lock(lock_path)
        artifact_bindings = {
            "human20bot-team-operator.bundle": "bundle_sha256",
            "deploy_human20bot_team_operator.py": "deployment_driver_sha256",
            "release_safety.py": "release_safety_sha256",
            "smoke_human20bot_team_access.py": "smoke_driver_sha256",
            "rollback-manifest.json": "rollback_manifest_sha256",
            "P04-verification.json": "p04_verification_sha256",
            "P05-test-receipt.json": "p05_test_receipt_sha256",
            "P05-static-scan.json": "p05_static_scan_sha256",
            "P05-independent-review.json": "independent_review_sha256",
        }
        manifest_artifacts = manifest.get("artifacts") or {}
        for name, key in artifact_bindings.items():
            if sha256_file(artifact_root / name) != manifest_artifacts.get(key):
                raise DeploymentError(f"sealed artifact hash mismatch: {name}")
        rollback_path = artifact_root / "rollback-manifest.json"
        if sha256_file(rollback_path) != (manifest.get("artifacts") or {}).get("rollback_manifest_sha256"):
            raise DeploymentError("rollback manifest hash mismatch")
        strict_json_load(rollback_path)
        overlay_root = artifact_root / str((manifest.get("profile_overlay") or {}).get("path", "profile-overlay"))
        if hash_tree(overlay_root, require_private=True)["sha256"] != (manifest.get("profile_overlay") or {}).get("tree_sha256"):
            raise DeploymentError("profile overlay tree hash mismatch")
        engine, preflight, scope_hash = _verify_manifest_preflight(args.manifest, manifest, app3, app4)
        if args.preflight_only:
            os.write(1, canonical_json_bytes(preflight))
            os.close(lock_fd)
            lock_fd = None
            return 0
        if not args.auto_rollback:
            raise DeploymentError("--apply requires --auto-rollback")
        overlay = yaml.safe_load(read_nofollow(overlay_root / "config.overlay.yaml"))
        if not isinstance(overlay, dict):
            raise DeploymentError("profile overlay must be a mapping")
        smoke_script = artifact_root / "smoke_human20bot_team_access.py"
        activation_smoke = artifact_root / "P06-final-audit.json"
        baseline_process = _read_service_process_identity(str(manifest.get("service", "")))
        activation_process: dict[str, Any] | None = None

        def smoke() -> dict[str, Any]:
            nonlocal activation_process
            if not smoke_script.is_file() or smoke_script.is_symlink():
                raise DeploymentError("sealed activation smoke script missing")
            activation_process = wait_for_stable_service_process(
                str(manifest.get("service", "")),
                prior_pid=int(baseline_process["pid"]),
                expected_executable=str(baseline_process["executable"]),
                expected_cmdline_sha256=str(baseline_process["cmdline_sha256"]),
            )
            owner = engine.repo.stat()
            proc = subprocess.run(
                [
                    str(baseline_process["interpreter"]),
                    str(smoke_script), "--manifest", str(args.manifest), "--live",
                    "--activation-check", "--send-canaries",
                    "--redacted-json-out", str(activation_smoke),
                ],
                cwd=engine.repo,
                env=smoke_environment_for_uid(owner.st_uid),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
                user=owner.st_uid if os.geteuid() == 0 else None,
                group=owner.st_gid if os.geteuid() == 0 else None,
            )
            if proc.returncode:
                detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
                reason = detail[-1][:240] if detail else "no diagnostic"
                raise DeploymentError(f"activation smoke failed: {reason}")
            return strict_json_load(activation_smoke)

        def rollback_health() -> dict[str, Any]:
            rollback_audit = artifact_root / "P06-rollback-health.json"
            if activation_process is None:
                raise DeploymentError("activation process identity missing before rollback")
            wait_for_stable_service_process(
                str(manifest.get("service", "")),
                prior_pid=int(activation_process["pid"]),
                expected_executable=str(baseline_process["executable"]),
                expected_cmdline_sha256=str(baseline_process["cmdline_sha256"]),
            )
            owner = engine.repo.stat()
            proc = subprocess.run(
                [
                    str(baseline_process["interpreter"]),
                    str(smoke_script), "--manifest", str(args.manifest), "--live",
                    "--rollback-check", "--redacted-json-out", str(rollback_audit),
                    "--prior-process-pid", str(activation_process["pid"]),
                    "--expected-process-executable", str(baseline_process["executable"]),
                    "--expected-process-cmdline-sha256", str(baseline_process["cmdline_sha256"]),
                ],
                cwd=engine.repo,
                env=smoke_environment_for_uid(owner.st_uid),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
                user=owner.st_uid if os.geteuid() == 0 else None,
                group=owner.st_gid if os.geteuid() == 0 else None,
            )
            if proc.returncode:
                detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
                reason = detail[-1][:240] if detail else "no diagnostic"
                raise DeploymentError(f"rollback smoke failed: {reason}")
            return strict_json_load(rollback_audit)

        control_owner = engine.repo.stat()
        _consume_approval_scope(
            consumption_root, scope_hash, str(manifest.get("candidate_sha", "")),
            owner_uid=control_owner.st_uid, owner_gid=control_owner.st_gid,
        )
        try:
            result = engine.apply(
                str(manifest.get("baseline_sha")), str(manifest.get("candidate_sha")),
                overlay, smoke=smoke, auto_rollback=True,
                rollback_health=rollback_health,
            )
            record = {
                "schema_version": "1.0", "status": "applied",
                "scope_sha256": scope_hash,
                "candidate_sha": manifest.get("candidate_sha"),
                "smoke": result.get("smoke"),
                "smoke_audit_sha256": sha256_file(activation_smoke),
            }
            _write_owned_json(artifact_root / "P06-activation-or-rollback.json", record)
            os.write(1, canonical_json_bytes(record))
            os.close(lock_fd)
            lock_fd = None
            return 0
        except DeploymentError as exc:
            rollback_status = "rollback-failed" if "rollback incomplete" in str(exc) else "rolled-back"
            record = {"schema_version": "1.0", "status": rollback_status, "scope_sha256": scope_hash, "candidate_sha": manifest.get("candidate_sha"), "error_class": type(exc).__name__}
            _write_owned_json(artifact_root / "P06-activation-or-rollback.json", record)
            raise
    except (SafetyError, OSError, yaml.YAMLError) as exc:
        if lock_fd is not None:
            os.close(lock_fd)
        os.write(2, f"ERROR: {exc}\n".encode())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
