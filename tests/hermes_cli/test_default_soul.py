"""Tests for the default SOUL.md seed content."""

import re
from pathlib import Path

from hermes_cli.default_soul import DEFAULT_SOUL_MD


def test_default_soul_is_public_safe_and_useful():
    assert "Hermes Agent" in DEFAULT_SOUL_MD
    assert "Privacy is non-negotiable" in DEFAULT_SOUL_MD
    assert "Use the user's language" in DEFAULT_SOUL_MD
    assert "Search before building" in DEFAULT_SOUL_MD

    forbidden_patterns = [
        r"/home/[^\s]+",
        r"/opt/[^\s]+",
        r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
        r"\b-100\d{6,}\b",
        r"[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*[:=]",
        r"BEGIN (?:RSA |OPENSSH |EC |)PRIVATE KEY",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, DEFAULT_SOUL_MD)


def test_docker_soul_matches_seed_template():
    repo_root = Path(__file__).resolve().parents[2]
    docker_soul = (repo_root / "docker" / "SOUL.md").read_text(encoding="utf-8")
    assert docker_soul == DEFAULT_SOUL_MD
