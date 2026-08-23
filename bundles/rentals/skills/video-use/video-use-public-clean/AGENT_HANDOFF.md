# Agent handoff: video-use

This is a public-clean skill bundle for conversational video editing.

## Load order

1. Read `SKILL.md`.
2. Read `README.md`.
3. Check `helpers/` commands with `--help` before first use.
4. If a project uses custom brand/style assets, read docs under `references/` and assets under `assets/`.

## Operating contract

- Inventory/transcribe first.
- Propose the edit strategy in plain language.
- Wait for user confirmation before cutting.
- Render preview/final with subtitles last and audio fades at cuts.
- Verify output before showing it.
- Keep all generated work in `<videos_dir>/edit/`; keep the skill directory clean.

## Verification commands

```bash
python helpers/timeline_view.py --help
python helpers/render.py --help
python helpers/pack_transcripts.py --help
ffprobe -version
```

Public-clean: yes. Private/operator-only material: not included.
