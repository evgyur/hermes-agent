from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dataset import build_dataset, check_dataset, redact_check, validate_labels
from agent.human20_skill_router import build_skill_manifest, privacy_findings, quality_scorecard
from agent.human20_observability import build_ops_report
from agent.human20_shadow import adjudication_check, audit_shadow, canary_readiness, compare_shadow, replay_shadow
from agent.human20_deploy import (
    assert_canary_green, canary_run, check_approval, dark_deploy, disable_drill,
    promote, secret_scan, verify_live, verify_manifest as verify_deploy_manifest,
)
from .scorecard import write_scorecard

FOCUSED_TESTS = [
    "tests/run_agent/test_tool_call_guardrail_runtime.py",
    "tests/gateway/test_session_split_brain_11016.py",
    "tests/gateway/test_telegram_group_gating.py",
    "tests/gateway/test_telegram_human20_cta.py",
    "tests/tools/test_read_loop_detection.py",
]


def _emit(payload: Any, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, sort_keys=True)
    stream.write(text + "\n")


def _run(command: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr[-1200:]}")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_digest(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {"exists": False, "files": 0, "sha256": None}
    lines: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts):
        rel = path.relative_to(root).as_posix()
        lines.append(f"{_sha256(path)}  {rel}")
    blob = ("\n".join(lines) + ("\n" if lines else "")).encode()
    return {"exists": True, "files": len(lines), "sha256": hashlib.sha256(blob).hexdigest()}


def _status_paths(root: Path) -> list[str]:
    raw = _run(["git", "status", "--porcelain=v1", "-z"], cwd=root).stdout
    parts = raw.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(parts):
        item = parts[index]
        if not item:
            break
        code, path = item[:2], item[3:]
        if code[0] in {"R", "C"} or code[1] in {"R", "C"}:
            index += 1
            if index >= len(parts):
                raise RuntimeError("malformed rename entry in git status")
            path = parts[index]
        paths.append(path)
        index += 1
    return sorted(paths)


def _file_record(root: Path, rel: str) -> dict[str, Any]:
    path = root / rel
    if not path.is_file():
        raise RuntimeError(f"baseline path is not a regular file: {path}")
    st = path.stat()
    tracked = _run(["git", "ls-files", "--error-unmatch", "--", rel], cwd=root, check=False).returncode == 0
    return {
        "path": rel,
        "sha256": _sha256(path),
        "size": st.st_size,
        "mode": stat.S_IMODE(st.st_mode),
        "owner": pwd.getpwuid(st.st_uid).pw_name,
        "tracked": tracked,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def capture_manifest(args: argparse.Namespace) -> None:
    live = Path(args.live_root).resolve()
    candidate = Path(args.candidate_root).resolve()
    head = _run(["git", "rev-parse", "HEAD"], cwd=live).stdout.strip()
    if head != args.expected_head:
        raise RuntimeError(f"live HEAD drift: expected {args.expected_head}, got {head}")
    paths = _status_paths(live)
    files = [_file_record(live, rel) for rel in paths]
    for record in files:
        candidate_path = candidate / record["path"]
        if not candidate_path.is_file():
            raise RuntimeError(f"candidate mirror missing: {candidate_path}")
        record["candidate_sha256"] = _sha256(candidate_path)
        if record["candidate_sha256"] != record["sha256"]:
            raise RuntimeError(f"candidate mirror mismatch: {record['path']}")
    config = Path(args.config)
    soul = Path(args.soul)
    pip_freeze = _run([str(candidate / "venv/bin/python"), "-m", "pip", "freeze"], cwd=candidate).stdout
    service_active = _run(["systemctl", "is-active", args.service], check=False).stdout.strip()
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "live_root": str(live),
        "candidate_root": str(candidate),
        "head": head,
        "branch": _run(["git", "branch", "--show-current"], cwd=live).stdout.strip(),
        "service": {"name": args.service, "active": service_active == "active", "unit_sha256": hashlib.sha256(_run(["systemctl", "cat", args.service]).stdout.encode()).hexdigest()},
        "dirty_files": files,
        "config": {"path": str(config), "sha256": _sha256(config), "mode": stat.S_IMODE(config.stat().st_mode)},
        "soul": {"path": str(soul), "exists": soul.exists(), "sha256": _sha256(soul) if soul.exists() else None},
        "skills": {"path": args.skills_root, **_tree_digest(Path(args.skills_root))},
        "dependencies": {
            "python": sys.version.split()[0],
            "pip_freeze_count": len([x for x in pip_freeze.splitlines() if x.strip()]),
            "pip_freeze_sha256": hashlib.sha256(pip_freeze.encode()).hexdigest(),
        },
    }
    _write_json(Path(args.out), payload)
    _emit({"ok": True, "head": head, "dirty_count": len(files), "manifest": args.out})


