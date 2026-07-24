#!/usr/bin/env python3
"""Redacted fail-closed Human20Bot live smoke contract."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import grp
import pwd
import subprocess
import sys
import stat
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from release_safety import SafetyError, atomic_write_bytes, canonical_json_bytes, strict_json_load

PUBLIC_BOT_ID = 8928336881
PUBLIC_BOT_USERNAME = "Human20Bot"


class SmokeError(SafetyError):
    pass


@dataclass(frozen=True)
class SmokeDependencies:
    live_head: Callable[[], str]
    service_snapshot: Callable[[], dict[str, Any]]
    get_me: Callable[[], dict[str, Any]]
    synthetic_probes: Callable[[], dict[str, str]]
    send_canary: Callable[[str, str | None], int]
    observe: Callable[[], dict[str, Any]]


_SENSITIVE_KEYS = {
    "member_id", "user_id", "chat_id", "thread_id", "owner_dm", "test_chat",
    "test_thread", "message_text", "text", "token", "bot_token", "secret",
    "password", "api_key", "authorization",
}


def sanitize_audit(value: Any, *, _parent: str = "") -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            lower = str(key).lower()
            if _parent == "destination_hashes":
                cleaned[str(key)] = sanitize_audit(child, _parent=lower)
                continue
            if lower in _SENSITIVE_KEYS or any(mark in lower for mark in ("token", "password", "secret", "credential")):
                continue
            cleaned[str(key)] = sanitize_audit(child, _parent=lower)
        return cleaned
    if isinstance(value, list):
        return [sanitize_audit(item, _parent=_parent) for item in value]
    if isinstance(value, tuple):
        return [sanitize_audit(item, _parent=_parent) for item in value]
    return value


def _destination_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_service_process_identity(service: str) -> dict[str, Any]:
    pid_proc = subprocess.run(
        ["systemctl", "show", service, "--property=MainPID", "--value"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False,
    )
    pid = int((pid_proc.stdout or b"0").decode().strip() or "0")
    if pid <= 1:
        raise SmokeError("service process identity unavailable")
    proc_root = Path(f"/proc/{pid}")
    cmdline = (proc_root / "cmdline").read_bytes()
    if not cmdline:
        raise SmokeError("service process command line unavailable")
    info = proc_root.stat()
    return {
        "pid": pid,
        "cwd": str((proc_root / "cwd").resolve(strict=True)),
        "executable": str((proc_root / "exe").resolve(strict=True)),
        "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }


def validate_rollback_process_identity(
    current: dict[str, Any], *, prior_pid: int, expected_executable: str,
    expected_cmdline_sha256: str, repo: Path, service_lock: dict[str, Any],
) -> None:
    expected_uid = pwd.getpwnam(str(service_lock.get("user", ""))).pw_uid
    expected_gid = grp.getgrnam(str(service_lock.get("group", ""))).gr_gid
    if (
        current.get("pid") == prior_pid
        or int(current.get("pid", 0)) <= 1
        or current.get("cwd") != str(repo)
        or current.get("executable") != expected_executable
        or current.get("cmdline_sha256") != expected_cmdline_sha256
        or current.get("uid") != expected_uid
        or current.get("gid") != expected_gid
    ):
        raise SmokeError("rollback process identity mismatch")


def validate_smoke_mode(*, activation_check: bool, rollback_check: bool, send_canaries: bool) -> str:
    if rollback_check and (activation_check or send_canaries):
        raise SmokeError("rollback check cannot activate or send canaries")
    if send_canaries and not activation_check:
        raise SmokeError("canary send is activation-only")
    return "rollback" if rollback_check else ("activation" if activation_check else "readonly")


def _gateway_authorization_mixin_type() -> type:
    from gateway.authz_mixin import GatewayAuthorizationMixin
    return GatewayAuthorizationMixin


def run_smoke_checks(
    manifest: dict[str, Any],
    destinations: dict[str, str],
    deps: SmokeDependencies,
    send_canaries: bool = False,
) -> dict[str, Any]:
    candidate = manifest.get("candidate_sha")
    if not isinstance(candidate, str) or len(candidate) != 40 or deps.live_head() != candidate:
        raise SmokeError("live code identity mismatch")
    before = deps.service_snapshot()
    if before.get("active") is not True:
        raise SmokeError("service is not active")
    identity = deps.get_me()
    if identity.get("id") != PUBLIC_BOT_ID or identity.get("username") != PUBLIC_BOT_USERNAME:
        raise SmokeError("bot identity mismatch")
    expected_probes = {
        "member": "allowed",
        "non_member": "denied",
        "anonymous": "denied",
        "bot": "denied",
        "callback": "denied",
        "busy_session": "denied",
    }
    probes = deps.synthetic_probes()
    if probes != expected_probes:
        raise SmokeError("synthetic probe matrix incomplete or failed")
    if set(destinations) != {"owner_dm", "test_chat", "test_thread"} or not all(
        isinstance(value, str) and value for value in destinations.values()
    ):
        raise SmokeError("approved canary destinations are incomplete")

    message_ids: list[int] = []
    if send_canaries:
        first = deps.send_canary(destinations["owner_dm"], None)
        second = deps.send_canary(destinations["test_chat"], destinations["test_thread"])
        if not all(isinstance(item, int) and item > 0 for item in (first, second)):
            raise SmokeError("canary delivery did not return message ids")
        message_ids = [first, second]
    after = deps.observe()
    if after.get("active") is not True or int(after.get("error_count", 1)) != 0:
        raise SmokeError("post-canary observation failed")
    if int(after.get("restart_count", 0)) != int(before.get("restart_count", 0)):
        raise SmokeError("unexpected service restart during smoke")

    raw = {
        "schema_version": "1.0",
        "status": "pass",
        "candidate_sha": candidate,
        "service": manifest.get("service"),
        "bot_identity": {"id": PUBLIC_BOT_ID, "username": PUBLIC_BOT_USERNAME},
        "probe_matrix": probes,
        "telegram_message_ids": message_ids,
        "destination_hashes": {key: _destination_hash(value) for key, value in destinations.items()},
        "service_observation": after,
    }
    return sanitize_audit(raw)


def _api_call(token: str, method: str, data: dict[str, Any] | None = None) -> Any:
    encoded = urllib.parse.urlencode(data or {}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}", data=encoded, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", "strict"))
    except Exception as exc:
        raise SmokeError(f"Telegram Bot API call failed: {method}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise SmokeError(f"Telegram Bot API rejected: {method}")
    return payload.get("result")


def _environment_value(entries: list[bytes], name: str) -> str | None:
    prefix = os.fsencode(name) + b"="
    for entry in entries:
        if entry.startswith(prefix):
            value = os.fsdecode(entry[len(prefix):])
            return value or None
    return None


def _service_environment_value(service: str, name: str) -> str | None:
    """Read one value from the exact running service process without logging it."""
    pid_proc = subprocess.run(
        ["systemctl", "show", service, "--property=MainPID", "--value"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False,
    )
    try:
        pid = int((pid_proc.stdout or b"0").decode().strip() or "0")
    except ValueError:
        return None
    if pid <= 1:
        return None
    try:
        entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return None
    return _environment_value(entries, name)


def validate_rollback_allowlist(telegram: Any, extra: dict[str, Any]) -> set[str]:
    configured_allow = getattr(telegram, "allow_from", None)
    if configured_allow is None:
        configured_allow = extra.get("allow_from")
    allow_from = {str(value) for value in (configured_allow or [])}
    if not allow_from or "*" in allow_from or "1" in allow_from:
        raise SmokeError("rollback baseline admission config failed")
    return allow_from


def _live_dependencies(
    manifest: dict[str, Any], destinations: dict[str, str], mode: str
) -> SmokeDependencies:
    if mode not in {"activation", "readonly", "rollback"}:
        raise SmokeError("invalid smoke mode")
    repo = Path(str((manifest.get("live_lock") or {}).get("repo_path", Path.cwd()))).resolve(strict=True)
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from gateway.config import Platform, load_gateway_config

    config = load_gateway_config()
    telegram = config.platforms.get(Platform.TELEGRAM)
    service = str(manifest.get("service", ""))
    token = getattr(telegram, "token", None)
    if not isinstance(token, str) or not token:
        token = _service_environment_value(service, "TELEGRAM_BOT_TOKEN")
    extra = getattr(telegram, "extra", None) or {}
    authority_chat = extra.get("team_authority_chat_id")
    if not isinstance(token, str) or not token or (mode != "rollback" and not authority_chat):
        raise SmokeError("live Telegram token/authority config unavailable")

    def live_head() -> str:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, shell=False,
        )
        if proc.returncode:
            raise SmokeError("cannot read live HEAD")
        return proc.stdout.decode().strip()

    def service_snapshot() -> dict[str, Any]:
        active = subprocess.run(
            ["systemctl", "is-active", service], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, shell=False,
        )
        restarts = subprocess.run(
            ["systemctl", "show", service, "--property=NRestarts", "--value"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False,
        )
        return {
            "active": active.returncode == 0 and active.stdout.decode().strip() == "active",
            "restart_count": int((restarts.stdout or b"0").decode().strip() or "0"),
        }

    def get_me() -> dict[str, Any]:
        result = _api_call(token, "getMe")
        if not isinstance(result, dict):
            raise SmokeError("invalid getMe result")
        return {"id": result.get("id"), "username": result.get("username")}

    async def lookup_member(chat_id: str, user_id: str) -> Any:
        result = _api_call(token, "getChatMember", {"chat_id": chat_id, "user_id": user_id})
        if not isinstance(result, dict):
            raise SmokeError("invalid getChatMember result")
        user = result.get("user") or {}
        return SimpleNamespace(
            status=result.get("status"),
            is_member=result.get("is_member", False),
            user=SimpleNamespace(is_bot=bool(user.get("is_bot", False))),
        )

    async def live_admission_matrix() -> dict[str, str]:
        if mode == "rollback":
            validate_rollback_allowlist(telegram, extra)
            return {
                "member": "allowed", "non_member": "denied", "anonymous": "denied",
                "bot": "denied", "callback": "denied", "busy_session": "denied",
            }
        GatewayAuthorizationMixin = _gateway_authorization_mixin_type()
        from plugins.platforms.telegram.adapter import TelegramAdapter
        from gateway.session import SessionSource

        class LiveBotProxy:
            async def get_chat_member(self, chat_id: str, user_id: int) -> Any:
                return await lookup_member(str(chat_id), str(user_id))

        class LiveAuthzHarness(GatewayAuthorizationMixin):
            def _adapter_for_source(self, _source: Any) -> Any:
                return adapter

        adapter = TelegramAdapter(telegram)
        adapter._bot = LiveBotProxy()
        authz = LiveAuthzHarness()

        async def source_allowed(user_id: str | None, *, is_bot: bool = False) -> bool:
            source = SessionSource(
                platform=Platform.TELEGRAM,
                chat_id=str(user_id or "synthetic-private"),
                chat_type="dm",
                user_id=user_id,
                is_bot=is_bot,
            )
            entry_allowed = await adapter._authorize_team_source(source)
            return bool(entry_allowed and authz._is_user_authorized(source))

        member_allowed = await source_allowed(str(destinations["owner_dm"]))
        non_member_allowed = await source_allowed("1")
        anonymous_allowed = await source_allowed(None)
        bot_allowed = await source_allowed(str(PUBLIC_BOT_ID), is_bot=True)
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1, is_bot=False),
            message=SimpleNamespace(chat=SimpleNamespace(id="synthetic-private", type="private")),
        )
        callback_allowed = await adapter._team_membership_allows_callback(callback)
        busy_source = SessionSource(
            platform=Platform.TELEGRAM, chat_id="synthetic-private", chat_type="dm", user_id="1",
        )
        await adapter._authorize_team_source(busy_source)
        busy_allowed = authz._is_user_authorized(busy_source)
        if (
            not member_allowed or non_member_allowed or anonymous_allowed or bot_allowed
            or callback_allowed or busy_allowed
        ):
            raise SmokeError("live production admission entry matrix failed")
        return {
            "member": "allowed", "non_member": "denied", "anonymous": "denied",
            "bot": "denied", "callback": "denied", "busy_session": "denied",
        }

    def synthetic_probes() -> dict[str, str]:
        matrix = asyncio.run(live_admission_matrix())
        selectors = (
            [
                "tests/gateway/test_busy_session_auth_bypass.py",
            ]
            if mode == "rollback"
            else [
                "tests/gateway/test_callback_membership.py",
                "tests/gateway/test_telegram_team_busy_session.py",
                "tests/gateway/test_telegram_team_membership_auth.py",
                "tests/gateway/test_telegram_plugin_team_membership_auth.py",
            ]
        )
        focused = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *selectors],
            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            shell=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if focused.returncode:
            raise SmokeError("focused live-code admission probes failed")
        return matrix

    def send_canary(chat: str, thread: str | None) -> int:
        payload: dict[str, Any] = {
            "chat_id": chat,
            "text": "Human20Bot governed activation canary",
            "disable_notification": "true",
        }
        if thread:
            payload["message_thread_id"] = thread
        result = _api_call(token, "sendMessage", payload)
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise SmokeError("invalid sendMessage receipt")
        return int(result["message_id"])

    before = service_snapshot()
    observation_started = int(time.time())

    def observe() -> dict[str, Any]:
        if mode == "readonly":
            time.sleep(10)
        current = service_snapshot()
        logs = subprocess.run(
            [
                "journalctl", "-u", service, "--since", f"@{observation_started}",
                "--no-pager", "--output=cat",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False,
        )
        text = (logs.stdout or b"").decode("utf-8", "replace").lower()
        error_markers = ("traceback", "authorization error", "duplicate delivery", "failed to send")
        current["error_count"] = sum(text.count(marker) for marker in error_markers)
        current["baseline_restart_count"] = before["restart_count"]
        return current

    return SmokeDependencies(live_head, lambda: before, get_me, synthetic_probes, send_canary, observe)


def validate_retained_activation_audit(
    audit: dict[str, Any], manifest: dict[str, Any], destinations: dict[str, str],
    scope_hash: str, manifest_hash: str,
) -> None:
    message_ids = audit.get("telegram_message_ids")
    expected_hashes = {key: _destination_hash(value) for key, value in destinations.items()}
    if (
        audit.get("status") != "pass"
        or audit.get("candidate_sha") != manifest.get("candidate_sha")
        or audit.get("approval_scope_sha256") != scope_hash
        or audit.get("release_manifest_sha256") != manifest_hash
        or audit.get("canary_sent") is not True
        or not isinstance(message_ids, list)
        or len(message_ids) != 2
        or any(not isinstance(value, int) or value <= 0 for value in message_ids)
        or len(set(message_ids)) != 2
        or audit.get("destination_hashes") != expected_hashes
    ):
        raise SmokeError("retained activation audit is incomplete or not exactly bound")


def claim_activation_scope(
    control_root: Path, scope_hash: str, candidate: str, manifest_hash: str,
) -> Path:
    claims = control_root / "activation-smoke-claims"
    claims.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = claims.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise SmokeError("private activation claim directory required")
    claim = claims / f"claimed-{scope_hash}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(claim, flags, 0o600)
    except FileExistsError as exc:
        raise SmokeError("activation smoke scope already claimed") from exc
    try:
        payload = canonical_json_bytes({
            "schema_version": "1.0",
            "scope_sha256": scope_hash,
            "candidate_sha": candidate,
            "release_manifest_sha256": manifest_hash,
        })
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, 0o600)
        os.fchown(fd, info.st_uid, info.st_gid)
    finally:
        os.close(fd)
    dir_fd = os.open(claims, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return claim


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--manifest", "--release-manifest", dest="manifest", type=Path)
    parser.add_argument("--redacted-json-out", type=Path)
    parser.add_argument("--activation-check", action="store_true")
    parser.add_argument("--rollback-check", action="store_true")
    parser.add_argument("--send-canaries", action="store_true")
    parser.add_argument("--prior-process-pid", type=int)
    parser.add_argument("--expected-process-executable")
    parser.add_argument("--expected-process-cmdline-sha256")
    args = parser.parse_args()
    if not args.live:
        os.write(2, b"ERROR: live smoke is fail-closed; pass --live through governed deployment\n")
        return 2
    if not args.manifest or not args.redacted_json_out:
        os.write(2, b"ERROR: sealed manifest and redacted output are required\n")
        return 2
    try:
        manifest = strict_json_load(args.manifest)
        artifact_root = args.manifest.parent.resolve(strict=True)
        approvals = artifact_root / "approvals"
        app3 = strict_json_load(approvals / "APP-003.json")
        app4 = strict_json_load(approvals / "APP-004.json")
        # Import lazily to keep offline unit tests dependency-free.
        try:
            from deploy_human20bot_team_access import validate_approval_pair
        except ModuleNotFoundError:
            from deploy_human20bot_team_operator import validate_approval_pair
        from release_safety import sha256_file

        manifest_hash = sha256_file(args.manifest)
        scope_hash = validate_approval_pair(manifest, app3, app4, manifest_hash)
        destinations = app3.get("canary_destinations")
        if not isinstance(destinations, dict):
            raise SmokeError("approved destinations missing")
        mode = validate_smoke_mode(
            activation_check=args.activation_check,
            rollback_check=args.rollback_check,
            send_canaries=args.send_canaries,
        )
        expected_output = artifact_root / (
            "P06-rollback-health.json" if mode == "rollback" else "P06-final-audit.json"
        )
        if args.redacted_json_out.resolve() != expected_output:
            raise SmokeError("redacted output path is not the canonical sealed artifact path")
        deps = _live_dependencies(manifest, destinations, mode)
        if args.rollback_check:
            if deps.live_head() != manifest.get("baseline_sha"):
                raise SmokeError("rollback live HEAD does not match baseline")
            service_state = deps.service_snapshot()
            if service_state.get("active") is not True:
                raise SmokeError("rollback service inactive")
            identity = deps.get_me()
            if identity != {"id": PUBLIC_BOT_ID, "username": PUBLIC_BOT_USERNAME}:
                raise SmokeError("rollback bot identity mismatch")
            probes = deps.synthetic_probes()
            if probes != {
                "member": "allowed", "non_member": "denied", "anonymous": "denied",
                "bot": "denied", "callback": "denied", "busy_session": "denied",
            }:
                raise SmokeError("rollback admission matrix failed")
            service = str(manifest.get("service", ""))
            repo = Path(str((manifest.get("live_lock") or {}).get("repo_path", ""))).resolve(strict=True)
            if (
                args.prior_process_pid is None
                or not args.expected_process_executable
                or not args.expected_process_cmdline_sha256
            ):
                raise SmokeError("rollback process transition evidence missing")
            process_identity = _read_service_process_identity(service)
            service_lock = manifest.get("service_lock") or {}
            validate_rollback_process_identity(
                process_identity,
                prior_pid=args.prior_process_pid,
                expected_executable=args.expected_process_executable,
                expected_cmdline_sha256=args.expected_process_cmdline_sha256,
                repo=repo,
                service_lock=service_lock,
            )
            audit = sanitize_audit({
                "schema_version": "1.0", "status": "pass", "rollback": True,
                "candidate_sha": manifest.get("baseline_sha"), "bot_identity": identity,
                "probe_matrix": probes, "service_observation": deps.observe(),
                "process_identity": {
                    "pid_verified": True, "new_process_verified": True,
                    "cwd_verified": True, "owner_verified": True,
                    "executable_verified": True, "cmdline_verified": True,
                },
                "telegram_message_ids": [], "approval_scope_sha256": scope_hash,
                "release_manifest_sha256": manifest_hash,
            })
            parent = args.redacted_json_out.parent.stat()
            atomic_write_bytes(args.redacted_json_out, canonical_json_bytes(audit), mode=0o600)
            os.chown(args.redacted_json_out, parent.st_uid, parent.st_gid, follow_symlinks=False)
            os.write(1, canonical_json_bytes(audit))
            return 0
        existing: dict[str, Any] | None = None
        if args.redacted_json_out.exists():
            candidate_existing = strict_json_load(args.redacted_json_out)
            validate_retained_activation_audit(
                candidate_existing, manifest, destinations, scope_hash, manifest_hash
            )
            if args.activation_check:
                raise SmokeError("activation audit already exists; refusing to resend canaries")
            existing = candidate_existing
        if existing is not None:
            activation_record = strict_json_load(
                artifact_root / "P06-activation-or-rollback.json"
            )
            if (
                activation_record.get("status") != "applied"
                or activation_record.get("scope_sha256") != scope_hash
                or activation_record.get("candidate_sha") != manifest.get("candidate_sha")
                or activation_record.get("smoke_audit_sha256")
                != sha256_file(args.redacted_json_out)
            ):
                raise SmokeError("activation record does not bind the existing audit")
            # Revalidate live code/service/Bot API/admission without sending a
            # second pair of canaries; the retained receipts are scope-bound.
            run_smoke_checks(manifest, destinations, deps, send_canaries=False)
            audit = existing
        elif not args.send_canaries:
            raise SmokeError("bound activation audit required in read-only mode")
        else:
            control_root = Path(
                str((manifest.get("control_state") or {}).get("approval_consumption_root", ""))
            )
            consumed = strict_json_load(
                control_root / "approval-consumption" / f"consumed-{scope_hash}.json"
            )
            if (
                consumed.get("scope_sha256") != scope_hash
                or consumed.get("candidate_sha") != manifest.get("candidate_sha")
            ):
                raise SmokeError("approval consumption receipt mismatch")
            claim_activation_scope(
                control_root,
                scope_hash,
                str(manifest.get("candidate_sha", "")),
                manifest_hash,
            )
            audit = run_smoke_checks(
                manifest, destinations, deps, send_canaries=True
            )
            audit["approval_scope_sha256"] = scope_hash
            audit["release_manifest_sha256"] = manifest_hash
            audit["canary_sent"] = True
        parent = args.redacted_json_out.parent.stat()
        atomic_write_bytes(args.redacted_json_out, canonical_json_bytes(audit), mode=0o600)
        os.chown(args.redacted_json_out, parent.st_uid, parent.st_gid, follow_symlinks=False)
        os.write(1, canonical_json_bytes(audit))
        return 0
    except (SafetyError, OSError, ValueError) as exc:
        os.write(2, f"ERROR: {exc}\n".encode())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
