#!/usr/bin/env python3
"""Materialize the canonical meeting voice runtime for one Unix service account."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat

ROOT = Path(__file__).resolve().parents[1]
TOKENS = ("@PYTHON@", "@RUNTIME_SCRIPT@", "@ENV_FILE@", "@MEETING_REMOTE@", "@SSH_IDENTITY@")


def _absolute_existing(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.exists():
        raise SystemExit(f"{label} must be an existing absolute path")
    return path.resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--meeting-remote", required=True)
    parser.add_argument("--ssh-identity", required=True)
    args = parser.parse_args()

    home = _absolute_existing(args.home, "home")
    python = _absolute_existing(args.python, "python")
    env_file = _absolute_existing(args.env_file, "env file")
    ssh_identity = _absolute_existing(args.ssh_identity, "SSH identity")
    if stat.S_IMODE(env_file.stat().st_mode) != 0o600:
        raise SystemExit("env file must have mode 0600")
    if stat.S_IMODE(ssh_identity.stat().st_mode) != 0o600:
        raise SystemExit("SSH identity must have mode 0600")
    if not args.meeting_remote or any(ch.isspace() for ch in args.meeting_remote):
        raise SystemExit("meeting remote must be one non-empty SSH target")

    runtime_dir = home / ".local/lib/sigurd-meeting"
    bin_dir = home / ".local/bin"
    unit_dir = home / ".config/systemd/user"
    for directory in (runtime_dir, bin_dir, unit_dir):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    runtime_script = runtime_dir / "zoom_gpt_voice.py"
    cli_script = bin_dir / "sigurd-voice"
    shutil.copyfile(ROOT / "scripts/zoom_gpt_voice.py", runtime_script)
    shutil.copyfile(ROOT / "scripts/sigurd_voice.py", cli_script)
    os.chmod(runtime_script, 0o700)
    os.chmod(cli_script, 0o700)

    replacements = {
        "@PYTHON@": str(python),
        "@RUNTIME_SCRIPT@": str(runtime_script),
        "@ENV_FILE@": str(env_file),
        "@MEETING_REMOTE@": args.meeting_remote,
        "@SSH_IDENTITY@": str(ssh_identity),
    }
    for name in ("sigurd-gpt-voice@.service", "sigurd-gpt-meet@.service"):
        rendered = (ROOT / "templates" / name).read_text(encoding="utf-8")
        for token, value in replacements.items():
            rendered = rendered.replace(token, value)
        if any(token in rendered for token in TOKENS):
            raise SystemExit(f"unrendered placeholder in {name}")
        target = unit_dir / name
        target.write_text(rendered, encoding="utf-8")
        os.chmod(target, 0o600)

    print(f"installed runtime={runtime_script} cli={cli_script} units={unit_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
