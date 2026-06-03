#!/usr/bin/env python3
"""Transcribe a recording with faster-whisper and timestamped segments."""
from __future__ import annotations

import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("media", help="audio/video file path")
parser.add_argument("--out", help="transcript output path")
parser.add_argument("--language", default="ru")
parser.add_argument("--model", default="small")
args = parser.parse_args()

try:
    from faster_whisper import WhisperModel
except Exception as e:
    raise SystemExit(f"missing faster_whisper: {e}")

media = Path(args.media).expanduser().resolve()
out = Path(args.out).expanduser().resolve() if args.out else media.with_suffix(".transcript.txt")
out.parent.mkdir(parents=True, exist_ok=True)

def fmt(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

model = WhisperModel(args.model, device="cpu", compute_type="int8")
segments, info = model.transcribe(
    str(media),
    language=args.language or None,
    beam_size=5,
    vad_filter=True,
    vad_parameters={"min_silence_duration_ms": 700},
    condition_on_previous_text=True,
)

with out.open("w", encoding="utf-8") as f:
    f.write(f"# Transcript: {media.name}\n")
    f.write(f"language={info.language} probability={info.language_probability:.3f} duration={info.duration:.1f}s model={args.model}\n\n")
    for seg in segments:
        text = seg.text.strip()
        if text:
            f.write(f"[{fmt(seg.start)}–{fmt(seg.end)}] {text}\n")

print(f"transcript={out}")
