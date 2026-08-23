#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Shell syntax.
bash -n scripts/test.sh

# Python syntax.
python3 -m py_compile scripts/privacy_scan.py
rm -rf scripts/__pycache__

# Required files.
test -f SKILL.md
test -f README.md
test -f AGENT_HANDOFF.md
test -f references/no-agent-watchdogs.md
test -f references/backup-storage-incidents.md
test -f references/disk-pressure-triage.md
test -f references/public-safety-checklist.md
test -f templates/guardian-report.md

# Basic frontmatter checks.
grep -q '^name: guardianangel$' SKILL.md
grep -q '^description:' SKILL.md
grep -q '^## Output Contract$' SKILL.md
grep -q '^## Quick Test Checklist$' SKILL.md
grep -q '^## Done Criteria$' SKILL.md

# Public-clean scan.
python3 scripts/privacy_scan.py .

echo "guardianangel tests OK"
