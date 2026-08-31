"""Non-conflicting ``hermes power`` and ``/powerpack`` surfaces."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .doctor import run_doctor


PIN = "3783fd9ffeada5bee050326f6f96360b6e213d6a"
MANAGED_STT_PROVIDER = "human20-keys-groq"


def hermes_version() -> str:
    """Return the independently versioned Hermes core release."""
    try:
        from hermes_cli import __version__

        return str(__version__)
    except Exception:
        return "unknown"


def _configure_managed_stt() -> None:
    """Select the Powerpack STT baseline without touching unrelated config."""
    from hermes_cli.config import set_config_value

    set_config_value("stt.enabled", "true")
    set_config_value("stt.provider", MANAGED_STT_PROVIDER)
    set_config_value("stt.language", "")
    set_config_value(f"stt.{MANAGED_STT_PROVIDER}.model", "whisper-large-v3", force=True)


def _persist_plugin_setting(ctx, key: str, value: str) -> None:
    setter = getattr(ctx, "set_config", None)
    if callable(setter):
        setter(key, value)
        return
    from hermes_cli.config import set_config_value

    set_config_value(f"plugins.entries.human20-powerpack-gen2.{key}", value, force=True)


def setup_parser(parser, root: Path, variant: str, mode: str) -> None:
    sub = parser.add_subparsers(dest="power_command")
    install = sub.add_parser("install", help="Select and validate a Powerpack variant")
    install.add_argument("variant", choices=["rentals", "employee", "owner"])
    install.add_argument("--mode", choices=["disabled", "compatibility", "gen2_only"], default="compatibility")
    install.set_defaults(power_root=str(root), power_variant=variant, power_mode=mode)
    status = sub.add_parser("status", help="Show package status")
    status.set_defaults(power_root=str(root), power_variant=variant, power_mode=mode)
    doctor = sub.add_parser("doctor", help="Validate package and managed runtimes")
    doctor.add_argument("--ci", action="store_true")
    doctor.add_argument("--host", action="store_true", help="Run host/runtime certification checks")
    doctor.add_argument("--service", default="human20team-hermes-gateway.service")
    doctor.add_argument("--repo-root")
    doctor.add_argument("--expected-user", default="human20team")
    doctor.add_argument("--active-plugin-root")
    doctor.add_argument("--hermes-home")
    doctor.add_argument("--upstream-sha", default=os.environ.get("HERMES_UPSTREAM_SHA", PIN))
    doctor.set_defaults(power_root=str(root), power_variant=variant, power_mode=mode)


def _write_receipt(root: Path, variant: str, mode: str, package_sha256: str) -> Path:
    configured_home = os.environ.get("HERMES_HOME")
    hermes_home = Path(configured_home) if configured_home else Path.home() / ".hermes"
    receipts = hermes_home / "powerpack" / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    path = receipts / "human20-powerpack-gen2-install.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": "human20-powerpack-gen2",
                "version": doctor_version(root),
                "powerpack_version": doctor_version(root),
                "hermes_version": hermes_version(),
                "variant": variant,
                "mode": mode,
                "package_sha256": package_sha256,
                "installed_at_unix": int(time.time()),
                "secrets_persisted": False,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def handle_cli(args, root: Path, variant: str, mode: str, *, ctx=None) -> int:
    command = getattr(args, "power_command", None)
    if command == "install":
        target = str(getattr(args, "variant", ""))
        target_mode = str(getattr(args, "mode", "compatibility"))
        report = run_doctor(root=root, variant=target, mode=target_mode, upstream_sha=PIN, ci=True)
        if not report["ok"]:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 1
        if ctx is None:
            print("Powerpack plugin context unavailable; variant was not persisted")
            return 1
        _persist_plugin_setting(ctx, "variant", target)
        _persist_plugin_setting(ctx, "mode", target_mode)
        if target_mode == "gen2_only":
            _configure_managed_stt()
        receipt = _write_receipt(root, target, target_mode, report["package_sha256"])
        print(json.dumps({"ok": True, "variant": target, "mode": target_mode, "receipt": str(receipt), "restart_required": target != variant or target_mode != mode}, sort_keys=True))
        return 0
    if command == "status":
        print(json.dumps({
            "plugin": "human20-powerpack-gen2",
            "version": doctor_version(root),
            "powerpack_version": doctor_version(root),
            "hermes_version": hermes_version(),
            "variant": variant,
            "mode": mode,
        }, sort_keys=True))
        return 0
    if command == "doctor":
        host = bool(getattr(args, "host", False))
        report = run_doctor(
            root=root,
            variant=variant,
            mode=mode,
            upstream_sha=getattr(args, "upstream_sha", PIN),
            ci=bool(getattr(args, "ci", False)),
            host=host,
            repo_root=Path(str(getattr(args, "repo_root", "") or Path.cwd())) if host else None,
            service=str(getattr(args, "service", "human20team-hermes-gateway.service")),
            expected_user=str(getattr(args, "expected_user", "human20team")),
            active_plugin_root=Path(str(getattr(args, "active_plugin_root", "") or root)) if host else None,
            hermes_home=Path(str(getattr(args, "hermes_home", "") or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")) if host else None,
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["ok"] else 1
    print("Usage: hermes powerpack-gen2 <status|doctor>")
    return 2


def slash_status(root: Path, variant: str, mode: str, raw: str) -> str:
    if raw.strip() == "doctor":
        report = run_doctor(root=root, variant=variant, mode=mode, upstream_sha=PIN, ci=True)
        return json.dumps(report, ensure_ascii=False, sort_keys=True)
    return (
        f"Powerpack Gen2 v{doctor_version(root)} · Hermes v{hermes_version()}; "
        f"mode={mode}; variant={variant}; use /powerpack-gen2 doctor for offline diagnostics."
    )


def doctor_version(root: Path) -> str:
    return str(json.loads((root / "metadata" / "powerpack-gen2.json").read_text(encoding="utf-8"))["version"])
