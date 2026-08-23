# Generic install

## Requirements

- Python 3.10+
- ffmpeg + ffprobe on PATH
- Optional: yt-dlp for URL downloads
- Optional: Node.js 22+ for HyperFrames/Remotion animation slots
- Optional: Manim for formal diagram animation
- STT provider key: `ELEVENLABS_API_KEY` or `GROQ_API_KEY`

## Install Python deps

```bash
cd /path/to/video-use-public-clean
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

## Configure STT

Create `.env` beside `SKILL.md` or export env vars in your shell:

```bash
VIDEO_USE_STT_PROVIDER=groq        # or elevenlabs
GROQ_api key placeholder:...                   # if using Groq
ELEVENLABS_api key placeholder:...             # if using ElevenLabs/Scribe
```

Keep `.env` private. Do not commit it or include it in shared archives.

## Register with an agent

Register/symlink the whole folder, not only `SKILL.md`, so helper scripts stay available. Examples:

```bash
mkdir -p ~/.claude/skills
ln -sfn /path/to/video-use-public-clean ~/.claude/skills/video-use

mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -sfn /path/to/video-use-public-clean "${CODEX_HOME:-$HOME/.codex}/skills/video-use"
```

For other agents, add the folder to the agent's skills/plugins directory or import `SKILL.md` in the project/system instructions.

## Verify

```bash
python helpers/timeline_view.py --help >/dev/null && echo timeline_view OK
python helpers/render.py --help >/dev/null && echo render OK
ffprobe -version | head -1
```

Do not run a paid transcription test unless the user supplies sample footage and agrees to use provider quota.
