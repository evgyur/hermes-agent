"""Synthetic-only replay and privacy-audit contract tests for P03."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from gateway.config import Platform
from gateway.run import _prepare_gateway_status_message


ROOT = Path(__file__).resolve().parents[2]
AUDITOR_PATH = ROOT / "scripts" / "audit_telegram_replay_fixtures.py"


def _load_auditor():
    spec = importlib.util.spec_from_file_location("audit_telegram_replay_fixtures", AUDITOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load replay auditor: {AUDITOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fixture(path: Path, **overrides: object) -> None:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "case_id": "synthetic-quiet-case",
        "synthetic": True,
        "source": "generated",
        "input": {"event_kind": "internal_progress", "payload": "synthetic progress marker"},
        "expected": {"telegram_messages": [], "durable_user_turns": []},
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_replay_auditor_accepts_generated_synthetic_fixture(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(fixtures / "quiet.json")
    output = tmp_path / "audit.json"

    result = _load_auditor().audit_fixture_directory(
        fixtures,
        require_synthetic=True,
        forbid_identifiers=True,
    )
    output.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")

    assert result["status"] == "pass"
    assert result["files_checked"] == 1
    assert result["violations"] == []
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "pass"


@pytest.mark.parametrize(
    ("overrides", "violation_code"),
    [
        ({"synthetic": False}, "fixture_not_synthetic"),
        ({"source": "captured"}, "fixture_source_not_generated"),
        ({"input": {"payload": "contact person@example.test"}}, "private_identifier"),
        ({"input": {"payload": "call +1 202 555 0182"}}, "private_identifier"),
        ({"input": {"payload": "message @private_person"}}, "private_identifier"),
        ({"input": {"user_id": 1234567890}}, "private_identifier"),
    ],
)
def test_replay_auditor_rejects_non_synthetic_or_identifying_data(
    tmp_path: Path,
    overrides: dict[str, object],
    violation_code: str,
) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(fixtures / "unsafe.json", **overrides)

    result = _load_auditor().audit_fixture_directory(
        fixtures,
        require_synthetic=True,
        forbid_identifiers=True,
    )

    assert result["status"] == "fail"
    assert violation_code in {item["code"] for item in result["violations"]}


@pytest.mark.parametrize(
    ("event_type", "message"),
    [
        ("tool_progress", "Reading synthetic fixture file"),
        ("self_review", "Self-improvement review: synthetic memory check complete"),
        ("interrupt", "Operation interrupted."),
        ("lifecycle", "Iteration budget exhausted after 16 synthetic steps."),
        ("lifecycle", "Compacting context — summarizing earlier synthetic conversation"),
    ],
)
def test_internal_lifecycle_status_never_becomes_standalone_telegram_message(
    event_type: str,
    message: str,
) -> None:
    assert _prepare_gateway_status_message(Platform.TELEGRAM, event_type, message) is None


def test_internal_lifecycle_status_remains_available_to_programmatic_surfaces() -> None:
    message = "Self-improvement review: synthetic memory check complete"

    assert _prepare_gateway_status_message("local", "self_review", message) == message


def test_replay_auditor_rejects_identifier_in_fixture_filename(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(fixtures / "person@example.test.json")

    result = _load_auditor().audit_fixture_directory(
        fixtures,
        require_synthetic=True,
        forbid_identifiers=True,
    )

    assert result["status"] == "fail"
    assert "private_identifier" in {item["code"] for item in result["violations"]}


def test_replay_auditor_rejects_fixture_root_symlink(tmp_path: Path) -> None:
    real_fixtures = tmp_path / "real-fixtures"
    real_fixtures.mkdir()
    _write_fixture(real_fixtures / "synthetic.json")
    fixtures_link = tmp_path / "fixtures-link"
    fixtures_link.symlink_to(real_fixtures, target_is_directory=True)

    result = _load_auditor().audit_fixture_directory(
        fixtures_link,
        require_synthetic=True,
        forbid_identifiers=True,
    )

    assert result["status"] == "fail"
    assert "fixtures_root_symlink" in {item["code"] for item in result["violations"]}
