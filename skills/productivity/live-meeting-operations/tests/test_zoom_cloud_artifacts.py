from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "zoom_cloud_artifacts.py"
spec = importlib.util.spec_from_file_location("zoom_cloud_artifacts", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_selects_exact_meeting_id_not_latest() -> None:
    meetings = [
        {"id": 11111111111, "start_time": "2026-08-30T13:00:00Z"},
        {"id": 88528475615, "start_time": "2026-08-30T09:50:00Z"},
    ]
    selected = module.select_meeting(meetings, "885 2847 5615", latest=False)
    assert str(selected["id"]) == "88528475615"


def test_missing_exact_meeting_fails_closed() -> None:
    meetings = [{"id": 11111111111, "start_time": "2026-08-30T13:00:00Z"}]
    try:
        module.select_meeting(meetings, "88528475615", latest=False)
    except RuntimeError as exc:
        assert "no matching" in str(exc)
    else:
        raise AssertionError("wrong recording must not be selected")


def test_vtt_conversion_preserves_timestamp_and_speaker(tmp_path: Path) -> None:
    source = tmp_path / "source.vtt"
    source.write_text(
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\nChip: Решение принято\n\n"
        "2\n00:00:04.000 --> 00:00:05.000\nМихаил: Беру задачу\n",
        encoding="utf-8",
    )
    destination = tmp_path / "readable.txt"
    count = module.vtt_to_txt(source, destination)
    text = destination.read_text(encoding="utf-8")
    assert count == 2
    assert "[00:00:01] Chip: Решение принято" in text
    assert "Михаил: Беру задачу" in text


def test_safe_filename_never_uses_provider_secrets() -> None:
    item = {
        "file_type": "TRANSCRIPT",
        "recording_type": "audio transcript",
        "download_url": "https://example.invalid/secret-token",
        "password": "do-not-leak",
    }
    assert module.safe_filename(1, item) == "01-audio-transcript-transcript.vtt"
