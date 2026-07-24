#!/usr/bin/env python3
"""Audit Telegram replay fixtures for synthetic provenance and identifiers."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.IGNORECASE)
_HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{4,31}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)")
_IDENTIFIER_KEYS = {
    "user_id",
    "chat_id",
    "telegram_id",
    "username",
    "handle",
    "phone",
    "email",
    "first_name",
    "last_name",
    "raw_chat",
    "raw_message",
    "captured_text",
    "real_identity",
}


def _identifier_reason(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _IDENTIFIER_KEYS:
                return f"identifier key at {path}.{key}"
            reason = _identifier_reason(child, f"{path}.{key}")
            if reason:
                return reason
        return None
    if isinstance(value, list):
        for index, child in enumerate(value):
            reason = _identifier_reason(child, f"{path}[{index}]")
            if reason:
                return reason
        return None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        if abs(value) >= 10_000_000:
            return f"long numeric identifier at {path}"
        return None
    if isinstance(value, str):
        for label, pattern in (
            ("email", _EMAIL_RE),
            ("Telegram handle", _HANDLE_RE),
            ("phone", _PHONE_RE),
        ):
            if pattern.search(value):
                return f"{label} at {path}"
    return None


def audit_fixture_directory(
    fixtures: Path | str,
    *,
    require_synthetic: bool,
    forbid_identifiers: bool,
) -> dict[str, Any]:
    root = Path(fixtures)
    violations: list[dict[str, str]] = []
    files_checked = 0

    if root.is_symlink():
        violations.append({"code": "fixtures_root_symlink", "file": str(root), "detail": "fixture root symlink is not allowed"})
    elif not root.is_dir():
        violations.append({"code": "fixtures_missing", "file": str(root), "detail": "fixture directory does not exist"})
    else:
        for path in sorted(root.rglob("*")):
            relative = str(path.relative_to(root))
            if path.is_symlink():
                violations.append({"code": "fixture_symlink", "file": relative, "detail": "symlinks are not allowed"})
                continue
            if path.is_dir():
                continue
            if path.suffix != ".json":
                violations.append({"code": "unexpected_fixture_type", "file": relative, "detail": "only JSON fixtures are allowed"})
                continue
            if forbid_identifiers:
                filename_reason = _identifier_reason(relative, "$.filename")
                if filename_reason:
                    violations.append({"code": "private_identifier", "file": relative, "detail": filename_reason})
            files_checked += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                violations.append({"code": "invalid_fixture_json", "file": relative, "detail": str(exc)})
                continue
            if not isinstance(payload, dict):
                violations.append({"code": "invalid_fixture_shape", "file": relative, "detail": "top level must be an object"})
                continue
            if require_synthetic and payload.get("synthetic") is not True:
                violations.append({"code": "fixture_not_synthetic", "file": relative, "detail": "synthetic must be true"})
            if require_synthetic and payload.get("source") != "generated":
                violations.append({"code": "fixture_source_not_generated", "file": relative, "detail": "source must equal generated"})
            if forbid_identifiers:
                reason = _identifier_reason(payload)
                if reason:
                    violations.append({"code": "private_identifier", "file": relative, "detail": reason})

    if files_checked == 0 and not any(item["code"] == "fixtures_missing" for item in violations):
        violations.append({"code": "fixtures_empty", "file": str(root), "detail": "at least one JSON fixture is required"})

    return {
        "schema_version": "1.0",
        "status": "pass" if not violations else "fail",
        "fixtures_root": str(root),
        "files_checked": files_checked,
        "require_synthetic": require_synthetic,
        "forbid_identifiers": forbid_identifiers,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--require-synthetic", action="store_true")
    parser.add_argument("--forbid-identifiers", action="store_true")
    parser.add_argument("--json-out", required=True, type=Path)
    args = parser.parse_args()

    result = audit_fixture_directory(
        args.fixtures,
        require_synthetic=args.require_synthetic,
        forbid_identifiers=args.forbid_identifiers,
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
