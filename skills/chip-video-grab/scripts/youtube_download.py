#!/usr/bin/env python3
"""Download public YouTube video/audio with a conservative yt-dlp fallback ladder.

The script prints a JSON result and never prints cookie values. It is designed
for Hermes skills: deterministic, easy to inspect, and safe to call from an
agent after the user provides a YouTube URL.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COOKIE_PATH = Path(os.getenv("YOUTUBE_COOKIES", "/tmp/yt-cookies/yt-cookies.txt"))
LOG_PATH = Path(os.getenv("YOUTUBE_GRAB_LOG", "/tmp/yt-cookies/download.log"))


def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"[{utc_ts()}] {message}\n")


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    safe = ["<cookies>" if item == str(COOKIE_PATH) else item for item in cmd]
    log("RUN " + " ".join(safe))
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def node_js_args() -> list[str]:
    node = shutil.which("node")
    if not node:
        return []
    return ["--js-runtimes", f"node:{node}", "--remote-components", "ejs:github"]


def newest_file(output_dir: Path) -> str | None:
    files = [p for p in output_dir.glob("*") if p.is_file()]
    if not files:
        return None
    return str(max(files, key=lambda p: p.stat().st_mtime))


def result_check(result: subprocess.CompletedProcess[str], url: str, output_dir: Path, backend: str, mode: str) -> dict[str, Any]:
    path = newest_file(output_dir)
    return {
        "success": bool(path and Path(path).exists()),
        "url": url,
        "mode": mode,
        "backend": backend,
        "file_path": path,
        "stdout_tail": (result.stdout or "")[-800:],
        "log": str(LOG_PATH),
    }


def base_ytdlp_args(url: str, output_template: str) -> list[str]:
    return [
        "yt-dlp",
        "--no-check-certificates",
        "--no-playlist",
        *node_js_args(),
        "--output",
        output_template,
        url,
    ]


def download(url: str, output_dir: Path, mode: str, audio_format: str) -> dict[str, Any]:
    if not shutil.which("yt-dlp"):
        return {"success": False, "error": "yt-dlp not found", "log": str(LOG_PATH)}
    if not shutil.which("ffmpeg"):
        return {"success": False, "error": "ffmpeg not found", "log": str(LOG_PATH)}

    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / "%(title).180B [%(id)s].%(ext)s")

    if mode == "audio":
        format_args = ["-x", "--audio-format", audio_format, "--audio-quality", "0"]
    else:
        format_args = ["-f", "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best"]

    attempts: list[tuple[str, list[str]]] = [
        ("plain", ["yt-dlp", *format_args, *base_ytdlp_args(url, output_template)[1:]]),
    ]
    if COOKIE_PATH.exists():
        attempts.append(
            (
                "cookies",
                ["yt-dlp", "--cookies", str(COOKIE_PATH), *format_args, *base_ytdlp_args(url, output_template)[1:]],
            )
        )

    last: subprocess.CompletedProcess[str] | None = None
    tried: list[str] = []
    for backend, cmd in attempts:
        tried.append(backend)
        result = run(cmd)
        last = result
        if result.returncode == 0:
            checked = result_check(result, url, output_dir, backend, mode)
            if checked["success"]:
                return checked

    stderr = (last.stderr if last else "") or "unknown error"
    log("FAILED " + stderr[-1200:])
    return {
        "success": False,
        "url": url,
        "mode": mode,
        "backends_tried": tried,
        "last_error": stderr[-1200:],
        "log": str(LOG_PATH),
        "cookies_present": COOKIE_PATH.exists(),
        "node_present": bool(shutil.which("node")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Download public YouTube video/audio")
    parser.add_argument("url")
    parser.add_argument("--mode", choices=["video", "audio"], default="video")
    parser.add_argument("--audio-format", choices=["mp3", "m4a"], default="mp3")
    parser.add_argument("--output-dir", default=str(Path.home() / "Downloads" / "youtube-grab"))
    args = parser.parse_args()

    res = download(args.url, Path(args.output_dir).expanduser(), args.mode, args.audio_format)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
