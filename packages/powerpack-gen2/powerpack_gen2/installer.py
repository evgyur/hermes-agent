"""Atomic bootstrap installer for the boxed Powerpack Gen2 plugin."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from . import doctor


PLUGIN_NAME = "human20-powerpack-gen2"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise RuntimeError("Hermes config must be a YAML mapping")
    return value


def _activate_config(
    config: dict[str, Any],
    variant: str,
    mode: str,
    *,
    h20_keys_available: bool,
) -> None:
    plugins = config.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise RuntimeError("plugins config must be a mapping")
    enabled = plugins.setdefault("enabled", [])
    if not isinstance(enabled, list):
        raise RuntimeError("plugins.enabled must be a list")
    if PLUGIN_NAME not in enabled:
        enabled.append(PLUGIN_NAME)
    entries = plugins.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise RuntimeError("plugins.entries must be a mapping")
    entries[PLUGIN_NAME] = {
        "settings": {
            "mode": mode,
            "variant": variant,
        },
        "allow_tool_override": True,
    }

    toolsets = config.setdefault("toolsets", [])
    if not isinstance(toolsets, list):
        raise RuntimeError("toolsets must be a list")
    if "powerpack-gen2" not in toolsets:
        toolsets.append("powerpack-gen2")

    web = config.setdefault("web", {})
    stt = config.setdefault("stt", {})
    image_gen = config.setdefault("image_gen", {})
    if not all(isinstance(section, dict) for section in (web, stt, image_gen)):
        raise RuntimeError("web, stt, and image_gen config sections must be mappings")
    if h20_keys_available:
        web["search_backend"] = "human20-perplexity"
    elif web.get("search_backend") == "human20-perplexity":
        web.pop("search_backend", None)
    stt["enabled"] = True
    stt["provider"] = "human20-keys-groq"
    stt.setdefault("human20-keys-groq", {})["model"] = "whisper-large-v3"
    image_gen["provider"] = "human20-keys-openai-codex"


def _atomic_write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            yaml.safe_dump(value, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def install(source_root: Path, hermes_home: Path, *, variant: str, mode: str) -> dict[str, Any]:
    """Verify, materialize, configure, and receipt one exact plugin package."""
    source_root = source_root.resolve(strict=True)
    hermes_home = hermes_home.resolve(strict=True)
    errors = doctor.validate_settings({"variant": variant, "mode": mode})
    if errors:
        raise ValueError("; ".join(errors))
    if variant == "owner" and not doctor.owner_mcp_runtime_report()["available"]:
        raise RuntimeError(
            "owner variant requires the Hermes MCP extra with streamable HTTP support"
        )
    inventory = doctor._inventory_report(source_root)
    if inventory.get("status") != "PASS":
        raise RuntimeError("Powerpack source inventory verification failed")

    plugins_root = hermes_home / "plugins"
    plugins_root.mkdir(parents=True, exist_ok=True)
    target = plugins_root / PLUGIN_NAME
    config_path = hermes_home / "config.yaml"
    original_config = config_path.read_bytes() if config_path.exists() else None
    config = _load_config(config_path)
    h20_keys_available = bool(
        os.environ.get("H20_KEYS_BASE_URL") and os.environ.get("H20_KEYS_API_KEY")
    )
    _activate_config(config, variant, mode, h20_keys_available=h20_keys_available)

    stage = Path(tempfile.mkdtemp(prefix=f".{PLUGIN_NAME}.stage-", dir=plugins_root))
    backup = plugins_root / f".{PLUGIN_NAME}.backup-{os.getpid()}"
    moved_old = False
    try:
        shutil.rmtree(stage)
        shutil.copytree(
            source_root,
            stage,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache"),
        )
        if doctor._inventory_report(stage).get("status") != "PASS":
            raise RuntimeError("staged Powerpack inventory verification failed")
        if target.exists():
            if backup.exists():
                raise RuntimeError("stale Powerpack installer backup exists")
            os.replace(target, backup)
            moved_old = True
        os.replace(stage, target)
        _atomic_write_yaml(config_path, config)
    except Exception:
        if target.exists() and moved_old:
            shutil.rmtree(target)
        if moved_old and backup.exists():
            os.replace(backup, target)
        if original_config is None:
            if config_path.exists():
                config_path.unlink()
        else:
            temporary = config_path.with_name(f".{config_path.name}.restore-{os.getpid()}")
            temporary.write_bytes(original_config)
            os.replace(temporary, config_path)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    if backup.exists():
        shutil.rmtree(backup)

    manifest = doctor.load_manifest(target)
    try:
        from hermes_cli import __version__ as hermes_version
    except Exception:
        hermes_version = "unknown"
    receipt = {
        "schema_version": 1,
        "status": "installed",
        "plugin": PLUGIN_NAME,
        "version": manifest["version"],
        "powerpack_version": manifest["version"],
        "hermes_version": str(hermes_version),
        "variant": variant,
        "mode": mode,
        "package_sha256": doctor._inventory_report(target)["aggregate_sha256"],
        "inventory_sha256": _sha256(target / doctor.INVENTORY_PATH),
        "installed_at_unix": int(time.time()),
        "secrets_persisted": False,
    }
    receipt_root = hermes_home / "powerpack" / "receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_root / f"{PLUGIN_NAME}-install.json"
    temporary = receipt_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, receipt_path)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Human20 Powerpack Gen2 over Hermes")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--variant", choices=sorted(doctor.VALID_VARIANTS), default="employee")
    parser.add_argument("--mode", choices=sorted(doctor.VALID_MODES), default="gen2_only")
    args = parser.parse_args()
    print(json.dumps(install(args.source_root, args.hermes_home, variant=args.variant, mode=args.mode), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
