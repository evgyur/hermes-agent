"""Transcribe a video with ElevenLabs Scribe or Groq Whisper.

Extracts mono 16kHz audio via ffmpeg, uploads to the selected provider,
normalizes the response into the Scribe-like `words` format used by the
video-use helpers, and writes it to <edit_dir>/transcripts/<video_stem>.json.

Cached: if the output file already exists, the upload is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --num-speakers 2
    python helpers/transcribe.py <video_path> --provider groq --language ru
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"
GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def _dotenv_value(name: str) -> str:
    """Read a value from repo .env, cwd .env, then environment."""
    for candidate in [
        Path(__file__).resolve().parent.parent / ".env",
        Path(".env"),
    ]:
        if candidate.exists():
            for line in candidate.read_text(errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
    return os.environ.get(name, "")


def choose_provider(provider: str | None = None) -> str:
    explicit = (
        provider
        or _dotenv_value("VIDEO_USE_STT_PROVIDER")
        or _dotenv_value("STT_PROVIDER")
        or os.environ.get("VIDEO_USE_STT_PROVIDER")
        or os.environ.get("STT_PROVIDER")
        or ""
    ).strip().lower()
    if explicit:
        if explicit not in {"elevenlabs", "scribe", "groq"}:
            sys.exit(f"unsupported transcription provider: {explicit}")
        return "elevenlabs" if explicit == "scribe" else explicit
    if _dotenv_value("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    if _dotenv_value("GROQ_API_KEY"):
        return "groq"
    sys.exit("no transcription key found: set ELEVENLABS_API_KEY or GROQ_API_KEY")


def load_api_key(provider: str | None = None) -> str:
    selected = choose_provider(provider)
    env_name = "ELEVENLABS_API_KEY" if selected == "elevenlabs" else "GROQ_API_KEY"
    v = _dotenv_value(env_name)
    if not v:
        sys.exit(f"{env_name} not found in repo .env, cwd .env, or environment")
    return v


def extract_audio(video_path: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call_scribe(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    data: dict[str, str] = {
        "model_id": "scribe_v1",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    if language:
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)

    with open(audio_path, "rb") as f:
        resp = requests.post(
            SCRIBE_URL,
            headers={"xi-api-key": api_key},
            files={"file": (audio_path.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Scribe returned {resp.status_code}: {resp.text[:500]}")

    payload = resp.json()
    payload.setdefault("provider", "elevenlabs")
    return payload


def _normalize_groq_words(payload: dict) -> dict:
    """Convert Groq/OpenAI verbose_json into the Scribe-like shape used here.

    Groq returns words as `{word,start,end}` when word timestamps are enabled.
    Some models/accounts may return only segment timestamps; keep a segment-level
    fallback so the rest of the pipeline still runs, but mark it in metadata.
    """
    words: list[dict] = []
    raw_words = payload.get("words") or []
    for w in raw_words:
        text = (w.get("word") or w.get("text") or "").strip()
        if not text:
            continue
        start = w.get("start")
        end = w.get("end")
        if start is None or end is None:
            continue
        words.append({"type": "word", "text": text, "start": float(start), "end": float(end)})

    timestamp_granularity = "word"
    if not words:
        timestamp_granularity = "segment"
        for seg in payload.get("segments") or []:
            text = (seg.get("text") or "").strip()
            start = seg.get("start")
            end = seg.get("end")
            if not text or start is None or end is None:
                continue
            words.append({"type": "word", "text": text, "start": float(start), "end": float(end)})

    return {
        "provider": "groq",
        "model": payload.get("model") or os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
        "language_code": payload.get("language"),
        "text": payload.get("text", ""),
        "timestamp_granularity": timestamp_granularity,
        "words": words,
        "raw": payload,
    }


def call_groq(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    data: list[tuple[str, str]] = [
        ("model", os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo")),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "word"),
    ]
    if language:
        data.append(("language", language))
    if num_speakers:
        print("  note: Groq Whisper does not diarize speakers; --num-speakers is ignored")

    with open(audio_path, "rb") as f:
        resp = requests.post(
            GROQ_TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (audio_path.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Groq returned {resp.status_code}: {resp.text[:500]}")

    return _normalize_groq_words(resp.json())


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
    provider: str | None = None,
    verbose: bool = True,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns existing path immediately if the transcript already exists.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)

    selected_provider = choose_provider(provider)
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio)
        size_mb = audio.stat().st_size / (1024 * 1024)
        if verbose:
            print(f"  uploading {video.stem}.wav ({size_mb:.1f} MB) to {selected_provider}", flush=True)
        if selected_provider == "groq":
            payload = call_groq(audio, api_key, language, num_speakers)
        else:
            payload = call_scribe(audio, api_key, language, num_speakers)

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            gran = payload.get("timestamp_granularity") or "word"
            print(f"    words: {len(payload['words'])} ({gran}-level)")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with ElevenLabs Scribe or Groq Whisper")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code (e.g., 'en', 'ru'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional number of speakers. ElevenLabs uses it for diarization; Groq ignores it.",
    )
    ap.add_argument(
        "--provider",
        choices=["elevenlabs", "groq"],
        default=None,
        help="Transcription provider. Default: ElevenLabs if ELEVENLABS_API_KEY exists, otherwise Groq if GROQ_API_KEY exists.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    provider = choose_provider(args.provider)
    api_key = load_api_key(provider)

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        api_key=api_key,
        language=args.language,
        num_speakers=args.num_speakers,
        provider=provider,
    )


if __name__ == "__main__":
    main()
