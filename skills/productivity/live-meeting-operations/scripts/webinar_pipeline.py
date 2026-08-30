#!/usr/bin/env python3
"""Durable two-phase webinar finalization and canonical private packaging.

Phase 1 proves the recording exists and is decodable before any synthesis lease is
required. Phase 2 is resumable: lock contention becomes FINALIZATION_DEFERRED,
never a false recording-missing result.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = 1


class PipelineError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PipelineError(f"{path} must contain a JSON object")
    return payload


def _run(argv: Sequence[str], *, timeout: float = 600, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise PipelineError(f"command failed ({result.returncode}): {argv[0]}: {detail}")
    return result


def _probe_media(media: Path, ffprobe: str) -> dict[str, Any]:
    if not media.is_file() or media.is_symlink() or media.stat().st_size <= 0:
        raise FileNotFoundError(str(media))
    result = _run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(media)],
        timeout=120,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if not video or duration <= 0:
        raise PipelineError("recording has no decodable video stream or positive duration")
    return {
        "bytes": media.stat().st_size,
        "duration_seconds": duration,
        "format_name": (payload.get("format") or {}).get("format_name"),
        "video": {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
        },
        "audio": None
        if not audio
        else {
            "codec": audio.get("codec_name"),
            "sample_rate": audio.get("sample_rate"),
            "channels": audio.get("channels"),
        },
    }


def _decode_samples(media: Path, duration: float, ffmpeg: str) -> list[dict[str, Any]]:
    points = [("first", 0.0), ("middle", max(0.0, duration / 2 - 5)), ("last", max(0.0, duration - 10))]
    receipts = []
    for label, start in points:
        result = _run(
            [
                ffmpeg,
                "-v",
                "error",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(media),
                "-t",
                "10",
                "-map",
                "0:v:0",
                "-f",
                "null",
                "-",
            ],
            timeout=120,
            check=False,
        )
        receipts.append({"label": label, "start_seconds": round(start, 3), "decodable": result.returncode == 0})
    if not all(item["decodable"] for item in receipts):
        raise PipelineError("recording failed first/middle/last decode checks")
    return receipts


def verify_recording(media: Path, *, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    metadata = _probe_media(media, ffprobe)
    metadata["decode_samples"] = _decode_samples(media, float(metadata["duration_seconds"]), ffmpeg)
    metadata["sha256"] = _sha256(media)
    metadata["verified_at"] = _now()
    return metadata


def _save_state(path: Path, state: dict[str, Any], status: str, **extra: Any) -> None:
    state["schema"] = SCHEMA
    state["status"] = status
    state["updated_at"] = _now()
    state.update(extra)
    _atomic_json(path, state)


def initialize(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    source = Path(args.source_receipt)
    if not source.is_file():
        raise PipelineError(f"source receipt not found: {source}")
    state = {
        "schema": SCHEMA,
        "kind": "canonical_meeting_pipeline_state",
        "event": {
            "title": args.title,
            "event_date": args.event_date,
            "timezone": args.timezone,
            "scheduled_start": args.scheduled_start,
            "scheduled_end": args.scheduled_end,
            "capture_started_at": args.capture_started_at,
            "official_replay_published_at": args.official_replay_published_at,
        },
        "source": {"receipt_path": str(source), "receipt_sha256": _sha256(source)},
        "capture": {"media_path": str(Path(args.media)), "started": bool(args.capture_started_at)},
        "phases": {
            "source": "VERIFIED",
            "capture": "STARTED" if args.capture_started_at else "PENDING",
            "recording_integrity": "PENDING",
            "asr": "PENDING",
            "package": "PENDING",
        },
        "created_at": _now(),
    }
    _save_state(state_path, state, "CAPTURE_STARTED" if args.capture_started_at else "SOURCE_RESOLVED")
    print(json.dumps({"status": state["status"], "state": str(state_path)}))
    return 0


def capture_receipt(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = _load_json(state_path)
    media = Path((state.get("capture") or {}).get("media_path") or args.media or "")
    size_before = media.stat().st_size if media.is_file() else 0
    if args.growth_window > 0:
        time.sleep(args.growth_window)
    size_after = media.stat().st_size if media.is_file() else 0
    metadata = _probe_media(media, args.ffprobe)
    growth_ok = size_after > size_before if args.require_growth else size_after >= size_before and size_after > 0
    media_ready = args.media_ready == "true"
    status = "CAPTURE_ACTIVE_VERIFIED" if media_ready and growth_ok else "CAPTURE_DEGRADED"
    source_path = Path((state.get("source") or {}).get("receipt_path") or "")
    source_payload = _load_json(source_path) if source_path.is_file() else {}
    source_metadata = source_payload.get("source") or source_payload
    selected_source = source_payload.get("selection") or source_payload.get("selected") or {}
    receipt = {
        "schema": SCHEMA,
        "kind": "live_capture_receipt",
        "status": status,
        "observed_at": _now(),
        "source": {
            "chat_id": source_metadata.get("chat_id"),
            "message_id": source_metadata.get("message_id"),
            "link_type": selected_source.get("source_type") or selected_source.get("kind"),
            "final_domain": selected_source.get("final_domain"),
            "receipt_path": str(source_path),
            "receipt_sha256": _sha256(source_path) if source_path.is_file() else None,
        },
        "media_path": str(media),
        "media_ready": media_ready,
        "size_before_bytes": size_before,
        "size_after_bytes": size_after,
        "growth_window_seconds": args.growth_window,
        "file_growth_verified": growth_ok,
        "probe": metadata,
    }
    receipt_path = Path(args.receipt_output)
    _atomic_json(receipt_path, receipt)
    state.setdefault("capture", {}).update(
        {
            "receipt_path": str(receipt_path),
            "receipt_sha256": _sha256(receipt_path),
            "media_ready": media_ready,
            "file_growth_verified": growth_ok,
        }
    )
    state.setdefault("phases", {})["capture"] = "VERIFIED" if media_ready and growth_ok else "DEGRADED"
    _save_state(state_path, state, status)
    print(json.dumps({"status": status, "receipt": str(receipt_path)}))
    return 0 if status == "CAPTURE_ACTIVE_VERIFIED" else 4


def _recording_receipt(state: Mapping[str, Any], media: Path, metadata: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(state.get("event") or {})
    return {
        "schema": SCHEMA,
        "kind": "recording_integrity_receipt",
        "event": {
            "title": event.get("title"),
            "event_date": event.get("event_date"),
            "timezone": event.get("timezone"),
            "scheduled_start": event.get("scheduled_start"),
            "scheduled_end": event.get("scheduled_end"),
            "capture_started_at": event.get("capture_started_at"),
            "official_replay_published_at": event.get("official_replay_published_at"),
        },
        "media": {"path": str(media), **dict(metadata)},
    }


def _copy_private(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    os.chmod(destination, 0o600)


def _require_private_config(path: Path) -> None:
    stat_result = path.stat()
    if stat_result.st_uid != os.geteuid() or stat_result.st_mode & 0o077:
        raise PipelineError(f"private command config must be owned by the current user and mode 0600: {path}")


def _run_asr(command_file: Path, *, media: Path, transcript: Path, package_dir: Path) -> None:
    _require_private_config(command_file)
    payload = json.loads(command_file.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload or not all(isinstance(item, str) for item in payload):
        raise PipelineError("ASR command file must be a JSON array of argv strings")
    replacements = {"{media}": str(media), "{transcript}": str(transcript), "{package}": str(package_dir)}
    argv: list[str] = [replacements.get(str(item), str(item)) for item in payload]
    _run(argv, timeout=7200)


def _artifact_entry(path: Path, root: Path) -> dict[str, Any]:
    return {"path": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _build_package(args: argparse.Namespace, state: dict[str, Any], recording_receipt: Path) -> dict[str, Any]:
    root = Path(args.package_dir)
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = root.parent / f".{root.name}.staging-{os.getpid()}"
    backup = root.parent / f".{root.name}.previous-{os.getpid()}"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(stage, 0o700)
    source_receipt = Path((state.get("source") or {}).get("receipt_path") or "")
    required_sources = [recording_receipt, source_receipt, Path(args.transcript)]
    for source in required_sources:
        if not source.is_file() or source.stat().st_size == 0:
            raise PipelineError(f"required package artifact is missing or empty: {source}")
    sources: list[tuple[Path, Path]] = [
        (recording_receipt, stage / "recording-receipt.json"),
        (source_receipt, stage / "source-receipt.json"),
        (Path(args.transcript), stage / "transcript.md"),
    ]
    capture_receipt = Path((state.get("capture") or {}).get("receipt_path") or "")
    if capture_receipt.is_file():
        sources.append((capture_receipt, stage / "capture-receipt.json"))
    optional = [
        (args.summary, "summary.md"),
        (args.decisions, "decisions-and-actions.md"),
        (args.ideas, "ideas.md"),
        (args.source_links, "source-links.md"),
    ]
    for raw, name in optional:
        if raw:
            source = Path(raw)
            if source.is_file():
                sources.append((source, stage / name))
    for source, destination in sources:
        _copy_private(source, destination)

    if args.speaker_dir:
        speaker_dir = Path(args.speaker_dir)
        if speaker_dir.is_dir():
            for source in sorted(speaker_dir.glob("*.md")):
                _copy_private(source, stage / "transcripts-by-speaker" / source.name)

    event = state.get("event") or {}
    semantic_dates = {
        "schema": SCHEMA,
        "event_date": event.get("event_date"),
        "scheduled_start": event.get("scheduled_start"),
        "scheduled_end": event.get("scheduled_end"),
        "capture_started_at": event.get("capture_started_at"),
        "package_created_at": _now(),
        "official_replay_published_at": event.get("official_replay_published_at"),
    }
    _atomic_json(stage / "semantic-dates.json", semantic_dates)
    files = [path for path in stage.rglob("*") if path.is_file() and path.name != "manifest.json"]
    manifest = {
        "schema": SCHEMA,
        "kind": "canonical_private_meeting_package",
        "created_at": _now(),
        "event": {"title": event.get("title"), "event_date": event.get("event_date")},
        "artifacts": [_artifact_entry(path, stage) for path in sorted(files)],
    }
    _atomic_json(stage / "manifest.json", manifest)
    if root.exists():
        root.rename(backup)
    try:
        stage.rename(root)
    except Exception:
        if backup.exists() and not root.exists():
            backup.rename(root)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    manifest["manifest_sha256"] = _sha256(root / "manifest.json")
    return manifest


def finalize(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = _load_json(state_path)
    media = Path((state.get("capture") or {}).get("media_path") or "")
    package_dir = Path(args.package_dir)
    package_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    recording_receipt = package_dir.parent / f".{package_dir.name}.recording-receipt.json"

    # Phase 1 intentionally precedes the synthesis lease.
    try:
        metadata = verify_recording(media, ffprobe=args.ffprobe, ffmpeg=args.ffmpeg)
    except FileNotFoundError:
        state.setdefault("phases", {})["recording_integrity"] = "MISSING"
        _save_state(state_path, state, "RECORDING_MISSING", blocker=f"media not found: {media}")
        print(json.dumps({"status": "RECORDING_MISSING", "media": str(media)}))
        return 5
    except (PipelineError, OSError, ValueError, json.JSONDecodeError) as exc:
        state.setdefault("phases", {})["recording_integrity"] = "INVALID"
        _save_state(state_path, state, "RECORDING_INVALID", blocker=str(exc))
        print(json.dumps({"status": "RECORDING_INVALID", "error": str(exc)}))
        return 6

    _atomic_json(recording_receipt, _recording_receipt(state, media, metadata))
    state.setdefault("recording", {}).update(
        {
            "receipt_path": str(recording_receipt),
            "receipt_sha256": _sha256(recording_receipt),
            "media_sha256": metadata["sha256"],
            "bytes": metadata["bytes"],
            "duration_seconds": metadata["duration_seconds"],
        }
    )
    state.setdefault("phases", {})["recording_integrity"] = "VERIFIED"
    _save_state(state_path, state, "RECORDING_VERIFIED")

    lock_path = Path(args.finalization_lock)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = open(lock_path, "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            resume_after = (datetime.now(timezone.utc) + timedelta(seconds=args.resume_delay)).isoformat()
            state.setdefault("phases", {})["package"] = "DEFERRED"
            _save_state(
                state_path,
                state,
                "FINALIZATION_DEFERRED",
                deferred={"reason": "finalization lease busy", "resume_after": resume_after},
            )
            print(json.dumps({"status": "FINALIZATION_DEFERRED", "resume_after": resume_after}))
            return 75

        transcript = Path(args.transcript)
        if (not transcript.is_file() or transcript.stat().st_size == 0) and args.asr_command_file:
            state.setdefault("phases", {})["asr"] = "RUNNING"
            _save_state(state_path, state, "ARTIFACTS_PROCESSING")
            _run_asr(Path(args.asr_command_file), media=media, transcript=transcript, package_dir=package_dir)
        if not transcript.is_file() or transcript.stat().st_size == 0:
            state.setdefault("phases", {})["asr"] = "PENDING"
            _save_state(state_path, state, "ARTIFACTS_PROCESSING", blocker="transcript not ready")
            print(json.dumps({"status": "ARTIFACTS_PROCESSING", "blocker": "transcript not ready"}))
            return 3
        state.setdefault("phases", {})["asr"] = "VERIFIED"
        manifest = _build_package(args, state, recording_receipt)
        state.setdefault("phases", {})["package"] = "VERIFIED"
        state.pop("blocker", None)
        state.pop("deferred", None)
        _save_state(
            state_path,
            state,
            "PACKAGE_COMPLETE",
            package={
                "path": str(package_dir),
                "manifest_path": str(package_dir / "manifest.json"),
                "manifest_sha256": manifest["manifest_sha256"],
                "artifact_count": len(manifest["artifacts"]),
            },
        )
        print(
            json.dumps(
                {
                    "status": "PACKAGE_COMPLETE",
                    "package": str(package_dir),
                    "manifest_sha256": manifest["manifest_sha256"],
                    "artifacts": len(manifest["artifacts"]),
                }
            )
        )
        return 0
    finally:
        lock_handle.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    init = sub.add_parser("init")
    init.add_argument("--state", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--event-date", required=True)
    init.add_argument("--timezone", required=True)
    init.add_argument("--scheduled-start")
    init.add_argument("--scheduled-end")
    init.add_argument("--capture-started-at")
    init.add_argument("--official-replay-published-at")
    init.add_argument("--source-receipt", required=True)
    init.add_argument("--media", required=True)
    init.set_defaults(func=initialize)

    capture = sub.add_parser("capture-receipt")
    capture.add_argument("--state", required=True)
    capture.add_argument("--media")
    capture.add_argument("--receipt-output", required=True)
    capture.add_argument("--media-ready", choices=("true", "false"), required=True)
    capture.add_argument("--growth-window", type=float, default=0)
    capture.add_argument("--require-growth", action="store_true")
    capture.add_argument("--ffprobe", default="ffprobe")
    capture.set_defaults(func=capture_receipt)

    final = sub.add_parser("finalize")
    final.add_argument("--state", required=True)
    final.add_argument("--package-dir", required=True)
    final.add_argument("--transcript", required=True)
    final.add_argument("--speaker-dir")
    final.add_argument("--summary")
    final.add_argument("--decisions")
    final.add_argument("--ideas")
    final.add_argument("--source-links")
    final.add_argument("--asr-command-file")
    final.add_argument("--finalization-lock", required=True)
    final.add_argument("--resume-delay", type=int, default=300)
    final.add_argument("--ffprobe", default="ffprobe")
    final.add_argument("--ffmpeg", default="ffmpeg")
    final.set_defaults(func=finalize)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (PipelineError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "PIPELINE_ERROR", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
