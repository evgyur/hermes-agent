---
name: chip-video-grab
description: "Download public YouTube videos or extract audio for local transcription with yt-dlp/ffmpeg. Use when the user sends a YouTube URL and asks to save the video, extract audio, transcribe it locally, or troubleshoot YouTube bot-check/cookie issues."
version: 1.0.0
author: Hermes Agent + Chip
license: MIT
dependencies: [yt-dlp, ffmpeg]
metadata:
  hermes:
    tags: [youtube, video, audio, transcription, yt-dlp, ffmpeg, media]
    related_skills: [whisper, youtube-content]
---

# chip-video-grab

Download a public YouTube video or extract its audio so Hermes can transcribe, summarize, or attach the resulting file.

## When to use

Use this skill when the user:

- sends a YouTube URL and asks to download/save the video;
- asks to extract audio from a YouTube video;
- asks to transcribe a YouTube video using local STT/Whisper;
- hits YouTube bot-check, missing formats, or cookie-related `yt-dlp` errors.

Do **not** use this for private, paid, or access-restricted content unless the user has already provided a valid, legal cookie file for their own account.

## Requirements

Install runtime dependencies on the host that will run the download:

```bash
sudo apt-get install -y ffmpeg nodejs
python -m pip install -U yt-dlp
```

`node` is recommended because modern `yt-dlp` often needs an EJS runtime for YouTube player challenges.

## Workflow

1. Parse the request into:
   - `url`: YouTube watch/shorts/live URL;
   - `mode`: `video` or `audio`;
   - optional `audio_format`: `mp3` or `m4a`.
2. Verify dependencies:
   ```bash
   command -v ffmpeg
   python -m yt_dlp --version
   command -v node || true
   ```
3. Run the bundled helper:
   ```bash
   python skills/chip-video-grab/scripts/youtube_download.py \
     --mode audio \
     --audio-format mp3 \
     --output-dir ~/Downloads/youtube-grab \
     "https://www.youtube.com/watch?v=VIDEO_ID"
   ```
4. Read the JSON result. Only claim success if `success: true` and `file_path` exists.
5. If the user asked for a transcript, pass the downloaded audio/video file into local STT:
   ```python
   from tools.transcription_tools import transcribe_audio
   print(transcribe_audio('/path/to/audio.mp3'))
   ```
6. If the user asked for the file in chat, attach the verified local file rather than only reporting the path.

## Cookie and bot-check fallback

The helper tries:

1. plain `yt-dlp`;
2. `yt-dlp` with `/tmp/yt-cookies/yt-cookies.txt` if present;
3. failure with an actionable JSON error and log path.

Cookie file format: Netscape cookies file at `/tmp/yt-cookies/yt-cookies.txt`.

Guardrails:

- Never ask for or handle the user's YouTube password.
- Do not print cookie values or tokens.
- Do not commit cookie files, logs containing cookies, or downloaded media.
- Treat bot-check failures as normal operational failures; report the blocker and the exact next step.

## Output contract

Return compactly:

- source URL;
- mode used (`video` or `audio`);
- backend used (`plain` or `cookies`);
- verified file path;
- transcript path or summary if requested;
- blocker and log path if failed.

## Quick smoke tests

```bash
python skills/chip-video-grab/scripts/youtube_download.py --help
python -m yt_dlp --simulate --skip-download --print title "https://www.youtube.com/watch?v=jNQXAC9IVRw"
```

The second command may fail on cloud servers with YouTube bot-check. That is still a useful test: it proves `yt-dlp` runs and shows whether cookies are required on that host.
