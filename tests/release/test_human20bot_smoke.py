from __future__ import annotations

import grp
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from smoke_human20bot_team_access import (
    PUBLIC_BOT_ID,
    PUBLIC_BOT_USERNAME,
    SmokeDependencies,
    SmokeError,
    _environment_value,
    _gateway_authorization_mixin_type,
    claim_activation_scope,
    run_smoke_checks,
    sanitize_audit,
    validate_retained_activation_audit,
    validate_rollback_allowlist,
    validate_rollback_process_identity,
    validate_smoke_mode,
)


def test_smoke_cli_defaults_fail_closed_without_live_flag() -> None:
    here = Path(__file__).resolve()
    source_root = here.parents[1] if (here.parents[1] / "scripts/smoke_human20bot_team_access.py").is_file() else here.parents[2]
    script = source_root / "scripts/smoke_human20bot_team_access.py"
    assert script.is_file()
    proc = subprocess.run(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 2
    assert b"live smoke is fail-closed" in proc.stderr
    assert b"PASS" not in proc.stdout


def test_smoke_imports_the_real_gateway_authorization_mixin_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExpectedMixin:
        pass

    module = ModuleType("gateway.authz_mixin")
    module.GatewayAuthorizationMixin = ExpectedMixin  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gateway.authz_mixin", module)
    assert _gateway_authorization_mixin_type() is ExpectedMixin


def test_canary_send_is_activation_only_and_rollback_never_sends() -> None:
    assert validate_smoke_mode(
        activation_check=True, rollback_check=False, send_canaries=True
    ) == "activation"
    assert validate_smoke_mode(
        activation_check=False, rollback_check=False, send_canaries=False
    ) == "readonly"
    assert validate_smoke_mode(
        activation_check=False, rollback_check=True, send_canaries=False
    ) == "rollback"
    with pytest.raises(SmokeError, match="activation-only"):
        validate_smoke_mode(
            activation_check=False, rollback_check=False, send_canaries=True
        )
    with pytest.raises(SmokeError, match="cannot activate or send"):
        validate_smoke_mode(
            activation_check=True, rollback_check=True, send_canaries=True
        )


def test_full_smoke_uses_approved_destinations_but_redacts_them() -> None:
    manifest = {"candidate_sha": "a" * 40, "service": "synthetic-gateway.service"}
    destinations = {
        "owner_dm": "private-owner-identifier",
        "test_chat": "private-chat-identifier",
        "test_thread": "private-thread-identifier",
    }
    calls: list[tuple[str, str | None]] = []

    deps = SmokeDependencies(
        live_head=lambda: "a" * 40,
        service_snapshot=lambda: {"active": True, "restart_count": 0},
        get_me=lambda: {"id": PUBLIC_BOT_ID, "username": PUBLIC_BOT_USERNAME},
        synthetic_probes=lambda: {
            "member": "allowed",
            "non_member": "denied",
            "anonymous": "denied",
            "bot": "denied",
            "callback": "denied",
            "busy_session": "denied",
        },
        send_canary=lambda chat, thread: calls.append((chat, thread)) or (100 + len(calls)),
        observe=lambda: {"active": True, "restart_count": 0, "error_count": 0},
    )

    audit = run_smoke_checks(manifest, destinations, deps, send_canaries=True)
    encoded = json.dumps(audit, sort_keys=True)

    assert audit["status"] == "pass"
    assert audit["bot_identity"] == {
        "id": PUBLIC_BOT_ID,
        "username": PUBLIC_BOT_USERNAME,
    }
    assert audit["telegram_message_ids"] == [101, 102]
    assert len(calls) == 2
    assert all(value not in encoded for value in destinations.values())
    assert "message_text" not in encoded


def test_smoke_fails_on_wrong_bot_or_incomplete_probe_matrix() -> None:
    base = dict(
        live_head=lambda: "a" * 40,
        service_snapshot=lambda: {"active": True, "restart_count": 0},
        send_canary=lambda _chat, _thread: 1,
        observe=lambda: {"active": True, "restart_count": 0, "error_count": 0},
    )
    destinations = {"owner_dm": "x", "test_chat": "y", "test_thread": "z"}

    wrong_bot = SmokeDependencies(
        **base,
        get_me=lambda: {"id": 1, "username": "WrongBot"},
        synthetic_probes=lambda: {},
    )
    with pytest.raises(SmokeError, match="identity"):
        run_smoke_checks({"candidate_sha": "a" * 40}, destinations, wrong_bot, True)

    incomplete = SmokeDependencies(
        **base,
        get_me=lambda: {"id": PUBLIC_BOT_ID, "username": PUBLIC_BOT_USERNAME},
        synthetic_probes=lambda: {"member": "allowed"},
    )
    with pytest.raises(SmokeError, match="probe"):
        run_smoke_checks({"candidate_sha": "a" * 40}, destinations, incomplete, True)


def test_sanitize_audit_never_emits_member_ids_text_or_secrets() -> None:
    raw = {
        "member_id": "private-member",
        "chat_id": "private-chat",
        "message_text": "private message body",
        "token": "synthetic-secret",
        "nested": {"user_id": 123, "safe": "pass"},
        "message_id": 55,
        "bot_identity": {"id": PUBLIC_BOT_ID, "username": PUBLIC_BOT_USERNAME},
    }

    cleaned = sanitize_audit(raw)
    encoded = json.dumps(cleaned)

    for forbidden in ("private-member", "private-chat", "private message body", "synthetic-secret"):
        assert forbidden not in encoded
    assert cleaned["message_id"] == 55
    assert cleaned["bot_identity"]["id"] == PUBLIC_BOT_ID


def test_retained_activation_audit_requires_exact_send_once_receipts() -> None:
    manifest = {"candidate_sha": "a" * 40, "service": "synthetic-gateway.service"}
    destinations = {"owner_dm": "owner", "test_chat": "chat", "test_thread": "thread"}
    deps = SmokeDependencies(
        live_head=lambda: "a" * 40,
        service_snapshot=lambda: {"active": True, "restart_count": 0},
        get_me=lambda: {"id": PUBLIC_BOT_ID, "username": PUBLIC_BOT_USERNAME},
        synthetic_probes=lambda: {
            "member": "allowed", "non_member": "denied", "anonymous": "denied",
            "bot": "denied", "callback": "denied", "busy_session": "denied",
        },
        send_canary=lambda _chat, _thread: 101 if _chat == "owner" else 102,
        observe=lambda: {"active": True, "restart_count": 0, "error_count": 0},
    )
    audit = run_smoke_checks(manifest, destinations, deps, send_canaries=True)
    audit.update({
        "approval_scope_sha256": "b" * 64,
        "release_manifest_sha256": "c" * 64,
        "canary_sent": True,
    })
    validate_retained_activation_audit(audit, manifest, destinations, "b" * 64, "c" * 64)

    audit["canary_sent"] = False
    with pytest.raises(SmokeError, match="not exactly bound"):
        validate_retained_activation_audit(audit, manifest, destinations, "b" * 64, "c" * 64)


def test_rollback_process_identity_requires_new_exact_baseline_process(tmp_path: Path) -> None:
    user = pwd.getpwuid(os.getuid())
    group = grp.getgrgid(os.getgid())
    current = {
        "pid": 222,
        "cwd": str(tmp_path),
        "executable": "/usr/bin/python3",
        "cmdline_sha256": "d" * 64,
        "uid": os.getuid(),
        "gid": os.getgid(),
    }
    service_lock = {"user": user.pw_name, "group": group.gr_name}
    validate_rollback_process_identity(
        current, prior_pid=111, expected_executable="/usr/bin/python3",
        expected_cmdline_sha256="d" * 64, repo=tmp_path, service_lock=service_lock,
    )
    with pytest.raises(SmokeError, match="process identity"):
        validate_rollback_process_identity(
            current, prior_pid=222, expected_executable="/usr/bin/python3",
            expected_cmdline_sha256="d" * 64, repo=tmp_path, service_lock=service_lock,
        )


def test_activation_scope_claim_is_global_and_one_shot(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    control_root.mkdir(mode=0o700)
    claim = claim_activation_scope(control_root, "a" * 64, "b" * 40, "c" * 64)
    assert claim.is_file()
    assert claim.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SmokeError, match="already claimed"):
        claim_activation_scope(control_root, "a" * 64, "b" * 40, "c" * 64)


def test_environment_value_reads_only_the_exact_requested_key() -> None:
    entries = [b"OTHER_TOKEN=do-not-use", b"TELEGRAM_BOT_TOKEN=synthetic-token", b""]
    assert _environment_value(entries, "TELEGRAM_BOT_TOKEN") == "synthetic-token"
    assert _environment_value(entries, "TOKEN") is None
    assert _environment_value([b"TELEGRAM_BOT_TOKEN="], "TELEGRAM_BOT_TOKEN") is None


def test_rollback_allowlist_supports_active_plugin_extra_and_fails_closed() -> None:
    telegram = SimpleNamespace(extra={})
    assert validate_rollback_allowlist(telegram, {"allow_from": ["42", "99"]}) == {"42", "99"}
    with pytest.raises(SmokeError, match="baseline admission"):
        validate_rollback_allowlist(telegram, {})
    with pytest.raises(SmokeError, match="baseline admission"):
        validate_rollback_allowlist(telegram, {"allow_from": ["*"]})
    with pytest.raises(SmokeError, match="baseline admission"):
        validate_rollback_allowlist(telegram, {"allow_from": ["1"]})


def test_rollback_allowlist_prefers_explicit_adapter_field() -> None:
    telegram = SimpleNamespace(allow_from=["77"])
    assert validate_rollback_allowlist(telegram, {"allow_from": ["88"]}) == {"77"}
