#!/usr/bin/env python3
"""Simple public-clean marker scan for GuardianAngel skill packages."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

# Keep these generic. Projects can add their own private markers locally.
FORBIDDEN_PATTERNS = {
    "absolute private path": re.compile(r"/(home|Users|opt|srv|etc)/[A-Za-z0-9_.-]+"),
    "telegram-style private chat id": re.compile(r"-100\d{7,}"),
    "assignment-like secret": re.compile(r"(?i)(api[_-]?key|token|secret|password|session)\s*[=:]\s*[^\s<>{}]+"),
    "private key header": re.compile(r"BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY"),
    "jwt-like token": re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),
}

ALLOW_FILES = {"privacy_scan.py"}
TEXT_SUFFIXES = {".md", ".py", ".sh", ".txt", ".yaml", ".yml", ".json"}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            yield path


def main() -> int:
    findings = []
    for path in iter_files(ROOT):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in FORBIDDEN_PATTERNS.items():
            if path.name in ALLOW_FILES and label in {"absolute private path", "telegram-style private chat id", "assignment-like secret", "private key header", "jwt-like token"}:
                continue
            for match in pattern.finditer(text):
                findings.append((str(path.relative_to(ROOT)), label, match.group(0)[:120]))
    if findings:
        for rel, label, sample in findings:
            print(f"{rel}: {label}: {sample}")
        return 1
    print("privacy scan OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
