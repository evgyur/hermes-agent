#!/usr/bin/env python3
"""
Guardrail for new skills: enforce create-skill workflow contract.

Default mode: --staged (check staged SKILL.md additions in current git repo)
Can also validate explicit skill directories passed as args.
"""

from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REQUIRED_SKILL_PATTERNS = [
    re.compile(r"^##\s+Output\s+Contract", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^##\s+Quick\s+Test\s+Checklist", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^##\s+Done\s+Criteria", re.IGNORECASE | re.MULTILINE),
]

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
XML_TAG_RE = re.compile(r"<[^>]+>")


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()


def try_repo_root() -> Path | None:
    try:
        return Path(run(["git", "rev-parse", "--show-toplevel"]))
    except subprocess.CalledProcessError:
        return None


def staged_new_skill_dirs(repo_root: Path) -> list[Path]:
    out = run(["git", "diff", "--cached", "--name-status"])
    dirs: list[Path] = []
    if not out:
        return dirs

    for line in out.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        path = parts[-1]
        if status.startswith("A") and path.endswith("/SKILL.md"):
            dirs.append((repo_root / path).parent)
    return dirs


def parse_frontmatter(text: str) -> tuple[str, str] | None:
    m = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.DOTALL)
    if not m:
        return None

    block = m.group(1)
    name = ""
    description = ""

    for line in block.splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip('"\'')
        if line.startswith("description:"):
            description = line.split(":", 1)[1].strip().strip('"\'')

    return name, description


def validate_frontmatter(skill_dir: Path, text: str) -> list[str]:
    errors: list[str] = []
    parsed = parse_frontmatter(text)
    if not parsed:
        return [f"{skill_dir}: missing YAML frontmatter with name/description"]

    name, description = parsed

    if not name:
        errors.append(f"{skill_dir}: frontmatter missing 'name'")
    elif not NAME_RE.match(name):
        errors.append(f"{skill_dir}: name must match ^[a-z0-9-]{{1,64}}$")
    elif "anthropic" in name or "claude" in name:
        errors.append(f"{skill_dir}: name cannot contain reserved words anthropic/claude")

    if not description:
        errors.append(f"{skill_dir}: frontmatter missing non-empty 'description'")
    else:
        if len(description) > 1024:
            errors.append(f"{skill_dir}: description exceeds 1024 chars")
        if XML_TAG_RE.search(description):
            errors.append(f"{skill_dir}: description cannot contain XML tags")
        if re.match(r"^(i|i\'m|you|we|я|ты)\b", description.strip(), re.IGNORECASE):
            errors.append(f"{skill_dir}: description should be in third person (not I/you/we)")

    return errors


def validate_skill_dir(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.exists():
        return [f"{skill_dir}: missing SKILL.md"]

    text = skill_md.read_text(encoding="utf-8", errors="ignore")

    errors.extend(validate_frontmatter(skill_dir, text))

    for pat in REQUIRED_SKILL_PATTERNS:
        if not pat.search(text):
            errors.append(f"{skill_dir}: SKILL.md missing section '{pat.pattern}'")

    refs = skill_dir / "references"
    has_refs = refs.exists() and any(p.suffix.lower() == ".md" for p in refs.rglob("*.md"))
    if not has_refs:
        errors.append(f"{skill_dir}: missing references/*.md")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce create-skill workflow for new skills")
    parser.add_argument("paths", nargs="*", help="Skill directories to validate")
    parser.add_argument("--staged", action="store_true", help="Validate staged added SKILL.md folders")
    args = parser.parse_args()

    repo_root = try_repo_root()

    skill_dirs: list[Path] = []
    if args.staged or not args.paths:
        if repo_root is None:
            if args.staged or not args.paths:
                print("❌ skill-workflow-guard: staged/default mode requires a git repository; pass explicit skill paths when validating a workspace copy")
                return 2
        else:
            skill_dirs.extend(staged_new_skill_dirs(repo_root))
    for p in args.paths:
        if os.path.isabs(p):
            skill_dirs.append(Path(p))
        elif repo_root is not None:
            skill_dirs.append(repo_root / p)
        else:
            skill_dirs.append(Path(p).resolve())

    # uniq preserve order
    seen = set()
    uniq_dirs: list[Path] = []
    for d in skill_dirs:
        k = str(d.resolve())
        if k not in seen:
            seen.add(k)
            uniq_dirs.append(d)

    if not uniq_dirs:
        print("skill-workflow-guard: no new skill dirs to validate")
        return 0

    failures: list[str] = []
    for d in uniq_dirs:
        failures.extend(validate_skill_dir(d))

    if failures:
        print("\n❌ create-skill workflow guard failed:\n")
        for f in failures:
            print(f"- {f}")
        print("\nFix: use /create-skill extract|interview|refactor and include frontmatter + Output Contract + Quick Test Checklist + Done Criteria + references/*.md")
        return 1

    print(f"✅ create-skill workflow guard passed for {len(uniq_dirs)} skill dir(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
