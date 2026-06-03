# Live webinar / stream capture

Use this when direct download fails and the only reliable route is recording the browser page on a VPS.

## What the pipeline does

`record_live_page.sh` starts:
- `Xvfb` virtual display;
- `PulseAudio` with a `webinar` null sink;
- visible Chromium via Playwright;
- `ffmpeg` screen capture + `webinar.monitor` audio capture.

The browser helper repeatedly tries to click likely play/start buttons, unmutes `video`/`audio` elements, and logs DOM/performance media hints.

## Setup

First run:

```bash
python3 scripts/setup_media_capture.py --install --smoke
```

If installed from Hermes skills, use the skill directory path shown by `skill_view`.

## Record

```bash
OUTDIR=~/webinar-recordings/my-webinar-$(date +%Y%m%d)
scripts/record_live_page.sh "$URL" "$OUTDIR" 14400
```

Environment overrides:

```bash
DISPLAY_ID=:121 WIDTH=1920 HEIGHT=1080 FPS=15 scripts/record_live_page.sh "$URL" "$OUTDIR" 7200
CHROME_PATH=/path/to/chrome scripts/record_live_page.sh "$URL" "$OUTDIR" 3600
```

## Verify

After the script ends:

```bash
cat "$OUTDIR/audio-rms.txt"
cat "$OUTDIR/ffprobe-av.txt"
cat "$OUTDIR/ffprobe-audio.txt"
```

Expected:
- `.mp4` has video + AAC audio streams;
- `.wav` exists at 16 kHz mono;
- RMS is above 0 during real playback.

If RMS is 0:
1. check `browser-events.jsonl` to confirm the stream actually played;
2. check screenshots `latest.png` / `final.png`;
3. try direct media URLs from the `perf` entries;
4. rerun with a longer cap or after manually solving login/CAPTCHA in a CDP browser;
5. do not summarize the silent recording.

## Transcribe

```bash
scripts/transcribe_recording.py "$OUTDIR"/live-audio-*.wav \
  --out "$OUTDIR"/transcript.txt \
  --language ru --model small
```

## Summary shape

```md
# <title> — summary

## Проверка записи
- recording: <path>
- audio RMS: <value>
- transcript coverage: <duration>

## Прозвучало в эфире
- [00:00:00] factual point

## Самые интересные моменты
- [00:00:00] moment and why it matters

## Практические приёмы / команды / инструменты
- tool/command/service: what was shown

## Подводные камни
- issue → workaround

## Что можно применить
- concrete application

## Мои рекомендации
- clearly separated from the speaker's content
```

## Direct media discovery

Before browser capture, inspect the page for cleaner media:

```js
[...document.querySelectorAll('video,audio')].map(v => v.currentSrc || v.src)
performance.getEntriesByType('resource')
  .map(e => e.name)
  .filter(n => /m3u8|mpd|\.m4s|\.ts|\.mp4|\.webm|audio|video/i.test(n))
```

If `.m3u8` or `.mpd` is found, prefer:

```bash
ffmpeg -i "$STREAM_URL" -c copy output.mkv
```

or `yt-dlp` when it handles the site better.

## Scheduling with Hermes cron

Create a wrapper under `~/.hermes/scripts/record_<slug>.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
URL='https://example.com/live-room'
OUTDIR="$HOME/webinar-recordings/<slug>-YYYYMMDD"
exec /path/to/chip-video-grab/scripts/record_live_page.sh "$URL" "$OUTDIR" 14400
```

Schedule it as a script-only job a few minutes before start:

```text
cronjob create:
  no_agent=true
  script=record_<slug>.sh
  schedule=<ISO start minus 2-5 min>
  deliver=origin
```

Then schedule an LLM post-process job after expected finish to verify, transcribe, and summarize.
