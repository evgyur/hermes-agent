#!/usr/bin/env python3
"""Build the deterministic members-only lesson-4 Powerpack ZIP.

The archive is generated from a committed tree so private working files,
credentials, caches and local state cannot leak into the lesson artifact.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "archive",
            "--format=zip",
            "--prefix=Hermes-powerpack/",
            f"--output={args.output}",
            args.ref,
        ],
        check=True,
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())