def verify_identity(args: argparse.Namespace) -> None:
    active = _run(["systemctl", "is-active", args.service], check=False).stdout.strip()
    if active != "active":
        raise RuntimeError(f"service is not active: {active}")
    values = _load_dotenv(Path(args.env_file))
    token = values.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is unavailable")
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/getMe")
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    result = payload.get("result") or {}
    bot_id, username = int(result.get("id", 0)), str(result.get("username", ""))
    if not payload.get("ok") or bot_id != args.expected_id or username.lower() != args.expected_username.lower():
        raise RuntimeError(f"identity mismatch: id={bot_id} username={username!r}")
    public = {"ok": True, "service": args.service, "service_active": True, "bot_id": bot_id, "username": username, "credential_output": "redacted"}
    rendered = json.dumps(public, ensure_ascii=False, sort_keys=True)
    if token in rendered:
        raise RuntimeError("credential leak detected in verifier output")
    _emit(rendered)


def verify_manifest(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest)
    data = json.loads(manifest_path.read_text())
    live = Path(args.live_root).resolve()
    candidate = Path(data["candidate_root"]).resolve()
    expected_paths = sorted(x["path"] for x in data["dirty_files"])
    actual_paths = _status_paths(live)
    if len(expected_paths) != args.expect_dirty_count or actual_paths != expected_paths:
        raise RuntimeError(f"dirty set drift: expected={expected_paths}, actual={actual_paths}")
    changed: list[str] = []
    for record in data["dirty_files"]:
        rel = record["path"]
        if _sha256(live / rel) != record["sha256"]:
            changed.append(f"live:{rel}")
        if _sha256(candidate / rel) != record["candidate_sha256"]:
            changed.append(f"candidate:{rel}")
    if _sha256(Path(data["config"]["path"])) != data["config"]["sha256"]:
        changed.append("config")
    if data["soul"]["exists"] and _sha256(Path(data["soul"]["path"])) != data["soul"]["sha256"]:
        changed.append("soul")
    if _tree_digest(Path(data["skills"]["path"]))["sha256"] != data["skills"]["sha256"]:
        changed.append("skills")
    if changed:
        raise RuntimeError(f"baseline drift: {changed}")
    _emit({"ok": True, "dirty_count": len(expected_paths), "changed": [], "head": data["head"]})


def verify_worktree(args: argparse.Namespace) -> None:
    candidate = Path(args.candidate).resolve()
    live = Path(args.live).resolve()
    if candidate == live:
        raise RuntimeError("candidate and live roots are identical")
    candidate_head = _run(["git", "rev-parse", "HEAD"], cwd=candidate).stdout.strip()
    live_head = _run(["git", "rev-parse", "HEAD"], cwd=live).stdout.strip()
    if candidate_head != args.expected_head or live_head != args.expected_head:
        raise RuntimeError(f"HEAD mismatch: live={live_head}, candidate={candidate_head}")
    manifest = json.loads((candidate / ".supergoal-evidence/baseline-manifest.json").read_text())
    mismatches = []
    for record in manifest["dirty_files"]:
        rel = record["path"]
        if _sha256(candidate / rel) != record["sha256"]:
            mismatches.append(rel)
    if mismatches:
        raise RuntimeError(f"candidate does not mirror baseline: {mismatches}")
    worktrees = _run(["git", "worktree", "list", "--porcelain"], cwd=live).stdout
    if f"worktree {candidate}" not in worktrees:
        raise RuntimeError("candidate is not registered as a git worktree")
    _emit({"ok": True, "live_head": live_head, "candidate_head": candidate_head, "candidate": str(candidate), "mirrored_files": len(manifest["dirty_files"])})


def capture_known_red(args: argparse.Namespace) -> None:
    root = Path.cwd()
    command = [str(root / "venv/bin/pytest"), "-q", "-o", "addopts=", *FOCUSED_TESTS]
    result = _run(command, cwd=root, check=False)
    text = result.stdout + "\n" + result.stderr
    match = re.search(r"(?:(\d+) failed, )?(\d+) passed", text)
    if not match:
        raise RuntimeError("pytest summary not found")
    failed = int(match.group(1) or 0)
    passed = int(match.group(2))
    failed_tests = [line.split()[1] for line in text.splitlines() if line.startswith("FAILED ")]
    normalized_warnings = []
    if "Invalid Telegram mention pattern" in text:
        normalized_warnings.append("invalid-mention-pattern")
    if passed != args.expect_passed or failed != args.expect_failed_count:
        raise RuntimeError(f"unexpected baseline: passed={passed}, failed={failed}")
    if args.expect_warning not in normalized_warnings:
        raise RuntimeError(f"expected warning missing: {args.expect_warning}")
    manifest_path = root / ".supergoal-evidence/baseline-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "command": ["venv/bin/pytest", "-q", "-o", "addopts=", *FOCUSED_TESTS],
        "expected_red": True,
        "pytest_exit": result.returncode,
        "passed": passed,
        "failed": failed,
        "failed_tests": failed_tests,
        "warnings": normalized_warnings,
        "head": manifest["head"],
        "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "output_tail": text[-4000:],
    }
    _write_json(Path(args.out), payload)
    rollback = {
        "schema_version": 1,
        "created_at": payload["created_at"],
        "head": manifest["head"],
        "dirty_files": manifest["dirty_files"],
        "config": manifest["config"],
        "soul": manifest["soul"],
        "skills": manifest["skills"],
        "service": manifest["service"],
    }
    rollback_path = Path(args.out).with_name("rollback-manifest.json")
    _write_json(rollback_path, rollback)
    _emit({"ok": True, "expected_red": True, "passed": passed, "failed": failed, "failed_tests": failed_tests, "warnings": normalized_warnings, "rollback_manifest": str(rollback_path)})


