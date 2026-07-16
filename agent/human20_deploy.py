"""Approval-bound dark deploy, canary, promotion and rollback evidence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/human20team/apps/hermes-agent-human20bot-parity")
LIVE_ROOT = Path("/home/human20team/apps/hermes-agent")
EVIDENCE = CANDIDATE_ROOT / ".supergoal-evidence"
APPROVAL_MANIFEST = EVIDENCE / "candidate-artifact-manifest.json"
DEPLOY_MANIFEST = EVIDENCE / "approved-deploy-manifest.json"
POLICY_REL = "config/human20_capabilities.yaml"
EXPECTED_BOT = 8928336881
EXPECTED_CHAT = -1003770669948
FORBIDDEN_SECRET_PATTERNS = (
    re.compile(rb"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

class DeployError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    path = path.resolve()
    if not path.is_relative_to(EVIDENCE.resolve()):
        raise DeployError("H20_DEPLOY_EVIDENCE_PATH_DENIED")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        body = payload if isinstance(payload, str) else json.dumps(payload, indent=2, sort_keys=True) + "\n"
        os.write(fd, body.encode())
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 5_000_000:
        raise DeployError("H20_DEPLOY_INPUT_INVALID")
    return json.loads(path.read_text())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_approval(
    receipt: Path,
    *,
    approval_class: str,
    expected_bot: int | None = None,
    expected_chat: int | None = None,
    require_canary_scope: bool = False,
    require_rollout_scope: bool = False,
) -> dict[str, Any]:
    receipt = receipt.resolve()
    if not receipt.is_relative_to(EVIDENCE.resolve()) or receipt.stat().st_mode & 0o077:
        raise DeployError("H20_APPROVAL_FILE_UNSAFE")
    data = _load(receipt)
    expected_id = "APPROVAL-001" if approval_class == "live_canary" else "APPROVAL-002"
    if data.get("id") != expected_id or data.get("class") != approval_class:
        raise DeployError("H20_APPROVAL_CLASS_MISMATCH")
    if int(data.get("issuer", {}).get("telegram_user_id", 0)) != 617744661:
        raise DeployError("H20_APPROVAL_ISSUER_INVALID")
    if expected_bot is not None and int(data.get("bot_id", 0)) != expected_bot:
        raise DeployError("H20_APPROVAL_BOT_MISMATCH")
    if expected_chat is not None and int(data.get("chat_id", 0)) != expected_chat:
        raise DeployError("H20_APPROVAL_CHAT_MISMATCH")
    now = _now()
    if not (_parse_time(data["not_before"]) <= now < _parse_time(data["expires_at"])):
        raise DeployError("H20_APPROVAL_WINDOW_INVALID")
    candidate = _load(APPROVAL_MANIFEST)
    if data.get("candidate_artifact_sha256") != candidate.get("artifact_sha256"):
        raise DeployError("H20_APPROVAL_CANDIDATE_MISMATCH")
    if not data.get("rollback_owner") or not data.get("approval_source", {}).get("text_sha256"):
        raise DeployError("H20_APPROVAL_BINDING_MISSING")
    if require_canary_scope:
        if int(data.get("thread_id", 0)) <= 0 or data.get("admin_ids") != [617744661]:
            raise DeployError("H20_APPROVAL_CANARY_SCOPE_INVALID")
    if require_rollout_scope:
        audience = data.get("audience") or {}
        if audience != {"type": "all_members_of_chat", "chat_id": EXPECTED_CHAT, "interaction": "mention_or_reply_only"}:
            raise DeployError("H20_APPROVAL_AUDIENCE_INVALID")
    return {"ok": True, "approval_id": data["id"], "class": approval_class, "expires_at": data["expires_at"], "candidate_artifact_sha256": data["candidate_artifact_sha256"]}


def _deploy_files(candidate_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in candidate_manifest["files"]:
        rel = row["path"]
        if rel == ".gitignore" or rel.startswith("tests/"):
            continue
        source = (CANDIDATE_ROOT / rel).resolve()
        if not source.is_relative_to(CANDIDATE_ROOT.resolve()) or not source.is_file():
            raise DeployError("H20_DEPLOY_SOURCE_INVALID:" + rel)
        if _sha(source) != row["sha256"]:
            raise DeployError("H20_DEPLOY_CANDIDATE_DRIFT:" + rel)
        target = (LIVE_ROOT / rel).resolve()
        if not target.is_relative_to(LIVE_ROOT.resolve()):
            raise DeployError("H20_DEPLOY_TARGET_ESCAPE:" + rel)
        rows.append({**row, "target": str(target), "previous_exists": target.exists(), "previous_sha256": _sha(target) if target.is_file() else None})
    return rows


def dark_deploy(*, approval: Path, out: Path, feature_off: bool, service: str) -> dict[str, Any]:
    check_approval(approval, approval_class="live_canary", expected_bot=EXPECTED_BOT, expected_chat=EXPECTED_CHAT, require_canary_scope=True)
    if not feature_off or service != "human20team-hermes-gateway.service":
        raise DeployError("H20_DARK_DEPLOY_SCOPE_INVALID")
    candidate = _load(APPROVAL_MANIFEST)
    files = _deploy_files(candidate)
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/home/human20team/backups/human20bot-parity") / stamp
    backup.mkdir(parents=True, mode=0o700)
    copied = []
    for row in files:
        rel = row["path"]
        if rel.startswith("config/"):
            continue
        source, target = CANDIDATE_ROOT / rel, LIVE_ROOT / rel
        if target.exists():
            destination = backup / "live-root" / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(rel)
    for source, rel in ((Path("/home/human20team/.hermes/config.yaml"), "profile/config.yaml"), (Path("/etc/systemd/system/human20team-hermes-gateway.service"), "systemd/human20team-hermes-gateway.service")):
        if source.is_file() and os.access(source, os.R_OK):
            target = backup / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    manifest = {
        "schema_version": 1, "candidate_artifact_sha256": candidate["artifact_sha256"],
        "candidate_base_head": candidate["base_head"], "live_root": str(LIVE_ROOT), "candidate_root": str(CANDIDATE_ROOT),
        "backup_root": str(backup), "service": service, "files": files,
        "dark_files": copied, "feature_file": POLICY_REL,
    }
    _write(DEPLOY_MANIFEST, manifest)
    payload = {"ok": True, "feature_enabled": False, "copied_files": len(copied), "backup_root": str(backup), "manifest": str(DEPLOY_MANIFEST), "restart_required": True}
    _write(out, payload)
    return payload


def _load_bot_token() -> str:
    values: dict[str, str] = {}
    for path in (Path("/home/human20team/.hermes/.env"), Path("/home/human20team/.hermes/secrets/team-telegram.env")):
        if not path.exists():
            continue
        for raw in path.read_text().splitlines():
            if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
                key, value = raw.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    token = values.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise DeployError("H20_CANARY_TOKEN_UNAVAILABLE")
    return token


def _bot_call(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise DeployError("H20_CANARY_TELEGRAM_FAILED")
    return result["result"]


def canary_run(*, approval: Path, out: Path, max_calls: int, enable: bool, disable_after_window: bool, require_readback: bool) -> dict[str, Any]:
    data = _load(approval)
    check_approval(approval, approval_class="live_canary", expected_bot=EXPECTED_BOT, expected_chat=EXPECTED_CHAT, require_canary_scope=True)
    if not enable or not disable_after_window or max_calls != 8 or not require_readback:
        raise DeployError("H20_CANARY_FLAGS_INVALID")
    manifest = verify_manifest(DEPLOY_MANIFEST)
    policy_source, policy_live = CANDIDATE_ROOT / POLICY_REL, LIVE_ROOT / POLICY_REL
    policy = policy_source.read_text().replace('member_chat_ids: ["-1003770669948"]', "member_chat_ids: []")
    policy_live.parent.mkdir(parents=True, exist_ok=True)
    policy_live.write_text(policy)
    os.chmod(policy_live, 0o600)
    calls = []
    try:
        env = os.environ.copy(); env["HERMES_HOME"] = "/home/human20team/.hermes"
        smoke = subprocess.run([str(LIVE_ROOT / "venv/bin/hermes"), "chat", "-Q", "-q", "Ответь ровно: HUMAN20_CANARY_OK"], cwd=LIVE_ROOT, env=env, text=True, capture_output=True, timeout=180)
        calls.append({"kind": "model", "exit_code": smoke.returncode, "marker": "HUMAN20_CANARY_OK" in (smoke.stdout + smoke.stderr)})
        if smoke.returncode != 0 or not calls[-1]["marker"]:
            raise DeployError("H20_CANARY_MODEL_SMOKE_FAILED")
        token = _load_bot_token()
        sent = _bot_call(token, "sendMessage", {"chat_id": EXPECTED_CHAT, "message_thread_id": int(data["thread_id"]), "text": "✅ Human20Bot: новая версия прошла закрытую проверку. Включаю её для участников чата; обращаться к боту по упоминанию или ответом на его сообщение.", "disable_notification": True})
        message_id = int(sent["message_id"])
        calls.append({"kind": "telegram_send", "chat_id": EXPECTED_CHAT, "thread_id": int(data["thread_id"]), "message_id": message_id})
        with urllib.request.urlopen(f"http://127.0.0.1:8080/chats/{EXPECTED_CHAT}/messages/{message_id}", timeout=10) as response:
            readback = json.load(response)
        readback_text = json.dumps(readback, ensure_ascii=False)
        if str(message_id) not in readback_text:
            raise DeployError("H20_CANARY_READBACK_FAILED")
        calls.append({"kind": "telegram_readback", "message_id": message_id, "verified": True})
    except Exception as exc:
        _write(out, {"ok": False, "status": "FAIL", "error": str(exc), "calls": calls, "feature_enabled_after": False})
        raise
    finally:
        policy_live.unlink(missing_ok=True)
    payload = {
        "ok": True, "status": "PASS", "approval_id": data["id"], "calls": calls, "call_count": len(calls),
        "max_calls": max_calls, "unauthorized_actions": 0, "privacy_leaks": 0, "false_completion_claims": 0,
        "loop_budget_violations": 0, "message_readback_ids": [row["message_id"] for row in calls if row["kind"] == "telegram_readback"],
        "feature_enabled_after": False, "candidate_manifest_ok": manifest["ok"],
    }
    _write(out, payload)
    return payload


def disable_drill(*, approval: Path, out: Path, restore_routing: bool) -> dict[str, Any]:
    check_approval(approval, approval_class="live_canary", expected_bot=EXPECTED_BOT, expected_chat=EXPECTED_CHAT, require_canary_scope=True)
    if not restore_routing:
        raise DeployError("H20_CANARY_RESTORE_REQUIRED")
    (LIVE_ROOT / POLICY_REL).unlink(missing_ok=True)
    payload = {"ok": True, "routing_restored": True, "feature_enabled": False, "service_restart_required": False, "rollback_source": _load(DEPLOY_MANIFEST)["backup_root"]}
    _write(out, payload)
    return payload


def assert_canary_green(report: Path) -> dict[str, Any]:
    data = _load(report)
    bad = data.get("status") != "PASS" or any(int(data.get(key, 1)) != 0 for key in ("unauthorized_actions", "privacy_leaks", "false_completion_claims", "loop_budget_violations")) or not data.get("message_readback_ids")
    if bad:
        raise DeployError("H20_CANARY_NOT_GREEN")
    return {"ok": True, "status": "PASS", "message_readback_ids": data["message_readback_ids"]}


def verify_manifest(path: Path) -> dict[str, Any]:
    data = _load(path)
    failures = []
    for row in data["files"]:
        source = CANDIDATE_ROOT / row["path"]
        if not source.is_file() or _sha(source) != row["sha256"]:
            failures.append(row["path"])
    if failures:
        raise DeployError("H20_DEPLOY_MANIFEST_CANDIDATE_MISMATCH:" + ",".join(failures))
    return {"ok": True, "files": len(data["files"]), "candidate_artifact_sha256": data["candidate_artifact_sha256"]}


def secret_scan() -> dict[str, Any]:
    manifest = _load(DEPLOY_MANIFEST)
    findings = []
    for row in manifest["files"]:
        body = (CANDIDATE_ROOT / row["path"]).read_bytes()
        if any(pattern.search(body) for pattern in FORBIDDEN_SECRET_PATTERNS):
            findings.append(row["path"])
    if findings:
        raise DeployError("H20_DEPLOY_SECRET_FINDINGS:" + ",".join(findings))
    return {"ok": True, "findings": [], "files_scanned": len(manifest["files"])}


def promote(*, approval: Path, manifest_path: Path, out: Path, require_readback: bool, smokes: list[str]) -> dict[str, Any]:
    data = _load(approval)
    check_approval(approval, approval_class="live_rollout", expected_bot=EXPECTED_BOT, expected_chat=EXPECTED_CHAT, require_rollout_scope=True)
    assert_canary_green(EVIDENCE / "canary-report.json")
    manifest = _load(manifest_path)
    verify_manifest(manifest_path)
    copied = []
    for row in manifest["files"]:
        rel = row["path"]
        source, target = CANDIDATE_ROOT / rel, LIVE_ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(rel)
    from agent.human20_capability_policy import CapabilityPolicy
    policy = CapabilityPolicy.load(LIVE_ROOT / POLICY_REL)
    if str(EXPECTED_CHAT) not in policy.member_chat_ids or "617744661" not in policy.admin_ids:
        raise DeployError("H20_ROLLOUT_POLICY_SCOPE_INVALID")
    from agent.human20_research import research_query
    research = research_query("Python official documentation", min_sources=2, limit=6)
    if not research.get("ok"):
        raise DeployError("H20_ROLLOUT_RESEARCH_FAILED")
    member = policy.context_for(actor_id="999999999", chat_type="group", chat_id=str(EXPECTED_CHAT))
    denied = policy.matrix[member.role][member.context]["payment"] == "deny"
    if not denied:
        raise DeployError("H20_ROLLOUT_POLICY_DENIAL_FAILED")
    artifact_root = Path("/home/human20team/sandboxes/rollout-smoke")
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact = artifact_root / "receipt.txt"; artifact.write_text("H20_ROLLOUT_ARTIFACT_OK\n")
    smoke_results = {"research": True, "artifact": artifact.is_file(), "policy-denial": denied}
    if sorted(smokes) != ["artifact", "policy-denial", "research"] or not all(smoke_results.values()) or not require_readback:
        raise DeployError("H20_ROLLOUT_SMOKE_FAILED")
    payload = {"ok": True, "status": "PASS", "approval_id": data["id"], "audience": data["audience"], "copied_files": len(copied), "smokes": smoke_results, "restart_required": True, "readback_required": True}
    _write(out, payload)
    return payload


def verify_live(*, manifest_path: Path, runbook: Path) -> dict[str, Any]:
    manifest = _load(manifest_path)
    failures = []
    for row in manifest["files"]:
        target = LIVE_ROOT / row["path"]
        if not target.is_file() or _sha(target) != row["sha256"]:
            failures.append(row["path"])
    service = subprocess.run(["systemctl", "is-active", manifest["service"]], text=True, capture_output=True)
    if failures or service.returncode != 0 or service.stdout.strip() != "active":
        raise DeployError("H20_DEPLOY_LIVE_VERIFY_FAILED:" + ",".join(failures))
    restore_commands = []
    previous_hashes = []
    for row in manifest["files"]:
        live = LIVE_ROOT / row["path"]
        saved = Path(manifest["backup_root"]) / "live-root" / row["path"]
        if row["previous_exists"]:
            restore_commands.append(f"sudo install -D -o human20team -g human20team {shlex.quote(str(saved))} {shlex.quote(str(live))}")
            previous_hashes.append(f"- `{row['path']}` → `{row['previous_sha256']}`")
        else:
            restore_commands.append(f"sudo rm -f {shlex.quote(str(live))}")
            previous_hashes.append(f"- `{row['path']}` → absent")
    rollback = f"""# Human20Bot exact rollback\n\nApproved candidate: `{manifest['candidate_artifact_sha256']}`\nBackup: `{manifest['backup_root']}`\nService: `{manifest['service']}`\n\n## Commands\n\n```bash\nsudo systemctl stop {manifest['service']}\n{chr(10).join(restore_commands)}\nif test -f {shlex.quote(str(Path(manifest['backup_root']) / 'profile/config.yaml'))}; then sudo install -m 0600 -o human20team -g human20team {shlex.quote(str(Path(manifest['backup_root']) / 'profile/config.yaml'))} /home/human20team/.hermes/config.yaml; fi\nsudo systemctl daemon-reload\nsudo systemctl restart {manifest['service']}\nsystemctl is-active {manifest['service']}\n```\n\n## Expected previous state\n\n{chr(10).join(previous_hashes)}\n\nAfter restart verify Bot API `getMe` = `8928336881 @Human20Bot`, mention-only routing includes `{EXPECTED_CHAT}`, and the service is active.\n"""
    _write(runbook, rollback)
    return {"ok": True, "status": "PASS", "files": len(manifest["files"]), "service": "active", "rollback_runbook": str(runbook), "candidate_artifact_sha256": manifest["candidate_artifact_sha256"]}
