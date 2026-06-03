# Cookie onboarding

Cookies are optional. Many public videos and livestreams work without them. Use cookies only when the site requires login, rate-limit bypass, or an authenticated replay.

## Safe rules

- Never ask for or automate the user's password.
- Never commit cookie files.
- Store local cookies under `~/.cache/chip-video-grab/` or another ignored runtime path.
- Prefer a live browser CDP export over reading encrypted browser profile databases.
- Treat historical or copied cookie files as sensitive.

## CDP export

If a browser is already open and logged in with remote debugging enabled:

```bash
python3 scripts/setup_media_capture.py \
  --cookies-from-cdp http://127.0.0.1:9222 \
  --cookie-out ~/.cache/chip-video-grab/youtube-cookies.txt
```

The exported Netscape cookie file can be passed to `yt-dlp`:

```bash
yt-dlp --cookies ~/.cache/chip-video-grab/youtube-cookies.txt "$URL"
```

## Starting a local debugging browser

On a desktop machine, the user can start Chrome/Chromium with:

```bash
google-chrome --remote-debugging-port=9222
# or
chromium --remote-debugging-port=9222
```

On a remote VPS, use a tunnel or browser relay so the CDP endpoint is reachable only locally/trusted. Do not expose CDP to the public internet.

## If cookies are unavailable

Fall back in this order:
1. public/direct media URL;
2. replay URL;
3. browser capture if the page can play in Chromium;
4. honest blocker report.