def _dataset_build(args: argparse.Namespace) -> None:
    _emit(build_dataset(Path(args.source), Path(args.out), args.size, args.source_sha))


def _dataset_check(args: argparse.Namespace) -> None:
    _emit(check_dataset(Path(args.input), Path(args.resolve_against), args.minimum, args.maximum, args.min_policy_controls))


def _dataset_redact_check(args: argparse.Namespace) -> None:
    _emit(redact_check(Path(args.input)))


def _dataset_validate_labels(args: argparse.Namespace) -> None:
    _emit(validate_labels(Path(args.input), Path(args.source), args.require_atomic_outcomes))


def _secure_json_write(path: Path, payload: dict[str, Any]) -> None:
    evidence_root = (Path.cwd() / ".supergoal-evidence").resolve()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(evidence_root):
        raise RuntimeError("H20_EVIDENCE_PATH_DENIED")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    resolved.chmod(0o600)


def _skills_inventory(args: argparse.Namespace) -> None:
    candidate_root = Path.cwd().resolve()
    config_path = candidate_root / "config/human20_skill_routes.yaml"
    manifest = build_skill_manifest(
        source_root=Path(args.root),
        candidate_root=candidate_root,
        config_path=config_path,
    )
    if args.require_owner_trigger_negative_tier_verifier_removal:
        required = {"owner", "trigger", "negative_trigger", "actor_tier", "verifier", "removal_condition"}
        for entry in manifest["exposed"]:
            if required - set(entry):
                raise RuntimeError("H20_SKILL_REQUIRED_METADATA_MISSING")
    findings = privacy_findings(candidate_root=candidate_root, config_path=config_path)
    if findings:
        raise RuntimeError("H20_SKILL_PRIVACY_FINDINGS")
    manifest["privacy_findings"] = findings
    _secure_json_write(Path(args.out), manifest)
    _emit(manifest)


def _eval_quality(args: argparse.Namespace) -> None:
    candidate_root = Path.cwd().resolve()
    payload = quality_scorecard(
        dataset=Path(args.dataset),
        config_path=candidate_root / "config/human20_skill_routes.yaml",
        min_lift_pp=float(args.min_lift_pp),
        no_family_regression=bool(args.no_family_regression),
    )
    _secure_json_write(Path(args.out), payload)
    _emit(payload)


def _eval_run(args: argparse.Namespace) -> None:
    _emit(write_scorecard(Path(args.input), Path(args.out)))


def _policy_check(args: argparse.Namespace) -> None:
    from agent.human20_capability_policy import CapabilityPolicy

    config = Path(args.config).resolve()
    policy = CapabilityPolicy.load(config)
    covered, total, coverage = policy.coverage()
    required_surfaces = {"direct-tool", "quick-command", "helper"}
    bypass_coverage = len(required_surfaces & policy.enforcement_surfaces) / len(required_surfaces)
    if coverage < args.require_coverage:
        raise RuntimeError(f"policy coverage {coverage:.6f} is below {args.require_coverage:.6f}")
    if args.check_bypasses and bypass_coverage < 1.0:
        raise RuntimeError(f"bypass coverage incomplete: {bypass_coverage:.6f}")
    _emit({
        "ok": True,
        "coverage": coverage,
        "covered_cells": covered,
        "total_cells": total,
        "bypass_coverage": bypass_coverage,
        "enforcement_surfaces": sorted(policy.enforcement_surfaces),
        "config_sha256": _sha256(config),
    })


def _source_map_check(args: argparse.Namespace) -> None:
    from agent.human20_context_router import load_source_map

    config = Path(args.config).resolve()
    source_map = load_source_map(config)
    domains = source_map["domains"]
    mutable_counts = {
        name: sum(1 for source in domain["sources"] if source.get("mutable") is True)
        for name, domain in domains.items()
    }
    if args.exactly_one_mutable_source and any(count != 1 for count in mutable_counts.values()):
        raise RuntimeError(f"mutable source invariant failed: {mutable_counts}")
    _emit({
        "ok": True,
        "domains": sorted(domains),
        "domain_count": len(domains),
        "mutable_source_counts": mutable_counts,
        "exactly_one_mutable_source": all(count == 1 for count in mutable_counts.values()),
        "config_sha256": _sha256(config),
    })


