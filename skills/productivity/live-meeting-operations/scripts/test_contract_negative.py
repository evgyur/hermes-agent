#!/usr/bin/env python3
"""Negative contract tests: critical removals must make validation fail."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from validate_contract import validate

ROOT = Path(__file__).resolve().parents[1]


def assert_negative(label: str, mutate) -> None:
    with tempfile.TemporaryDirectory(prefix="meeting-skill-negative-") as tmp:
        candidate = Path(tmp) / "skill"
        shutil.copytree(ROOT, candidate, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        mutate(candidate)
        errors = validate(candidate)
        if not errors:
            raise AssertionError(f"negative test did not fail: {label}")
        print(f"PASS negative: {label}: {errors[0]}")


def main() -> None:
    assert_negative(
        "remove owner privacy boundary",
        lambda root: (root / "SKILL.md").write_text(
            (root / "SKILL.md").read_text().replace("verified owner-only room", "private room"),
            encoding="utf-8",
        ),
    )
    assert_negative(
        "remove webinar contract",
        lambda root: (root / "SKILL.md").write_text(
            (root / "SKILL.md").read_text().replace("### 5. Webinar operation", "### Event operation"),
            encoding="utf-8",
        ),
    )
    assert_negative(
        "remove canonical receipt governance",
        lambda root: (root / "references/canonical-governance-and-rollout.md").unlink(),
    )
    print("NEGATIVE_CONTRACT_TESTS_OK")


if __name__ == "__main__":
    main()
