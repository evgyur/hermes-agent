#!/usr/bin/env python3
"""Build a minimal, private Human20Bot team-operator profile overlay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml


_ALLOWED_OUTPUTS = {"config.overlay.yaml", "MANIFEST.json"}
_ALLOWED_EXTRA_KEYS = {
    "team_authority_chat_id",
    "team_membership_positive_ttl_seconds",
    "team_membership_negative_ttl_seconds",
    "team_membership_max_cache_entries",
}
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b|bearer\s+[A-Za-z0-9._~-]{16,})"
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON input: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return value


def _atomic_private_write(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError(f"output symlink is forbidden: {path}")
    tmp = path.with_name(f".{path.name}.tmp")
    if tmp.exists() or tmp.is_symlink():
        raise ValueError(f"stale temporary output is forbidden: {tmp}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        if tmp.exists() and not tmp.is_symlink():
            tmp.unlink()


def _capability_fingerprint(config: dict[str, Any]) -> str:
    capability = {
        "tools": config.get("tools"),
        "toolsets": config.get("toolsets"),
        "plugins": config.get("plugins"),
        "mcp_servers": config.get("mcp_servers"),
    }
    encoded = json.dumps(capability, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def build_profile_overlay(
    config_path: Path | str,
    out_dir: Path | str,
    approval_path: Path | str,
    redact: bool,
    no_secrets: bool,
) -> dict[str, Any]:
    if not redact:
        raise ValueError("--redact is required")
    if not no_secrets:
        raise ValueError("--no-secrets is required")

    config_path = Path(config_path)
    out_dir = Path(out_dir)
    approval_path = Path(approval_path)
    p02_path = approval_path.parent.parent / "P02-membership-config.json"

    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid config YAML: {config_path}") from exc
    if not isinstance(config, dict):
        raise ValueError("profile config must be a mapping")

    approval = _read_json(approval_path)
    p02 = _read_json(p02_path)
    if (
        approval.get("approval_id") != "APP-002"
        or approval.get("class_name") != "privacy"
        or approval.get("status") != "consumed"
    ):
        raise ValueError("APP-002 consumed privacy approval is required")

    extra_patch = p02.get("extra_patch")
    if not isinstance(extra_patch, dict) or set(extra_patch) != _ALLOWED_EXTRA_KEYS:
        raise ValueError("P02 extra patch contains missing or unexpected keys")
    authority = approval.get("authority_chat_id")
    if not isinstance(authority, str) or not authority:
        raise ValueError("approval authority chat id is missing")
    if extra_patch.get("team_authority_chat_id") != authority:
        raise ValueError("approval authority does not match sealed P02 authority")
    for key in _ALLOWED_EXTRA_KEYS - {"team_authority_chat_id"}:
        value = extra_patch.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"invalid positive integer for {key}")

    telegram = config.get("telegram") or {}
    if not isinstance(telegram, dict):
        raise ValueError("telegram config must be a mapping")
    existing_suppressed = telegram.get("suppress_tool_progress_chats") or []
    if not isinstance(existing_suppressed, list) or not all(
        isinstance(item, (str, int)) for item in existing_suppressed
    ):
        raise ValueError("telegram suppress_tool_progress_chats must be a scalar list")

    overlay = {
        "schema_version": "1.0",
        "telegram": {
            "extra": {key: extra_patch[key] for key in sorted(_ALLOWED_EXTRA_KEYS)},
            "group_sessions_per_user": True,
            "require_mention": False,
            "suppress_tool_progress_chats": sorted(
                {authority, *(str(item) for item in existing_suppressed)}
            ),
            "thread_sessions_per_user": True,
        },
    }
    overlay_text = yaml.safe_dump(overlay, sort_keys=True, allow_unicode=True)
    if _SECRET_VALUE_RE.search(overlay_text):
        raise ValueError("secret-like value detected in overlay")

    if out_dir.is_symlink():
        raise ValueError("output directory symlink is forbidden")
    if out_dir.exists() and not out_dir.is_dir():
        raise ValueError("output path must be a directory")
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    unknown = {entry.name for entry in out_dir.iterdir()} - _ALLOWED_OUTPUTS
    if unknown:
        raise ValueError(f"unexpected existing output entries: {sorted(unknown)}")
    for name in _ALLOWED_OUTPUTS:
        if (out_dir / name).is_symlink():
            raise ValueError(f"output symlink is forbidden: {name}")

    overlay_hash = _sha256_bytes(overlay_text.encode("utf-8"))
    manifest = {
        "schema_version": "1.0",
        "status": "staged",
        "approval_id": "APP-002",
        "redaction": "private values confined to config.overlay.yaml; no credentials copied",
        "no_secrets": True,
        "capability_config_sha256": _capability_fingerprint(config),
        "source_sha256": {
            "config": _sha256_file(config_path),
            "approval": _sha256_file(approval_path),
            "p02_membership_config": _sha256_file(p02_path),
        },
        "output_sha256": {"config.overlay.yaml": overlay_hash},
        "files": ["config.overlay.yaml"],
        "excluded_top_level_keys": [
            "mcp_servers",
            "plugins",
            "providers",
            "toolsets",
            "tools",
        ],
    }
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if _SECRET_VALUE_RE.search(manifest_text):
        raise ValueError("secret-like value detected in manifest")

    _atomic_private_write(out_dir / "config.overlay.yaml", overlay_text)
    _atomic_private_write(out_dir / "MANIFEST.json", manifest_text)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--authority-chat-id-from-approval", type=Path, required=True)
    parser.add_argument("--redact", action="store_true")
    parser.add_argument("--no-secrets", action="store_true")
    args = parser.parse_args()
    result = build_profile_overlay(
        config_path=args.config,
        out_dir=args.out,
        approval_path=args.authority_chat_id_from_approval,
        redact=args.redact,
        no_secrets=args.no_secrets,
    )
    print(
        "Staged overlay contains generic membership policy and quiet defaults, "
        "preserves full tools, and contains no secrets. "
        f"capability_config_sha256={result['capability_config_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