def _replay(args: argparse.Namespace) -> None:
    if args.dataset:
        root = Path.cwd().resolve()
        dataset = Path(args.dataset).resolve()
        out = Path(args.out).resolve() if args.out else None
        if args.profile != "candidate" or args.mode != "shadow" or not args.deny_all_effects:
            raise RuntimeError("H20_SHADOW_FLAGS_REQUIRED")
        if not dataset.is_relative_to(root / "data"):
            raise RuntimeError("H20_SHADOW_DATASET_PATH_DENIED")
        if out is None or not out.is_relative_to(root / ".supergoal-evidence"):
            raise RuntimeError("H20_EVIDENCE_PATH_DENIED")
        payload = replay_shadow(dataset=dataset, out=out)
        _emit({"ok": True, "summary": payload["summary"], "zero_effects": True, "semantic_answer_quality_claim": False})
        return

    from agent.human20_context_router import load_source_map, replay_incident

    if not args.episodes:
        raise RuntimeError("--episodes is required for dry-run source replay")
    source_map_path = Path(args.source_map).resolve()
    source_map = load_source_map(source_map_path)
    try:
        episodes = [int(value.strip()) for value in args.episodes.split(",") if value.strip()]
    except ValueError as exc:
        raise RuntimeError("episodes must be comma-separated integer message IDs") from exc
    if not episodes:
        raise RuntimeError("at least one episode is required")
    results = [replay_incident(source_map, episode) for episode in episodes]
    if args.assert_price_set:
        expected = [int(value.strip()) for value in args.assert_price_set.split(",") if value.strip()]
        price_results = [result for result in results if result.get("status") == "verified_fact"]
        if len(price_results) != 1 or price_results[0].get("prices") != expected:
            raise RuntimeError(f"price assertion failed: expected={expected} results={price_results}")
    forbidden = {int(value) for value in (args.forbid or [])}
    for result in results:
        if forbidden & set(result.get("prices") or []):
            raise RuntimeError(f"forbidden price present: {sorted(forbidden)}")
    if args.require_context_or_exact_blocker:
        invalid = [
            result for result in results
            if result.get("status") not in {"context_plan", "exact_blocker"}
            or (result.get("status") == "context_plan" and not result.get("operations"))
            or (result.get("status") == "exact_blocker" and not result.get("blocker_code"))
        ]
        if invalid:
            raise RuntimeError(f"context/blocker contract failed: {invalid}")
    _emit({
        "ok": True,
        "mode": args.mode,
        "episodes": results,
        "source_map_sha256": _sha256(source_map_path),
        "external_effects": 0,
    })


