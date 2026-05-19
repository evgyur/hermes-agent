#!/usr/bin/env bash
set -euo pipefail

# Public-safe smoke for the Hermes Power Setup MVP. It performs local checks only:
# no publishing, no Telegram sends, no private overlay, no secret printing.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

cd "$ROOT"

$PYTHON -m py_compile hermes_cli/power.py
$PYTHON -m hermes_cli.main power inventory --json >/tmp/hermes-power-inventory.json
$PYTHON -m hermes_cli.main power doctor --json >/tmp/hermes-power-doctor.json
$PYTHON -m hermes_cli.main power secret-scan >/tmp/hermes-power-secret-scan.txt

$PYTHON - <<'PY'
import json
from pathlib import Path
inventory = json.loads(Path('/tmp/hermes-power-inventory.json').read_text())
assert 'tg' not in inventory['default_modules']
assert 'postcraft' not in inventory['default_modules']
assert not inventory['default_exclusion_violations']
checks = json.loads(Path('/tmp/hermes-power-doctor.json').read_text())
surface_ids = {s['id'] for s in inventory['smoke_surfaces']}
assert {'stt', 'tts', 'auxiliary_vision', 'image_generation', 'video_generation'} <= surface_ids
assert all(s['requires_private_key_in_template'] is False for s in inventory['smoke_surfaces'])
check_names = {c['name'] for c in checks}
assert {'STT', 'TTS', 'Auxiliary vision', 'Image generation', 'PiAPI video generation'} <= check_names
print('hermes-power-smoke: PASS')
PY
