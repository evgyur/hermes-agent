---
name: chip-video-grab
description: "Use when downloading YouTube/Instagram videos, recording live webinars/streams from browser pages, extracting audio, transcribing recordings, or producing timestamped summaries. Provides first-run VPS onboarding for ffmpeg, yt-dlp, Playwright Chromium, Xvfb/PulseAudio browser capture, optional cookie export, and verified media artifacts."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [media, video, youtube, instagram, livestream, webinar, transcription, ffmpeg]
    related_skills: [youtube-content, video-use]
---

# Chip Video Grab

Downloads videos, records live browser-based webinars/streams, extracts audio, transcribes recordings, and creates timestamped summaries with verification. Designed for VPS/server Hermes installs where the agent may not have a physical desktop or audio device.

## When to Use

- A user sends a YouTube, YouTube Live, Shorts, Instagram reel/post, webinar room, livestream, or private player page URL.
- The user asks to download/save a video, extract audio, record an эфир/webinar, transcribe it, or summarize the most useful moments.
- A direct media URL is unavailable and the agent needs to capture browser tab video/audio via virtual display/audio.
- A first-time VPS needs dependencies, cookies, browser capture plumbing, or a smoke test before recording.

Do **not** use for generic video editing decisions after the source is already available; use `video-use` for edits/cuts/subtitles/overlays.

## Output Contract

Return:
1. mode used: `download`, `audio`, `record-live`, `transcribe`, or `summarize`;
2. downloaded/recorded file path;
3. verification evidence: `ffprobe`, file size/duration, and for live capture `audio-rms.txt`;
4. fallback path used: direct media, cookies, mirror, or browser capture;
5. transcript and summary paths when requested;
6. blocker evidence if anything fails. Never fabricate transcript or webinar content.

## First-Run Onboarding on a VPS

On the first request on a new VPS, run setup before attempting an important recording:

```bash
python3 skills/media/chip-video-grab/scripts/setup_media_capture.py --install --smoke
```

If running from an installed skill instead of the repo, resolve the script from the loaded skill directory:

```bash
python3 ~/.hermes/skills/chip-video-grab/scripts/setup_media_capture.py --install --smoke
```

The setup script:
- checks/installs system tools: `ffmpeg`, `ffprobe`, `yt-dlp`, `Xvfb`, `pulseaudio`, `pactl`, `parec`, `dbus-x11`;
- checks Python packages: `playwright`, `faster_whisper`;
- installs Playwright Chromium when missing;
- runs a browser-audio smoke test through Xvfb + PulseAudio;
- optionally attempts cookie export if a CDP endpoint is provided.

Cookie onboarding options:

```bash
# If a browser is already running with remote debugging:
python3 skills/media/chip-video-grab/scripts/setup_media_capture.py \
  --cookies-from-cdp http://127.0.0.1:9222 \
  --cookie-out ~/.cache/chip-video-grab/youtube-cookies.txt
```

If no live CDP browser exists, do not ask for passwords. Tell the user one of these is needed:
- a replay/direct media URL that does not require login;
- a browser already logged in and exposed through CDP;
- a local Netscape cookie file created by the user.

## Route Order

1. **Direct media first.** Inspect DOM/performance entries for `.m3u8`, `.mpd`, `.mp4`, `video.currentSrc`, iframe/player URLs. Use `ffmpeg`/`yt-dlp` when a real stream URL is available.
2. **Downloader ladder.** For YouTube/Instagram, try plain `yt-dlp`/provider-specific helpers, then cookies, then mirror/embed fallback.
3. **Browser capture fallback.** For protected private rooms or players with no extractable stream, record visible Chromium via Xvfb + PulseAudio monitor audio.
4. **Replay fallback.** If live capture is blocked or silent, ask for replay URL and repeat direct-media/download route.

## Live Webinar / Stream Capture

Use `references/live-webinar-capture.md` for details.

Minimal command:

```bash
SKILL=skills/media/chip-video-grab
OUTDIR=~/webinar-recordings/<slug>-$(date +%Y%m%d)
"$SKILL/scripts/record_live_page.sh" "$URL" "$OUTDIR" 14400
```

Expected outputs:
- `live-av-*.mp4` — screen + audio recording;
- `live-audio-*.wav` — mono 16 kHz audio;
- `browser-events.jsonl` — page/media/resource logs;
- `ffprobe-av.txt`, `ffprobe-audio.txt`;
- `audio-rms.txt`.

Hard rule: if `audio-rms.txt` reports `rms=0` after real playback, do not claim recording/transcription success.

## Transcription

```bash
python3 skills/media/chip-video-grab/scripts/transcribe_recording.py \
  ~/webinar-recordings/<slug>/live-audio-*.wav \
  --out ~/webinar-recordings/<slug>/<slug>.transcript.txt \
  --language ru --model small
```

For high-stakes multi-speaker calls, a dedicated external transcription service with diarization may be better. Local `faster_whisper` is the default for private, fast, server-side processing.

## Summary Rules

When summarizing recordings:
- use only the transcript/recording, not guesses;
- include timestamps;
- separate `Прозвучало в эфире` from `Мои рекомендации`;
- include practical commands/tools mentioned;
- include what the user can apply in their own workflow;
- report transcript coverage and any silence/capture gaps.

## Quick Test Checklist

- [ ] `setup_media_capture.py --install --smoke` passes or reports exact missing dependencies.
- [ ] Live smoke test creates `.mp4`, `.wav`, `ffprobe-*`, and non-zero `audio-rms.txt`.
- [ ] A known public YouTube/Instagram URL can be downloaded or fails with an actionable blocker.
- [ ] `transcribe_recording.py` writes timestamped transcript for a short audio file.
- [ ] Summary output separates facts from recommendations.

## Done Criteria

- [ ] Dependencies are installed or the missing dependency is named explicitly.
- [ ] Download/recording path exists and is verified before reporting success.
- [ ] Live capture has non-zero audio RMS before transcription/summarization is claimed.
- [ ] Cookies are obtained only from CDP or user-provided local files, never passwords.
- [ ] Transcript and summary are based on actual recorded/downloaded media.
- [ ] Runtime artifacts, cookies, private URLs, and recordings stay outside git.

## Common Pitfalls

1. **Recording headless Chromium.** Headless often produces no browser audio. Use visible Chromium under Xvfb.
2. **Skipping RMS verification.** Silent capture produces fake-looking transcripts. Stop on `rms=0`.
3. **Assuming cookies are available.** Cookies require a logged-in browser/CDP or user-provided Netscape cookie file; never request passwords.
4. **Using odd screen width.** H.264 requires even dimensions. Default is `1366x768`.
5. **Treating `write() failed: Broken pipe` from `parec` as fatal.** It is harmless when ffmpeg exits after `-t` and ffprobe verifies the output.
6. **Committing private URLs/cookies.** Runtime outputs and cookies stay outside git.

## References

- [Live webinar / stream capture](references/live-webinar-capture.md)
- [Cookie onboarding](references/cookie-onboarding.md)
