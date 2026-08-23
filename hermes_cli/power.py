"""Hermes Power Setup helpers.

This module provides the unified private Powerpack installer.  Rentals and
employee installs share one runtime and one curated skill payload; employee
installs add only customer-scoped Human20 Keys routing.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hermes_cli.config import get_hermes_home, get_project_root, load_config, save_config
from hermes_cli.power_variants import (
    DEFAULT_H20_BASE_URL,
    VARIANTS,
    install_rentals_bundle,
    load_employee_overlay,
    normalize_h20_base_url,
    save_employee_credentials,
    validate_h20_identity,
)


EXCLUDED_DEFAULT_MODULES = frozenset({"tg", "postcraft"})

POWER_AUTONOMY_PROMPT = """You are a result-oriented Hermes assistant. Treat an authenticated direct request or approved plan as one bounded Task Authorization Envelope covering its goal, owned object, allowed surfaces/effects, exclusions, risk caps, rollback, and completion proof. Inside an unchanged envelope, continue through implementation, tests, reviewer fixes, same-scope candidate regeneration, refreshed evidence, commit/push when in scope, rollback-capable deploy/restart, smoke/rollback validation, retries, and successful callback re-entry without another GO. Candidate SHA or manifest changes invalidate candidate-bound evidence and technical receipts, not plan authority. Stage boundaries and successful callbacks are not user-facing stop points. Ask once only for a real target/surface/effect/risk expansion, an excluded protected effect, explicit revocation, loss of rollback, or missing irretrievable input. Protected effects include money/external spend, DNS/network/TLS, secrets or a new trust boundary, new human access, destructive production data, mass/public messaging, customer-facing price/entitlement changes, and non-user-owned mutation. Require fresh evidence from the actual consumer before claiming completion."""

POWER_DEFAULT_MODULES: tuple[str, ...] = (
    "voice-studio",
    "vision-doc-intake",
    "image-studio",
    "piapi-video-generation",
    "model-profile-switcher",
    "power-compat-commands",
    "telegram-user-bridge",
    "browser-relay",
    "media-intake",
    "ops-doctor",
    "plan-authorized-execution",
    "memory-skill-hygiene",
    "reasoning-personas",
    "mcp-webhook-starter",
)

POWER_OPTIONAL_MODULES: tuple[str, ...] = (
    "video-edit",
    "operator-briefing",
    "cron-digests",
    "creator-editorial-pack",  # optional; intentionally not tg/postcraft by name
)

POWER_SMOKE_SURFACES: tuple[dict[str, Any], ...] = (
    {
        "id": "plan_authorization",
        "label": "Plan authorization",
        "module": "plan-authorized-execution",
        "config_paths": ["display.personality=power", "agent.personalities.power"],
        "doctor_check": "Plan authorization",
        "requires_private_key_in_template": False,
    },
    {
        "id": "stt",
        "label": "STT",
        "module": "voice-studio",
        "config_paths": ["stt.enabled", "stt.provider"],
        "doctor_check": "STT",
        "requires_private_key_in_template": False,
    },
    {
        "id": "tts",
        "label": "TTS",
        "module": "voice-studio",
        "config_paths": ["tts.provider", "voice.auto_tts"],
        "doctor_check": "TTS",
        "requires_private_key_in_template": False,
    },
    {
        "id": "auxiliary_vision",
        "label": "Auxiliary vision",
        "module": "vision-doc-intake",
        "config_paths": ["agent.image_input_mode", "auxiliary.vision.provider", "auxiliary.vision.model"],
        "doctor_check": "Auxiliary vision",
        "requires_private_key_in_template": False,
    },
    {
        "id": "image_generation",
        "label": "Image generation",
        "module": "image-studio",
        "config_paths": ["toolsets[]:image_gen", "plugins/image_gen"],
        "doctor_check": "Image generation",
        "requires_private_key_in_template": False,
    },
    {
        "id": "video_generation",
        "label": "Video generation (PiAPI)",
        "module": "piapi-video-generation",
        "config_paths": ["video_generation.provider=piapi", "skills/media/piapi-video-toolkit", "PIAPI_API_KEY", "ffmpeg", "ffprobe"],
        "doctor_check": "PiAPI video generation",
        "requires_private_key_in_template": False,
    },
)

PUBLIC_REPO_CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "name": "profile-switcher-pack",
        "repo": "https://github.com/example/profile-switcher-pack",
        "classification": "public-powerpack",
        "notes": "Genericize profiles before bundling; never commit OAuth tokens.",
    },
    {
        "name": "operator-briefing-style-pack",
        "repo": "https://github.com/example/operator-briefing-style-pack",
        "classification": "optional-public-style-pack",
        "notes": "Rename/position as operator-briefing; do not make it default voice.",
    },
    {
        "name": "bot-cooperation-pack",
        "repo": "https://github.com/example/bot-cooperation-pack",
        "classification": "public-powerpack",
        "notes": "Useful for Telegram bot cooperation and mention/reply routing.",
    },
    {
        "name": "read-only-sensing-pack",
        "repo": "https://github.com/example/read-only-sensing-pack",
        "classification": "optional-public-sensing",
        "notes": "Keep read-only and remove private source assumptions.",
    },
)

PRIVATE_OVERLAY_EXAMPLES: tuple[str, ...] = (
    "Product business logic, CTA rules, channel ids, and ban-word lists",
    "Operator user ids, Telegram topics/chats, account defaults, and delivery rails",
    "OAuth access/refresh tokens, auth.json, .env files, and *.session files",
    "Private memory, operating-system, project-runtime state, source feeds, and internal dashboards",
    "tg/postcraft editorial workflows unless explicitly installed as a separate optional pack",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # ok | warn | fail
    detail: str


# Conservative secret patterns for public artifacts.  This is not a replacement
# for a real scanner, but it catches the common mistakes in templates/docs.
SECRET_PATTERNS: tuple[str, ...] = (
    r"sk-[A-Za-z0-9]{20,}",
    r"gh[opsu]_[A-Za-z0-9_]{30,}",
    r"xox[baprs]-[A-Za-z0-9-]{20,}",
    r"[0-9]{8,10}:[A-Za-z0-9_-]{30,}",  # Telegram bot token
    r"\"refresh_token\"\s*:\s*\"[^\"<]{20,}\"",
    r"\"access_token\"\s*:\s*\"eyJ[A-Za-z0-9_-]{40,}",
)


def _has_module(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _env_present(*names: str) -> bool:
    return any(bool(os.getenv(name)) for name in names)


def _status_icon(status: str) -> str:
    return {"ok": "✓", "warn": "⚠", "fail": "✗"}.get(status, "•")


def _merge_dict(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_dict(base[key], value)
        else:
            base[key] = value
    return base


def _snapshot_file(path: Path) -> tuple[bool, bytes, int]:
    if not path.exists():
        return False, b"", 0o600
    return True, path.read_bytes(), path.stat().st_mode & 0o777


def _restore_file(path: Path, snapshot: tuple[bool, bytes, int]) -> None:
    existed, payload, mode = snapshot
    if not existed:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def power_preset_defaults() -> dict[str, Any]:
    """Return the public-safe MVP defaults applied by `hermes power install`.

    These defaults are deliberately mild: they enable voice/vision-capable
    config paths and quick commands, but do not write secrets and do not enable
    private overlay modules.
    """

    return {
        "stt": {
            "enabled": True,
            "provider": "local",
            "local": {"model": "base", "language": ""},
        },
        "tts": {
            "provider": "edge",
            "edge": {"voice": "en-US-AriaNeural"},
        },
        "voice": {
            "auto_tts": False,
            "max_recording_seconds": 120,
        },
        "agent": {
            "image_input_mode": "auto",
            "personalities": {
                "power": {
                    "description": "One plan-level authorization; autonomous execution to verified result",
                    "system_prompt": POWER_AUTONOMY_PROMPT,
                    "tone": "direct, concise, result-first",
                    "style": "Report verified results or one precise blocker.",
                }
            },
        },
        "display": {"personality": "power"},
        "auxiliary": {
            "vision": {
                "provider": "auto",
                "model": "",
                "timeout": 120,
                "download_timeout": 30,
            }
        },
        "video_generation": {
            "provider": "piapi",
            "piapi": {
                "api_key_env": "PIAPI_API_KEY",
                "default_model": "seedance-2-fast-preview",
                "workflow": "generate-remove-watermark-download",
            },
        },
        "toolsets": [
            "hermes-cli",
            "terminal",
            "file",
            "web",
            "browser",
            "vision",
            "image_gen",
            "video",
            "tts",
            "skills",
            "memory",
            "session_search",
            "cronjob",
            "delegation",
        ],
        "quick_commands": {
            "gptprof": {
                "type": "exec",
                "command": "python3 -m hermes_cli.power_quick gptprof",
                "append_args": True,
            },
            "mmfast": {
                "type": "alias",
                "target": "/model MiniMax-M2.7 --provider minimax --global",
            },
            "gptt": {
                "type": "alias",
                "target": "/model gpt-5.5 --provider openai-codex --global",
            },
            "xai": {
                "type": "alias",
                "target": "/model grok-4.3 --provider xai-oauth --global",
            },
            "say": {
                "type": "exec",
                "command": "python3 -m hermes_cli.power_quick say",
                "append_args": True,
            },
            "img": {
                "type": "exec",
                "command": "python3 -m hermes_cli.power_quick img",
                "append_args": True,
            },
            "video": {
                "type": "exec",
                "command": "python3 -m hermes_cli.power_quick video",
                "append_args": True,
            },
        },
        "power": {
            "enabled": True,
            "preset": "default",
            "modules": list(POWER_DEFAULT_MODULES),
            "bundled_skills": [
                "piapi-video-toolkit",
                "reasoning-personas",
                "rp",
                "decision",
                "superpowers",
                "shaw",
            ],
            "private_overlay_required": False,
            "smoke_surfaces": [surface["id"] for surface in POWER_SMOKE_SURFACES],
        },
    }


def build_inventory() -> dict[str, Any]:
    modules = list(POWER_DEFAULT_MODULES)
    excluded = sorted(EXCLUDED_DEFAULT_MODULES & set(modules))
    return {
        "default_modules": modules,
        "optional_modules": list(POWER_OPTIONAL_MODULES),
        "excluded_from_default": sorted(EXCLUDED_DEFAULT_MODULES),
        "default_exclusion_violations": excluded,
        "smoke_surfaces": list(POWER_SMOKE_SURFACES),
        "public_repo_candidates": list(PUBLIC_REPO_CANDIDATES),
        "private_overlay_examples": list(PRIVATE_OVERLAY_EXAMPLES),
        "rules": [
            "Default pack must not include tg or postcraft.",
            "Public artifacts must use placeholders for ids, tokens, emails, and sessions.",
            "Private overlay must be non-required for a fresh public install.",
            "Do not push/public publish from this command; it only writes local config when explicitly installed.",
        ],
    }


def run_inventory(json_output: bool = False) -> int:
    inventory = build_inventory()
    if json_output:
        print(json.dumps(inventory, ensure_ascii=False, indent=2))
        return 1 if inventory["default_exclusion_violations"] else 0

    print("Hermes Power Setup inventory")
    print()
    print("Default modules:")
    for module in inventory["default_modules"]:
        print(f"  - {module}")
    print()
    print("Excluded from default:")
    for module in inventory["excluded_from_default"]:
        print(f"  - {module}")
    if inventory["default_exclusion_violations"]:
        print("\nFAIL: excluded modules are present in the default pack")
        return 1
    print("\nOK: tg/postcraft are not in the default pack")
    return 0


def collect_power_checks() -> list[CheckResult]:
    cfg = load_config()
    checks: list[CheckResult] = []

    inventory = build_inventory()
    if inventory["default_exclusion_violations"]:
        checks.append(CheckResult("default module boundary", "fail", "tg/postcraft present in default pack"))
    else:
        checks.append(CheckResult("default module boundary", "ok", "tg/postcraft excluded"))

    agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent"), dict) else {}
    personalities = agent_cfg.get("personalities", {}) if isinstance(agent_cfg.get("personalities"), dict) else {}
    power_personality = personalities.get("power", {}) if isinstance(personalities.get("power"), dict) else {}
    power_prompt = str(power_personality.get("system_prompt", ""))
    display_cfg = cfg.get("display", {}) if isinstance(cfg.get("display"), dict) else {}
    selected_personality = str(display_cfg.get("personality", "") or "").strip().lower()
    autonomy_markers = (
        "Task Authorization Envelope",
        "without another GO",
        "invalidate candidate-bound evidence",
        "not plan authority",
    )
    power_cfg = cfg.get("power", {}) if isinstance(cfg.get("power"), dict) else {}
    if all(marker in power_prompt for marker in autonomy_markers):
        detail = "power personality installed"
        if selected_personality == "power":
            detail += " and active"
        else:
            detail += f"; active personality is {selected_personality or 'none'}"
        checks.append(CheckResult("Plan authorization", "ok", detail))
    elif power_cfg.get("enabled") is True:
        checks.append(CheckResult("Plan authorization", "fail", "power personality is missing the plan-level authorization contract"))
    else:
        checks.append(CheckResult("Plan authorization", "warn", "run `hermes power install` to install the plan-level authorization contract"))

    stt_cfg = cfg.get("stt", {}) if isinstance(cfg.get("stt"), dict) else {}
    stt_enabled = stt_cfg.get("enabled", True)
    stt_provider = stt_cfg.get("provider", "local")
    if not stt_enabled:
        checks.append(CheckResult("STT", "warn", "disabled in config"))
    elif stt_provider == "local":
        if _has_module("faster_whisper") or shutil.which("whisper"):
            checks.append(CheckResult("STT", "ok", "local provider available"))
        elif _env_present("GROQ_API_KEY", "VOICE_TOOLS_OPENAI_KEY", "MISTRAL_API_KEY"):
            checks.append(CheckResult("STT", "warn", "local faster-whisper missing; cloud STT key present for fallback"))
        else:
            checks.append(CheckResult("STT", "warn", "install faster-whisper or set GROQ_API_KEY/VOICE_TOOLS_OPENAI_KEY/MISTRAL_API_KEY"))
    elif stt_provider == "groq":
        checks.append(CheckResult("STT", "ok" if _env_present("GROQ_API_KEY") else "warn", "provider=groq"))
    elif stt_provider == "openai":
        checks.append(CheckResult("STT", "ok" if _env_present("VOICE_TOOLS_OPENAI_KEY", "OPENAI_API_KEY") else "warn", "provider=openai"))
    elif stt_provider == "mistral":
        checks.append(CheckResult("STT", "ok" if _env_present("MISTRAL_API_KEY") else "warn", "provider=mistral"))
    else:
        checks.append(CheckResult("STT", "warn", f"unknown provider={stt_provider}"))

    tts_cfg = cfg.get("tts", {}) if isinstance(cfg.get("tts"), dict) else {}
    tts_provider = tts_cfg.get("provider", "edge")
    if tts_provider == "edge":
        checks.append(CheckResult("TTS", "ok", "Edge TTS selected (free default)"))
    elif tts_provider == "elevenlabs":
        checks.append(CheckResult("TTS", "ok" if _env_present("ELEVENLABS_API_KEY") else "warn", "provider=elevenlabs"))
    elif tts_provider == "openai":
        checks.append(CheckResult("TTS", "ok" if _env_present("VOICE_TOOLS_OPENAI_KEY", "OPENAI_API_KEY") else "warn", "provider=openai"))
    elif tts_provider == "minimax":
        checks.append(CheckResult("TTS", "ok" if _env_present("MINIMAX_API_KEY", "MINIMAX_CN_API_KEY") else "warn", "provider=minimax"))
    elif tts_provider == "mistral":
        checks.append(CheckResult("TTS", "ok" if _env_present("MISTRAL_API_KEY") else "warn", "provider=mistral"))
    else:
        checks.append(CheckResult("TTS", "warn", f"provider={tts_provider}; verify manually"))

    aux = cfg.get("auxiliary", {}) if isinstance(cfg.get("auxiliary"), dict) else {}
    vision_cfg = aux.get("vision", {}) if isinstance(aux.get("vision"), dict) else {}
    vision_provider = vision_cfg.get("provider", "auto")
    if vision_provider in ("auto", ""):
        if _env_present("OPENROUTER_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "MINIMAX_API_KEY", "OPENAI_API_KEY"):
            checks.append(CheckResult("Auxiliary vision", "ok", "auto provider has at least one likely vision backend key"))
        else:
            checks.append(CheckResult("Auxiliary vision", "warn", "auto provider set; add OPENROUTER/GOOGLE/MINIMAX/OpenAI credentials or configure auxiliary.vision"))
    elif vision_provider == "minimax":
        checks.append(CheckResult("Auxiliary vision", "ok" if _env_present("MINIMAX_API_KEY", "MINIMAX_CN_API_KEY") else "warn", "provider=minimax; run a real image smoke before relying on it"))
    else:
        checks.append(CheckResult("Auxiliary vision", "ok", f"provider={vision_provider}"))

    if (get_project_root() / "plugins" / "image_gen").exists():
        checks.append(CheckResult("Image generation", "ok", "image_gen plugin directory present"))
    else:
        checks.append(CheckResult("Image generation", "warn", "image_gen plugins not found"))

    piapi_skill = get_project_root() / "skills" / "media" / "piapi-video-toolkit" / "SKILL.md"
    if piapi_skill.exists():
        if _env_present("PIAPI_API_KEY"):
            detail = "PiAPI skill present and PIAPI_API_KEY configured for video generation"
            status = "ok"
        else:
            detail = "PiAPI skill present; set PIAPI_API_KEY locally for actual video generation"
            status = "warn"
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            detail += "; install ffmpeg/ffprobe for post-processing smoke"
            status = "warn"
        checks.append(CheckResult("PiAPI video generation", status, detail))
    else:
        checks.append(CheckResult("PiAPI video generation", "warn", "bundled PiAPI video generation skill not found"))

    if shutil.which("tesseract") or _has_module("fitz") or _has_module("pymupdf"):
        checks.append(CheckResult("OCR/docs", "ok", "OCR/document extraction dependency present"))
    else:
        checks.append(CheckResult("OCR/docs", "warn", "install tesseract and/or pymupdf for doc intake"))

    if shutil.which("git"):
        checks.append(CheckResult("Release hygiene", "ok", "git available for inventory/secret scans"))
    else:
        checks.append(CheckResult("Release hygiene", "warn", "git not found"))

    return checks


def run_doctor(json_output: bool = False) -> int:
    checks = collect_power_checks()
    if json_output:
        print(json.dumps([check.__dict__ for check in checks], ensure_ascii=False, indent=2))
    else:
        print("Hermes Power Setup doctor")
        print()
        for check in checks:
            print(f"  {_status_icon(check.status)} {check.name}: {check.detail}")
        print()
        print("No public push is performed by power doctor/install.")

    return 1 if any(check.status == "fail" for check in checks) else 0


def apply_power_preset(dry_run: bool = False, preset: str = "rentals") -> dict[str, Any]:
    current = load_config()
    if preset == "default":
        preset = "rentals"
    if preset not in VARIANTS:
        raise ValueError(f"Unknown Powerpack variant: {preset}")
    preset_config = power_preset_defaults()
    current_display = current.get("display", {}) if isinstance(current.get("display"), dict) else {}
    current_personality = str(current_display.get("personality", "") or "").strip().lower()
    if current_personality not in {"", "none", "default", "neutral"}:
        preset_config.pop("display", None)
    power_config = preset_config.setdefault("power", {})
    power_config["preset"] = preset
    if preset == "employee":
        _merge_dict(preset_config, load_employee_overlay(get_project_root()))
    updated = _merge_dict(dict(current), preset_config)
    if not dry_run:
        save_config(updated)
    return updated


def run_install(args: Any) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    json_output = bool(getattr(args, "json", False))
    preset = str(getattr(args, "preset", "rentals") or "rentals")
    if preset == "default":
        preset = "rentals"
    base_url = normalize_h20_base_url(getattr(args, "h20_base_url", DEFAULT_H20_BASE_URL))
    identity: dict[str, Any] | None = None
    customer_key = ""
    if preset == "employee" and not dry_run:
        expected_customer_id = str(getattr(args, "employee_id", "") or "").strip()
        if not expected_customer_id:
            raise ValueError("Employee install requires --employee-id")
        customer_key = os.environ.get("H20_KEYS_API_KEY", "").strip()
        if not customer_key and bool(getattr(args, "h20_key_stdin", False)):
            customer_key = sys.stdin.readline().strip()
        if not customer_key:
            raise ValueError(
                "Employee install requires H20_KEYS_API_KEY or --h20-key-stdin"
            )
        identity = validate_h20_identity(
            customer_key,
            base_url=base_url,
            expected_customer_id=expected_customer_id,
        )

    home = get_hermes_home()
    config_snapshot = _snapshot_file(home / "config.yaml")
    env_snapshot = _snapshot_file(home / ".env")
    try:
        updated = apply_power_preset(dry_run=dry_run, preset=preset)
        bundle = None if dry_run else install_rentals_bundle(get_project_root(), home)
        credential_names: list[str] = []
        if preset == "employee" and not dry_run:
            credential_names = save_employee_credentials(customer_key, base_url)
    except Exception:
        if not dry_run:
            _restore_file(home / "config.yaml", config_snapshot)
            _restore_file(home / ".env", env_snapshot)
        raise
    result = {
        "preset": preset,
        "dry_run": dry_run,
        "modules": list(POWER_DEFAULT_MODULES),
        "excluded": sorted(EXCLUDED_DEFAULT_MODULES),
        "config_path": str(home / "config.yaml"),
        "toolsets": updated.get("toolsets", []),
        "rentals_skill_roots": [] if bundle is None else bundle["skill_roots"],
        "employee_identity": identity,
        "credential_names": credential_names,
    }
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        action = "Would update" if dry_run else "Updated"
        print(f"{action} Hermes config for Power Setup preset: {preset}")
        print(f"Config: {result['config_path']}")
        print("Modules:")
        for module in POWER_DEFAULT_MODULES:
            print(f"  - {module}")
        print("Excluded from default: tg, postcraft")
        if bundle is not None:
            print(f"Installed rentals skill roots: {len(bundle['skill_roots'])}")
        if identity is not None:
            print(f"Validated H20 customer: {identity['customer_id']}")
            print("Stored credential names: " + ", ".join(credential_names))
        print("Next: hermes power doctor")
    return 0


def run_secret_scan(paths: list[str] | None = None, json_output: bool = False) -> int:
    import re

    root = get_project_root()
    if not paths:
        paths = [
            "docs/power-setup.md",
            "templates/configs",
            "hermes_cli/power.py",
            "scripts/smoke-power-setup.sh",
            "skills/media/piapi-video-toolkit",
        ]
    compiled = [re.compile(pattern) for pattern in SECRET_PATTERNS]
    findings: list[dict[str, Any]] = []
    for rel in paths:
        path = (root / rel).resolve()
        candidates: list[Path]
        if path.is_dir():
            candidates = [p for p in path.rglob("*") if p.is_file()]
        elif path.exists():
            candidates = [path]
        else:
            continue
        for candidate in candidates:
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                for pattern in compiled:
                    if pattern.search(line):
                        findings.append({"path": str(candidate.relative_to(root)), "line": line_no, "pattern": pattern.pattern})
                        break
    if json_output:
        print(json.dumps({"findings": findings}, ensure_ascii=False, indent=2))
    else:
        if findings:
            print("Potential secrets found:")
            for item in findings:
                print(f"  - {item['path']}:{item['line']} ({item['pattern']})")
        else:
            print("Power public artifact secret scan: OK")
    return 1 if findings else 0


def cmd_power(args: Any) -> None:
    action = getattr(args, "power_action", None) or "doctor"
    if action == "doctor":
        raise SystemExit(run_doctor(json_output=bool(getattr(args, "json", False))))
    if action == "inventory":
        raise SystemExit(run_inventory(json_output=bool(getattr(args, "json", False))))
    if action == "install":
        raise SystemExit(run_install(args))
    if action == "secret-scan":
        raise SystemExit(run_secret_scan(paths=getattr(args, "paths", None), json_output=bool(getattr(args, "json", False))))
    if action == "list":
        raise SystemExit(run_inventory(json_output=False))
    print("Unknown power action. Use: hermes power doctor|inventory|install|secret-scan|list", file=sys.stderr)
    raise SystemExit(2)


def run_power_setup(args: Any) -> None:
    """Entry point for `hermes setup power`.

    In non-interactive contexts this applies the public-safe defaults directly.
    For interactive setup we still keep it deterministic for now: print what is
    being written and apply the same default preset. Future phases can add a TUI
    picker without changing the command contract.
    """

    dry_run = bool(getattr(args, "dry_run", False))

    class _InstallArgs:
        json = False

    install_args = _InstallArgs()
    install_args.dry_run = dry_run
    run_install(install_args)


def add_power_parser(subparsers: Any) -> Any:
    parser = subparsers.add_parser(
        "power",
        help="Install and diagnose the Hermes Power Setup",
        description="Public-safe multimodal setup: voice, vision, image/video, model switching, and ops checks.",
    )
    power_sub = parser.add_subparsers(dest="power_action")

    doctor = power_sub.add_parser("doctor", help="Run Power Setup readiness checks")
    doctor.add_argument("--json", action="store_true", help="Emit JSON")

    inventory = power_sub.add_parser("inventory", help="Show public/private module boundary")
    inventory.add_argument("--json", action="store_true", help="Emit JSON")

    power_sub.add_parser("list", help="List default Power Setup modules")

    install = power_sub.add_parser("install", help="Install the unified rentals or employee Powerpack variant")
    install.add_argument("preset", nargs="?", default="rentals", choices=["default", *VARIANTS], help="Variant name (default is a compatibility alias for rentals)")
    install.add_argument("--dry-run", action="store_true", help="Show what would be changed without writing config")
    install.add_argument("--json", action="store_true", help="Emit JSON")
    install.add_argument("--employee-id", help="Expected H20 customer id; required for employee installs")
    install.add_argument("--h20-key-stdin", action="store_true", help="Read the H20 customer key from stdin without exposing it in argv")
    install.add_argument("--h20-base-url", default=DEFAULT_H20_BASE_URL, help="Human20 Keys customer base URL")

    scan = power_sub.add_parser("secret-scan", help="Scan public Power Setup artifacts for common secret patterns")
    scan.add_argument("paths", nargs="*", help="Optional repo-relative files/directories to scan")
    scan.add_argument("--json", action="store_true", help="Emit JSON")

    parser.set_defaults(func=cmd_power)
    return parser
