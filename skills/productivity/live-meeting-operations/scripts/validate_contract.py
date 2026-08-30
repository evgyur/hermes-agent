#!/usr/bin/env python3
"""Deterministic contract validator for the canonical Human20 meeting skill."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_FILES = {
    "SKILL.md",
    "VERSION",
    "CHANGELOG.md",
    "references/canonical-governance-and-rollout.md",
    "references/webinar-lifecycle.md",
    "references/telegram-webinar-capture-and-packaging.md",
    "references/zoom-cloud-artifacts.md",
    "scripts/zoom_cloud_artifacts.py",
    "scripts/telegram_source_resolver.py",
    "scripts/webinar_pipeline.py",
    "scripts/webinar_finalizer_gate.py",
}
REQUIRED_NEEDLES = {
    "single canonical meeting root": "single canonical meeting root",
    "canonical repository": "human20team/human20-meeting-operations",
    "zoom api first": "check the configured Zoom Server-to-Server API",
    "webinar mode": "### 5. Webinar operation",
    "realtime voice": "speech-to-speech",
    "authorized context": "authorized context envelope",
    "owner boundary": "verified owner-only room",
    "shared room privacy": "Shared Human20 rooms",
    "kanban handoff": "Team20 cards",
    "independent egress": "independent egress receipt",
    "recording proof": "Recording-active state",
    "telegram source resolver": "telegram_source_resolver.py",
    "deferred finalization": "FINALIZATION_DEFERRED",
    "canonical package": "canonical private packaging",
}
FORBIDDEN_SUFFIXES = {".m4a", ".mp3", ".mp4", ".wav", ".vtt", ".srt", ".cookie", ".sqlite"}
FORBIDDEN_TEXT = [
    re.compile(r"(?i)(api[_-]?key|client[_-]?secret|access[_-]?token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
TRIGGER_CASES = {
    "создай Zoom на завтра и пришли ссылку": True,
    "зайди в Google Meet и отвечай голосом": True,
    "вытащи последнюю транскрибацию Zoom и ответственных": True,
    "проведи вебинар, модерируй вопросы и сделай протокол": True,
    "запиши эфир из этого Telegram-поста и сделай расшифровку": True,
    "создай карточку в Kanban по готовому ТЗ": False,
    "расшифруй загруженный mp3": False,
}
TRIGGER_RE = re.compile(
    r"(?i)\b(zoom|google\s*meet|meet\.google|вебинар\w*|эфир\w*|созвон\w*|конференц\w*|meeting\w*|"
    r"облачн\w*\s+запис\w*|транскрибац\w*\s+(?:zoom|meet)|говор\w*\s+голос\w*\s+(?:в|на)\s+(?:zoom|meet))\b"
)


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    for rel in sorted(REQUIRED_FILES):
        if not (root / rel).is_file():
            fail(errors, f"missing required file: {rel}")
    skill = root / "SKILL.md"
    if not skill.is_file():
        return errors
    text = skill.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        fail(errors, "SKILL.md frontmatter must start at byte 0")
    if text.count("\nname: live-meeting-operations\n") != 1:
        fail(errors, "canonical skill name missing or duplicated")
    version = (root / "VERSION").read_text(encoding="utf-8").strip() if (root / "VERSION").is_file() else ""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fail(errors, "VERSION is not semver")
    if f"version: {version}" not in text:
        fail(errors, "SKILL.md version differs from VERSION")
    for label, needle in REQUIRED_NEEDLES.items():
        if needle.lower() not in text.lower():
            fail(errors, f"missing contract: {label}")
    linked = set(re.findall(r"\[[^\]]+\]\((references/[^)]+|scripts/[^)]+)\)", text))
    for rel in sorted(linked):
        if not (root / rel).is_file():
            fail(errors, f"broken direct link: {rel}")
    skill_files = [p for p in root.rglob("SKILL.md") if ".git" not in p.parts]
    if skill_files != [skill]:
        fail(errors, "repository must expose exactly one loadable SKILL.md")
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name in {".env", "auth.json", "cookies.txt"}:
            fail(errors, f"runtime/private artifact forbidden: {path.relative_to(root)}")
        if path.suffix.lower() in {".md", ".py", ".json", ".yaml", ".yml", ".txt"} or path.name in {"VERSION"}:
            content = path.read_text(encoding="utf-8", errors="replace")
            for pattern in FORBIDDEN_TEXT:
                if pattern.search(content):
                    fail(errors, f"credential-like text in {path.relative_to(root)}")
                    break
    for prompt, expected in TRIGGER_CASES.items():
        actual = bool(TRIGGER_RE.search(prompt))
        if actual != expected:
            fail(errors, f"trigger case mismatch: {prompt!r} expected={expected} actual={actual}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    errors = validate(root)
    payload = {"ok": not errors, "root": str(root), "errors": errors, "trigger_cases": len(TRIGGER_CASES)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