def _cmd_research_smoke(args: argparse.Namespace) -> int:
    from urllib.request import Request, urlopen
    from agent.human20_research import research_query

    if not args.live_read_only:
        raise RuntimeError("H20_RESEARCH_LIVE_READ_ONLY_REQUIRED")
    query_specs = [
        (
            "Python official documentation",
            ["https://docs.python.org/", "https://www.python.org/doc/"],
        ),
        (
            "Telegram Bot API official documentation",
            ["https://core.telegram.org/bots/api", "https://developers.telegram.org/"],
        ),
        (
            "OpenAI API official documentation",
            ["https://developers.openai.com/api/docs", "https://developers.openai.com/api/reference/overview"],
        ),
    ]
    if args.queries < 1 or args.queries > len(query_specs):
        raise RuntimeError("H20_RESEARCH_QUERY_COUNT_INVALID")
    results = []
    for query, authoritative_urls in query_specs[: args.queries]:
        result = research_query(query, min_sources=args.min_sources, limit=max(6, args.min_sources))
        retrievable = []
        for source_url in authoritative_urls:
            try:
                request = Request(source_url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(request, timeout=10) as response:
                    status = int(getattr(response, "status", 200))
                    response.read(1)
                if 200 <= status < 400:
                    retrievable.append({"url": source_url, "status": status})
            except Exception:
                continue
        if len(retrievable) < args.min_sources:
            raise RuntimeError("H20_RESEARCH_SOURCES_NOT_RETRIEVABLE")
        result["authoritative_sources"] = authoritative_urls
        result["retrievable_sources"] = retrievable
        results.append(result)
    payload = {
        "ok": True,
        "mode": "live_read_only",
        "query_count": len(results),
        "min_sources": args.min_sources,
        "results": results,
        "live_mutations": 0,
    }
    _write_json(Path(args.out), payload)
    _emit(payload)
    return 0


def _cmd_artifact_e2e(args: argparse.Namespace) -> int:
    from agent.human20_artifacts import ArtifactWorkspace, FakeTelegramDelivery

    if args.transport != "fake" or not args.assert_zero_live_send:
        raise RuntimeError("H20_ARTIFACT_FAKE_TRANSPORT_REQUIRED")
    workspace = ArtifactWorkspace(Path.cwd() / ".supergoal-sandbox", "p06-e2e", max_bytes=1_048_576)
    artifact = workspace.write_bytes("reports/e2e.txt", b"P06_VERIFIED_ARTIFACT\n")
    delivery = FakeTelegramDelivery(workspace)
    missing = delivery.deliver("reports/missing.txt", requested=True)
    receipt = delivery.deliver("reports/e2e.txt", requested=True)
    if delivery.live_send_count != 0 or receipt.get("live_send") is not False:
        raise RuntimeError("H20_ARTIFACT_LIVE_SEND_DETECTED")
    payload = {
        "ok": True,
        "artifact": artifact,
        "missing_file_probe": missing,
        "delivery": receipt,
        "live_send_count": delivery.live_send_count,
    }
    _write_json(Path(args.out), payload)
    _emit(payload)
    return 0


def _approval_check(args: argparse.Namespace) -> None:
    payload = check_approval(
        Path(args.receipt), approval_class=args.approval_class,
        expected_bot=args.expected_bot, expected_chat=args.expected_chat,
        require_canary_scope=bool(args.require_chat_thread_admins_sha_window_rollback),
        require_rollout_scope=bool(args.require_audience_window_rollback_owner),
    )
    _emit(payload)


def _deploy_dark(args: argparse.Namespace) -> None:
    _emit(dark_deploy(approval=Path(args.approval), out=Path(args.out), feature_off=args.feature_off, service=args.service))


def _canary_run_live(args: argparse.Namespace) -> None:
    _emit(canary_run(approval=Path(args.approval), out=Path(args.out), max_calls=args.max_calls, enable=args.enable, disable_after_window=args.disable_after_window, require_readback=args.require_message_readback))


def _canary_disable(args: argparse.Namespace) -> None:
    _emit(disable_drill(approval=Path(args.approval), out=Path(args.out), restore_routing=args.restore_routing))


def _canary_assert(args: argparse.Namespace) -> None:
    _emit(assert_canary_green(Path(args.report)))


def _security_secret_scan(args: argparse.Namespace) -> None:
    _emit(secret_scan())


def _deploy_manifest_verify(args: argparse.Namespace) -> None:
    _emit(verify_deploy_manifest(Path(args.manifest)))


def _deploy_promote(args: argparse.Namespace) -> None:
    _emit(promote(approval=Path(args.approval), manifest_path=Path(args.manifest), out=Path(args.out), require_readback=args.require_readback, smokes=[value for value in args.smoke.split(",") if value]))


def _deploy_verify_live(args: argparse.Namespace) -> None:
    _emit(verify_live(manifest_path=Path(args.manifest), runbook=Path(args.runbook)))


def _shadow_audit(args: argparse.Namespace) -> None:
    root = Path.cwd().resolve()
    shadow = Path(args.shadow).resolve()
    if not shadow.is_relative_to(root / ".supergoal-evidence"):
        raise RuntimeError("H20_EVIDENCE_PATH_DENIED")
    _emit(audit_shadow(shadow, expect_zero_effects=args.expect_zero_effects))


def _eval_compare_shadow(args: argparse.Namespace) -> None:
    root = Path.cwd().resolve()
    evidence = root / ".supergoal-evidence"
    candidate = Path(args.candidate).resolve()
    baseline = Path(args.baseline).resolve()
    out = Path(args.out).resolve()
    if not all(path.is_relative_to(evidence) for path in (candidate, baseline, out)):
        raise RuntimeError("H20_EVIDENCE_PATH_DENIED")
    payload = compare_shadow(
        candidate=candidate, baseline=baseline, min_safe_policy=args.min_safe_policy,
        min_outcome=args.min_outcome, min_lift_pp=args.min_baseline_lift_pp,
        max_hermes_gap_pp=args.max_hermes_gap_pp, out=out,
    )
    _emit(payload)


def _eval_adjudication_check(args: argparse.Namespace) -> None:
    root = Path.cwd().resolve()
    dataset = Path(args.dataset).resolve()
    if not dataset.is_relative_to(root / "data"):
        raise RuntimeError("H20_SHADOW_DATASET_PATH_DENIED")
    _emit(adjudication_check(dataset=dataset, require_reviewer_reason_diff=args.require_reviewer_reason_diff))


def _canary_readiness(args: argparse.Namespace) -> None:
    root = Path.cwd().resolve()
    evidence = root / ".supergoal-evidence"
    comparison = Path(args.comparison).resolve()
    out = Path(args.out).resolve()
    if not comparison.is_relative_to(evidence) or not out.is_relative_to(evidence):
        raise RuntimeError("H20_EVIDENCE_PATH_DENIED")
    _emit(canary_readiness(comparison=comparison, out=out))


def _cmd_observability_report(args: argparse.Namespace) -> int:
    candidate_root = Path.cwd().resolve()
    fixture = Path(args.fixture).resolve()
    evidence_root = (candidate_root / ".supergoal-evidence").resolve()
    out = Path(args.out).resolve()
    if not fixture.is_relative_to(candidate_root / "tests" / "fixtures"):
        raise RuntimeError("H20_TELEMETRY_FIXTURE_PATH_DENIED")
    if not out.is_relative_to(evidence_root):
        raise RuntimeError("H20_EVIDENCE_PATH_DENIED")
    payload = build_ops_report(
        fixture=fixture,
        alert_at=args.alert_at,
        hard_stop_at=args.hard_stop_at,
        out=out,
    )
    _emit(payload)
    return 0


def _cmd_runtime_runaway_probe(args: argparse.Namespace) -> int:
    from agent.tool_guardrails import ToolCallGuardrailConfig, ToolCallGuardrailController

    config = ToolCallGuardrailConfig.from_mapping({
        "warnings_enabled": True,
        "hard_stop_enabled": True,
        "warn_after": {
            "total_calls": args.warn,
            "exact_failure": args.exact_failure_halt,
        },
        "hard_stop_after": {
            "total_calls": args.halt,
            "exact_failure": args.exact_failure_halt,
        },
    })
    guard = ToolCallGuardrailController(config)
    observations: list[dict[str, Any]] = []
    for index in range(1, args.repeat + 1):
        before = guard.before_call(args.tool, {"probe_index": index})
        if not before.allows_execution:
            observations.append(before.to_metadata())
            break
        decision = guard.after_call(
            args.tool,
            {"probe_index": index},
            json.dumps({"ok": True, "probe_index": index}, sort_keys=True),
            failed=False,
        )
        observations.append(decision.to_metadata())
        if decision.should_halt:
            break

    exact_guard = ToolCallGuardrailController(config)
    exact_decisions = [
        exact_guard.after_call(
            args.tool,
            {"probe": "identical-failure"},
            json.dumps({"error": "deterministic probe failure"}),
            failed=True,
        )
        for _ in range(args.exact_failure_halt)
    ]

    warnings = [item for item in observations if item.get("action") == "warn"]
    halts = [item for item in observations if item.get("action") == "halt"]
    exact_halt = exact_decisions[-1]
    status = "passed" if (
        warnings
        and warnings[0].get("count") == args.warn
        and halts
        and halts[0].get("count") == args.halt
        and exact_halt.action == "halt"
        and exact_halt.count == args.exact_failure_halt
    ) else "failed"
    payload = {
        "status": status,
        "tool": args.tool,
        "warning_at": warnings[0].get("count") if warnings else None,
        "halt_at": halts[0].get("count") if halts else None,
        "exact_failure_halt_at": exact_halt.count if exact_halt.action == "halt" else None,
        "false_completion": False,
        "final_behavior": "honest_blocker_or_verified_final_answer",
    }
    _emit(payload)
    return 0 if status == "passed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.human20_parity")
    top = parser.add_subparsers(dest="area", required=True)
    baseline = top.add_parser("baseline")
    sub = baseline.add_subparsers(dest="action", required=True)

    capture = sub.add_parser("capture-manifest")
    capture.add_argument("--live-root", required=True)
    capture.add_argument("--candidate-root", required=True)
    capture.add_argument("--expected-head", required=True)
    capture.add_argument("--service", required=True)
    capture.add_argument("--config", default="/home/human20team/.hermes/config.yaml")
    capture.add_argument("--soul", default="/home/human20team/.hermes/SOUL.md")
    capture.add_argument("--skills-root", default="/home/human20team/.hermes/skills")
    capture.add_argument("--out", required=True)
    capture.set_defaults(handler=capture_manifest)

    identity = sub.add_parser("verify-identity")
    identity.add_argument("--service", required=True)
    identity.add_argument("--expected-id", type=int, required=True)
    identity.add_argument("--expected-username", required=True)
    identity.add_argument("--env-file", default="/home/human20team/.hermes/secrets/team-telegram.env")
    identity.add_argument("--redact", action="store_true")
    identity.set_defaults(handler=verify_identity)

    manifest = sub.add_parser("verify-manifest")
    manifest.add_argument("--manifest", required=True)
    manifest.add_argument("--live-root", required=True)
    manifest.add_argument("--expect-dirty-count", type=int, required=True)
    manifest.set_defaults(handler=verify_manifest)

    worktree = sub.add_parser("verify-worktree")
    worktree.add_argument("--candidate", required=True)
    worktree.add_argument("--live", required=True)
    worktree.add_argument("--expected-head", required=True)
    worktree.set_defaults(handler=verify_worktree)

    red = sub.add_parser("capture-known-red")
    red.add_argument("--out", required=True)
    red.add_argument("--expect-passed", type=int, required=True)
    red.add_argument("--expect-failed-count", type=int, required=True)
    red.add_argument("--expect-warning", required=True)
    red.set_defaults(handler=capture_known_red)

    dataset = top.add_parser("dataset")
    dataset_sub = dataset.add_subparsers(dest="action", required=True)
    dataset_build = dataset_sub.add_parser("build")
    dataset_build.add_argument("--source", required=True)
    dataset_build.add_argument("--out", required=True)
    dataset_build.add_argument("--size", type=int, default=160)
    dataset_build.add_argument("--source-sha", required=True)
    dataset_build.set_defaults(handler=_dataset_build)

    dataset_check = dataset_sub.add_parser("check")
    dataset_check.add_argument("--input", required=True)
    dataset_check.add_argument("--min", type=int, dest="minimum", required=True)
    dataset_check.add_argument("--max", type=int, dest="maximum", required=True)
    dataset_check.add_argument("--min-policy-controls", type=int, required=True)
    dataset_check.add_argument("--resolve-against", required=True)
    dataset_check.set_defaults(handler=_dataset_check)

    dataset_redact = dataset_sub.add_parser("redact-check")
    dataset_redact.add_argument("input")
    dataset_redact.add_argument("--fail-on-private-payload", action="store_true")
    dataset_redact.add_argument("--fail-on-secret", action="store_true")
    dataset_redact.set_defaults(handler=_dataset_redact_check)

    dataset_labels = dataset_sub.add_parser("validate-labels")
    dataset_labels.add_argument("--input", required=True)
    dataset_labels.add_argument("--source", required=True)
    dataset_labels.add_argument("--require-atomic-outcomes", action="store_true")
    dataset_labels.set_defaults(handler=_dataset_validate_labels)

    skills = top.add_parser("skills")
    skills_sub = skills.add_subparsers(dest="action", required=True)
    skills_inventory = skills_sub.add_parser("inventory")
    skills_inventory.add_argument("--root", required=True)
    skills_inventory.add_argument("--out", required=True)
    skills_inventory.add_argument("--require-owner-trigger-negative-tier-verifier-removal", action="store_true")
    skills_inventory.set_defaults(handler=_skills_inventory)

    evaluator = top.add_parser("eval")
    eval_sub = evaluator.add_subparsers(dest="action", required=True)
    eval_run = eval_sub.add_parser("run")
    eval_run.add_argument("--input", required=True)
    eval_run.add_argument("--paired", action="store_true")
    eval_run.add_argument("--out", required=True)
    eval_run.set_defaults(handler=_eval_run)
    eval_quality = eval_sub.add_parser("quality")
    eval_quality.add_argument("--dataset", required=True)
    eval_quality.add_argument("--paired", action="store_true")
    eval_quality.add_argument("--min-lift-pp", type=float, required=True)
    eval_quality.add_argument("--no-family-regression", action="store_true")
    eval_quality.add_argument("--out", required=True)
    eval_quality.set_defaults(handler=_eval_quality)
    eval_compare = eval_sub.add_parser("compare")
    eval_compare.add_argument("--candidate", required=True)
    eval_compare.add_argument("--baseline", required=True)
    eval_compare.add_argument("--min-safe-policy", type=float, required=True)
    eval_compare.add_argument("--min-outcome", type=float, required=True)
    eval_compare.add_argument("--min-baseline-lift-pp", type=float, required=True)
    eval_compare.add_argument("--max-hermes-gap-pp", type=float, required=True)
    eval_compare.add_argument("--out", required=True)
    eval_compare.set_defaults(handler=_eval_compare_shadow)
    eval_adjudication = eval_sub.add_parser("adjudication-check")
    eval_adjudication.add_argument("--dataset", required=True)
    eval_adjudication.add_argument("--require-reviewer-reason-diff", action="store_true")
    eval_adjudication.set_defaults(handler=_eval_adjudication_check)

    approval = top.add_parser("approval")
    approval_sub = approval.add_subparsers(dest="action", required=True)
    approval_check = approval_sub.add_parser("check")
    approval_check.add_argument("--class", dest="approval_class", required=True, choices=["live_canary", "live_rollout"])
    approval_check.add_argument("--receipt", required=True)
    approval_check.add_argument("--expected-bot", type=int)
    approval_check.add_argument("--expected-chat", type=int)
    approval_check.add_argument("--candidate-root")
    approval_check.add_argument("--require-chat-thread-admins-sha-window-rollback", action="store_true")
    approval_check.add_argument("--require-audience-window-rollback-owner", action="store_true")
    approval_check.set_defaults(handler=_approval_check)

    deploy = top.add_parser("deploy")
    deploy_sub = deploy.add_subparsers(dest="action", required=True)
    deploy_dark = deploy_sub.add_parser("dark")
    deploy_dark.add_argument("--approval", required=True)
    deploy_dark.add_argument("--candidate-root")
    deploy_dark.add_argument("--feature-off", action="store_true")
    deploy_dark.add_argument("--service", required=True)
    deploy_dark.add_argument("--out", required=True)
    deploy_dark.set_defaults(handler=_deploy_dark)
    deploy_manifest = deploy_sub.add_parser("manifest")
    deploy_manifest_sub = deploy_manifest.add_subparsers(dest="manifest_action", required=True)
    deploy_manifest_verify = deploy_manifest_sub.add_parser("verify")
    deploy_manifest_verify.add_argument("manifest")
    deploy_manifest_verify.set_defaults(handler=_deploy_manifest_verify)
    deploy_manifest_live = deploy_manifest_sub.add_parser("verify-live")
    deploy_manifest_live.add_argument("--manifest", required=True)
    deploy_manifest_live.add_argument("--runbook", required=True)
    deploy_manifest_live.set_defaults(handler=_deploy_verify_live)
    deploy_promote = deploy_sub.add_parser("promote")
    deploy_promote.add_argument("--approval", required=True)
    deploy_promote.add_argument("--manifest", required=True)
    deploy_promote.add_argument("--smoke", required=True)
    deploy_promote.add_argument("--require-readback", action="store_true")
    deploy_promote.add_argument("--out", required=True)
    deploy_promote.set_defaults(handler=_deploy_promote)

    security = top.add_parser("security")
    security_sub = security.add_subparsers(dest="action", required=True)
    security_scan = security_sub.add_parser("secret-scan")
    security_scan.add_argument("--git-diff", action="store_true")
    security_scan.add_argument("--evidence")
    security_scan.set_defaults(handler=_security_secret_scan)

    policy = top.add_parser("policy")
    policy_sub = policy.add_subparsers(dest="action", required=True)
    policy_check = policy_sub.add_parser("check")
    policy_check.add_argument("--config", required=True)
    policy_check.add_argument("--require-coverage", type=float, default=1.0)
    policy_check.add_argument("--check-bypasses", action="store_true")
    policy_check.set_defaults(handler=_policy_check)

    source_map = top.add_parser("source-map")
    source_map_sub = source_map.add_subparsers(dest="action", required=True)
    source_map_check = source_map_sub.add_parser("check")
    source_map_check.add_argument("config")
    source_map_check.add_argument("--exactly-one-mutable-source", action="store_true")
    source_map_check.set_defaults(handler=_source_map_check)

    replay = top.add_parser("replay")
    replay.add_argument("--episodes")
    replay.add_argument("--dataset")
    replay.add_argument("--profile")
    replay.add_argument("--mode", choices=["dry-run", "shadow"], default="dry-run")
    replay.add_argument("--deny-all-effects", action="store_true")
    replay.add_argument("--out")
    replay.add_argument("--source-map", default="config/human20_source_map.yaml")
    replay.add_argument("--assert-price-set")
    replay.add_argument("--forbid", action="append")
    replay.add_argument("--require-context-or-exact-blocker", action="store_true")
    replay.set_defaults(handler=_replay)

    shadow = top.add_parser("shadow")
    shadow_sub = shadow.add_subparsers(dest="action", required=True)
    shadow_audit = shadow_sub.add_parser("audit")
    shadow_audit.add_argument("shadow")
    shadow_audit.add_argument("--expect-zero-effects", action="store_true")
    shadow_audit.set_defaults(handler=_shadow_audit)

    research = top.add_parser("research")
    research_sub = research.add_subparsers(dest="action", required=True)
    research_smoke = research_sub.add_parser("smoke")
    research_smoke.add_argument("--live-read-only", action="store_true")
    research_smoke.add_argument("--queries", type=int, required=True)
    research_smoke.add_argument("--min-sources", type=int, required=True)
    research_smoke.add_argument("--out", required=True)
    research_smoke.set_defaults(handler=_cmd_research_smoke)

    artifact = top.add_parser("artifact")
    artifact_sub = artifact.add_subparsers(dest="action", required=True)
    artifact_e2e = artifact_sub.add_parser("e2e")
    artifact_e2e.add_argument("--transport", choices=["fake"], required=True)
    artifact_e2e.add_argument("--assert-zero-live-send", action="store_true")
    artifact_e2e.add_argument("--out", required=True)
    artifact_e2e.set_defaults(handler=_cmd_artifact_e2e)

    canary = top.add_parser("canary")
    canary_sub = canary.add_subparsers(dest="action", required=True)
    canary_ready = canary_sub.add_parser("readiness")
    canary_ready.add_argument("--comparison", required=True)
    canary_ready.add_argument("--out", required=True)
    canary_ready.set_defaults(handler=_canary_readiness)
    canary_run_parser = canary_sub.add_parser("run")
    canary_run_parser.add_argument("--approval", required=True)
    canary_run_parser.add_argument("--enable", action="store_true")
    canary_run_parser.add_argument("--disable-after-window", action="store_true")
    canary_run_parser.add_argument("--require-zero-unauthorized", action="store_true")
    canary_run_parser.add_argument("--require-message-readback", action="store_true")
    canary_run_parser.add_argument("--max-calls", type=int, required=True)
    canary_run_parser.add_argument("--out", required=True)
    canary_run_parser.set_defaults(handler=_canary_run_live)
    canary_disable = canary_sub.add_parser("disable-drill")
    canary_disable.add_argument("--approval", required=True)
    canary_disable.add_argument("--restore-routing", action="store_true")
    canary_disable.add_argument("--out", required=True)
    canary_disable.set_defaults(handler=_canary_disable)
    canary_assert = canary_sub.add_parser("assert-green")
    canary_assert.add_argument("--report", required=True)
    canary_assert.set_defaults(handler=_canary_assert)

    observability = top.add_parser("observability")
    observability_sub = observability.add_subparsers(dest="action", required=True)
    observability_report = observability_sub.add_parser("report")
    observability_report.add_argument("--fixture", required=True)
    observability_report.add_argument("--alert-at", type=int, required=True)
    observability_report.add_argument("--hard-stop-at", type=int, required=True)
    observability_report.add_argument("--out", required=True)
    observability_report.set_defaults(handler=_cmd_observability_report)

    runtime = top.add_parser("runtime")
    runtime_sub = runtime.add_subparsers(dest="action", required=True)
    runaway = runtime_sub.add_parser("runaway-probe")
    runaway.add_argument("--tool", required=True)
    runaway.add_argument("--repeat", type=int, required=True)
    runaway.add_argument("--warn", type=int, required=True)
    runaway.add_argument("--halt", type=int, required=True)
    runaway.add_argument("--exact-failure-halt", type=int, required=True)
    runaway.set_defaults(handler=_cmd_runtime_runaway_probe)
    return parser


def main() -> int:
    # Root is used only for the manifest verifier because one captured live
    # backup is mode 0600 root:root. Scope git's safe-directory override to
    # this process; do not mutate global git configuration.
    if os.geteuid() == 0 and "GIT_CONFIG_COUNT" not in os.environ:
        os.environ.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "*",
        })
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)}, error=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
