"""Variant helpers for the unified private Hermes Powerpack.

`rentals` and `employee` share the same runtime and curated skill bundle.
The employee overlay differs only in customer-scoped Human20 Keys routing.
Secrets are accepted only from stdin or the process environment and are never
printed, serialized into templates, or committed.
"""
from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

VARIANTS = ("rentals", "employee")
DEFAULT_H20_BASE_URL = "https://keys.human20.app"
_EMPLOYEE_SECRET_ENV_NAMES = (
    "H20_KEYS_API_KEY",
    "PERPLEXITY_API_KEY",
    "GROQ_API_KEY",
)


def load_employee_overlay(project_root: Path) -> dict[str, Any]:
    path = project_root / "templates" / "configs" / "power-employee.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Employee overlay must be a YAML mapping: {path}")
    return payload


def install_rentals_bundle(project_root: Path, hermes_home: Path) -> dict[str, Any]:
    """Install the single curated skills payload used by both variants."""
    source = project_root / "bundles" / "rentals" / "skills"
    if not source.is_dir():
        raise FileNotFoundError(f"Rentals bundle is missing: {source}")
    target = hermes_home / "skills"
    target.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for skill_root in sorted(path for path in source.iterdir() if path.is_dir()):
        shutil.copytree(skill_root, target / skill_root.name, dirs_exist_ok=True)
        installed.append(skill_root.name)
    return {"source": str(source), "target": str(target), "skill_roots": installed}


def install_employee_bundle(project_root: Path, hermes_home: Path) -> dict[str, Any]:
    """Install capabilities that are available only to employee tenants."""
    source = project_root / "bundles" / "employee" / "skills"
    if not source.is_dir():
        raise FileNotFoundError(f"Employee bundle is missing: {source}")
    target = hermes_home / "skills"
    target.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for skill_root in sorted(path for path in source.iterdir() if path.is_dir()):
        shutil.copytree(skill_root, target / skill_root.name, dirs_exist_ok=True)
        installed.append(skill_root.name)
    return {"source": str(source), "target": str(target), "skill_roots": installed}


def normalize_h20_base_url(value: str | None) -> str:
    base = (value or DEFAULT_H20_BASE_URL).strip().rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://127.0.0.1:"):
        raise ValueError("H20 base URL must use HTTPS (loopback HTTP is allowed for host-local installs)")
    return base


def validate_h20_identity(
    customer_key: str,
    *,
    base_url: str = DEFAULT_H20_BASE_URL,
    expected_customer_id: str | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """Validate a customer key against /v1/me without exposing the key."""
    key = customer_key.strip()
    if not key:
        raise ValueError("Employee install requires a customer-scoped H20 key")
    base = normalize_h20_base_url(base_url)
    request = urllib.request.Request(
        f"{base}/v1/me",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"H20 identity validation failed with HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f"H20 identity validation failed: {type(exc).__name__}") from None
    if not isinstance(payload, dict):
        raise ValueError("H20 identity response was not an object")
    customer_id = str(payload.get("customer_id") or payload.get("id") or "").strip()
    if not customer_id:
        raise ValueError("H20 identity response omitted customer_id")
    expected = (expected_customer_id or "").strip()
    if expected and customer_id != expected:
        raise ValueError("H20 key belongs to a different customer")
    return {
        "customer_id": customer_id,
        "status": payload.get("status"),
        "providers": payload.get("providers") or payload.get("provider_allowlist") or [],
    }


def employee_env_values(customer_key: str, base_url: str) -> dict[str, str]:
    """Return one-key compatibility aliases consumed by existing Hermes tools."""
    key = customer_key.strip()
    if not key:
        raise ValueError("Employee install requires a customer-scoped H20 key")
    base = normalize_h20_base_url(base_url)
    return {
        "H20_KEYS_API_KEY": key,
        "H20_KEYS_BASE_URL": f"{base}/v1",
        "PERPLEXITY_API_KEY": key,
        "PERPLEXITY_API_URL": f"{base}/v1/chat/completions",
        "PERPLEXITY_MODEL": "pplx-sonar",
        "GROQ_API_KEY": key,
        "GROQ_BASE_URL": f"{base}/proxy/groq/openai/v1",
    }


def save_employee_credentials(customer_key: str, base_url: str) -> list[str]:
    """Persist aliases through Hermes credential lifecycle; return names only."""
    from hermes_cli.credential_lifecycle import save_provider_env_credential

    values = employee_env_values(customer_key, base_url)
    for name, value in values.items():
        save_provider_env_credential(name, value)
    return sorted(values)


def employee_secret_env_names() -> tuple[str, ...]:
    return _EMPLOYEE_SECRET_ENV_NAMES
