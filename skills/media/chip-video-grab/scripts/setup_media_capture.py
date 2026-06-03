#!/usr/bin/env python3
"""First-run setup for chip-video-grab on a VPS.

Checks and optionally installs media capture dependencies. Keeps secrets/cookies local.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def run(cmd: list[str], check: bool = False, timeout: int = 300, env: dict | None = None) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd))
    return subprocess.run(cmd, text=True, capture_output=True, check=check, timeout=timeout, env=env)

def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def apt_install(packages: list[str]) -> None:
    if not have("apt-get"):
        print("apt-get not available; install manually:", " ".join(packages))
        return
    sudo = ["sudo", "-n"] if have("sudo") else []
    probe = run(sudo + ["true"], timeout=15) if sudo else subprocess.CompletedProcess([], 0, "", "")
    if sudo and probe.returncode != 0:
        print("sudo without password is unavailable; install manually:", " ".join(packages))
        return
    run(sudo + ["apt-get", "update", "-qq"], check=False, timeout=600)
    run(sudo + ["apt-get", "install", "-y"] + packages, check=False, timeout=1200)

def ensure_python_packages(install: bool) -> None:
    missing = []
    for mod, pkg in [("playwright", "playwright"), ("faster_whisper", "faster-whisper")]:
        try:
            __import__(mod)
            print(f"python:{mod}=ok")
        except Exception:
            print(f"python:{mod}=missing")
            missing.append(pkg)
    if missing and install:
        run([sys.executable, "-m", "pip", "install"] + missing, check=False, timeout=1200)
    try:
        import playwright  # noqa: F401
        run([sys.executable, "-m", "playwright", "install", "chromium"], check=False, timeout=1200)
    except Exception:
        pass

def export_cookies_from_cdp(cdp: str, out: Path) -> int:
    import urllib.request
    out.parent.mkdir(parents=True, exist_ok=True)
    version = json.load(urllib.request.urlopen(cdp.rstrip("/") + "/json/version", timeout=10))
    ws = version.get("webSocketDebuggerUrl")
    if not ws:
        print("cdp_no_websocket")
        return 1
    try:
        import websocket  # type: ignore
    except Exception:
        print("missing websocket-client; installing/checking")
        run([sys.executable, "-m", "pip", "install", "websocket-client"], check=False, timeout=300)
        import websocket  # type: ignore
    sock = websocket.create_connection(ws, timeout=10)
    sock.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
    cookies = []
    while True:
        msg = json.loads(sock.recv())
        if msg.get("id") == 1:
            cookies = msg.get("result", {}).get("cookies", [])
            break
    sock.close()
    with out.open("w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            domain = c.get("domain", "")
            include_sub = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = str(int(c.get("expires") or 0))
            name = c.get("name", "")
            value = c.get("value", "")
            if domain and name:
                f.write("\t".join([domain, include_sub, path, secure, expires, name, value]) + "\n")
    os.chmod(out, 0o600)
    print(f"cookies_exported={out} count={len(cookies)}")
    return 0

def smoke() -> int:
    out = Path(tempfile.mkdtemp(prefix="chip-video-grab-smoke-"))
    html = out / "tone.html"
    html.write_text("""<!doctype html><html><body><button id=b>play</button><script>
const ac=new AudioContext(); b.onclick=()=>{const o=ac.createOscillator(); o.frequency.value=660; o.connect(ac.destination); o.start(); setTimeout(()=>o.stop(), 3000)};
</script></body></html>""", encoding="utf-8")
    script = ROOT / "scripts" / "record_live_page.sh"
    proc = run([str(script), html.as_uri(), str(out / "capture"), "8"], check=False, timeout=120)
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    rms_file = out / "capture" / "audio-rms.txt"
    if not rms_file.exists():
        print("smoke_failed=no_audio_rms")
        return 1
    text = rms_file.read_text(errors="ignore")
    print(text)
    rms = 0
    for line in text.splitlines():
        if line.startswith("rms="):
            rms = int(line.split("=", 1)[1])
    if rms <= 0:
        print("smoke_failed=silent_capture")
        return 1
    print(f"smoke_ok outdir={out / 'capture'} rms={rms}")
    return 0

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="attempt apt/pip/playwright install")
    ap.add_argument("--smoke", action="store_true", help="run browser audio capture smoke test")
    ap.add_argument("--cookies-from-cdp", help="CDP endpoint, e.g. http://127.0.0.1:9222")
    ap.add_argument("--cookie-out", default=str(Path.home() / ".cache/chip-video-grab/youtube-cookies.txt"))
    args = ap.parse_args()

    required = ["ffmpeg", "ffprobe", "yt-dlp", "Xvfb", "pulseaudio", "pactl", "parec", "python3"]
    missing = [c for c in required if not have(c)]
    print("commands=" + json.dumps({c: shutil.which(c) for c in required}, ensure_ascii=False))
    if missing:
        print("missing_commands=" + ",".join(missing))
        if args.install:
            apt_install(["ffmpeg", "yt-dlp", "xvfb", "pulseaudio", "pulseaudio-utils", "dbus-x11", "alsa-utils"])
    ensure_python_packages(args.install)
    if args.cookies_from_cdp:
        rc = export_cookies_from_cdp(args.cookies_from_cdp, Path(args.cookie_out).expanduser())
        if rc:
            return rc
    if args.smoke:
        return smoke()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
