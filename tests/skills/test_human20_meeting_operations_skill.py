from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "productivity" / "live-meeting-operations"
RECEIPT = ROOT / "skills" / ".sources" / "live-meeting-operations.json"


def test_canonical_human20_meeting_skill_receipt_matches_tree():
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert receipt["source_repo"] == "human20team/human20-meeting-operations"
    assert re.fullmatch(r"[0-9a-f]{40}", receipt["source_commit"])
    assert re.fullmatch(r"[0-9a-f]{40}", receipt["source_tree"])
    assert receipt["version"] == (SKILL / "VERSION").read_text().strip()
    actual = {
        p.relative_to(SKILL).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in SKILL.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    expected = {row["path"]: row["sha256"] for row in receipt["entries"]}
    assert actual == expected


def test_canonical_human20_meeting_skill_owns_full_lifecycle():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for needle in (
        "single canonical meeting root",
        "Zoom calls, Zoom webinars, Google Meet calls",
        "speech-to-speech",
        "authorized context envelope",
        "verified owner-only room",
        "check the configured Zoom Server-to-Server API",
        "Team20 cards",
    ):
        assert needle.lower() in text.lower()


def test_no_second_human20_meeting_lifecycle_is_bundled():
    collisions = []
    for path in (ROOT / "skills").rglob("SKILL.md"):
        if path == SKILL / "SKILL.md":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "canonical_repo: human20team/human20-meeting-operations" in text:
            collisions.append(path.relative_to(ROOT).as_posix())
    assert collisions == []
