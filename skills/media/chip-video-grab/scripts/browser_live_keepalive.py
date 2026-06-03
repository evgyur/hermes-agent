#!/usr/bin/env python3
"""Keep a live/webinar page open in visible Chromium and log media state."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

if len(sys.argv) < 4:
    raise SystemExit("usage: browser_live_keepalive.py <url> <outdir> <max_seconds>")

url = sys.argv[1]
outdir = Path(sys.argv[2]).expanduser().resolve()
max_seconds = int(float(sys.argv[3]))
outdir.mkdir(parents=True, exist_ok=True)
log_path = outdir / "browser-events.jsonl"
shot_path = outdir / "latest.png"

def chrome_path() -> str | None:
    env = os.getenv("CHROME_PATH")
    if env and Path(env).exists():
        return env
    for c in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]:
        p = shutil.which(c)
        if p:
            return p
    pw = Path.home() / ".cache/ms-playwright"
    for name in ["chrome", "chromium"]:
        found = sorted(pw.glob(f"**/{name}")) if pw.exists() else []
        if found:
            return str(found[-1])
    return None

def log(obj: dict) -> None:
    obj["ts"] = time.time()
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        executable_path=chrome_path(),
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-features=AudioServiceSandbox",
            "--window-size=1366,768",
        ],
    )
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    page.on("console", lambda msg: log({"kind": "console", "type": msg.type, "text": msg.text[:1200]}))
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        log({"kind": "goto", "url": page.url, "title": page.title()})
    except Exception as e:
        log({"kind": "goto_error", "error": repr(e)})

    start = time.time()
    last_shot = 0.0
    last_state = 0.0
    while time.time() - start < max_seconds:
        try:
            page.mouse.click(683, 384)
            page.evaluate(
                """
                () => {
                  for (const v of document.querySelectorAll('video,audio')) {
                    try { v.muted = false; v.volume = 1; if (v.play) v.play().catch(()=>{}); } catch(e) {}
                  }
                  const re = /(play|старт|начать|смотреть|войти|продолжить|запустить|подключиться|▶|►)/i;
                  for (const b of document.querySelectorAll('button,a,[role=button],div')) {
                    const t=(b.innerText||b.textContent||'').trim();
                    if (t && t.length < 80 && re.test(t)) { try { b.click(); } catch(e) {} }
                  }
                }
                """
            )
        except Exception as e:
            log({"kind": "tick_error", "error": repr(e)})

        now = time.time()
        if now - last_state > 60:
            last_state = now
            try:
                info = page.evaluate(
                    """
                    () => ({
                      title: document.title,
                      url: location.href,
                      text: document.body ? document.body.innerText.slice(0, 2500) : '',
                      media: [...document.querySelectorAll('video,audio')].map((v, i) => ({
                        i, tag:v.tagName, src:v.currentSrc||v.src||'', paused:v.paused,
                        muted:v.muted, volume:v.volume, currentTime:v.currentTime,
                        duration:v.duration, readyState:v.readyState, networkState:v.networkState
                      })),
                      perf: performance.getEntriesByType('resource').map(e=>e.name)
                        .filter(n => /m3u8|mpd|\\.m4s|\\.ts|\\.mp4|\\.webm|stream|video|audio/i.test(n)).slice(-120)
                    })
                    """
                )
                log({"kind": "page_state", "info": info})
            except Exception as e:
                log({"kind": "state_error", "error": repr(e)})
        if now - last_shot > 120:
            last_shot = now
            try:
                page.screenshot(path=str(shot_path), full_page=False)
                log({"kind": "screenshot", "path": str(shot_path)})
            except Exception as e:
                log({"kind": "screenshot_error", "error": repr(e)})
        time.sleep(10)

    try:
        page.screenshot(path=str(outdir / "final.png"), full_page=False)
    except Exception:
        pass
    browser.close()
    log({"kind": "browser_done"})
