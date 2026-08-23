#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".db", ".sqlite", ".pyc"}
PATTERNS = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{24,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "literal_secret_assignment": re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
}
ALLOWLIST_SNIPPETS = ["[REDACTED]", "example", "placeholder", "dummy", "mock_offline"]


def iter_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_PARTS for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield p


def scan(root: Path) -> list[tuple[str, int, str]]:
    findings = []
    for p in iter_files(root):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for idx, line in enumerate(text.splitlines(), 1):
            if any(x in line for x in ALLOWLIST_SNIPPETS):
                continue
            for name, rx in PATTERNS.items():
                if rx.search(line):
                    findings.append((str(p), idx, name))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Lightweight repo secret scan")
    parser.add_argument("--path", default=".")
    args = parser.parse_args()
    findings = scan(Path(args.path))
    for path, line, name in findings:
        print(f"{path}:{line}: {name}")
    print(f"secret_scan_findings={len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
