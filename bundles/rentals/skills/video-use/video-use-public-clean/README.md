# video-use public clean bundle

Conversation-driven video editing skill: inventory footage, transcribe word-level, pack transcripts, propose an edit strategy, wait for confirmation, cut/render, self-check, iterate.

## What is included

- `SKILL.md` — the operating playbook.
- `helpers/` — Python helpers for transcription, transcript packing, timeline/waveform view, grading, and rendering.
- `pyproject.toml` — Python dependencies.
- `references/README.md` — where to put project-specific reference docs.
- `assets/README.md` — where to put brand/media assets.
- `install.md` — generic install notes.
- `AGENT_HANDOFF.md` — quick handoff for another agent.

## What is intentionally not included

No API keys, `.env`, private paths, raw footage, rendered outputs, client references, caches, virtualenv, node_modules, or generated `edit/` folders.

## Minimal setup

```bash
cd video-use-public-clean
python -m venv .venv
. .venv/bin/activate
pip install -e .
ffmpeg -version
cp .env.example .env  # then add ELEVENLABS_API_KEY or GROQ_API_KEY
```

Then register the folder as a skill in your agent, keeping `SKILL.md` and `helpers/` together.
