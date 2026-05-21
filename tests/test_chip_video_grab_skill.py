import importlib.util
from pathlib import Path


def test_chip_video_grab_skill_is_public_safe():
    root = Path(__file__).resolve().parents[1]
    skill = root / "skills" / "chip-video-grab" / "SKILL.md"
    script = root / "skills" / "chip-video-grab" / "scripts" / "youtube_download.py"

    assert skill.exists()
    assert script.exists()

    text = skill.read_text(encoding="utf-8")
    assert "name: chip-video-grab" in text
    assert "yt-dlp" in text
    assert "ffmpeg" in text

    forbidden = [
        "/home/" + "her" + "mes",
        "/home/" + "ch" + "ip",
        "tele" + "gram",
        "617" + "744" + "661",
        "959" + "483" + "82",
        "178.",
        "138.",
        "api" + "_key",
        "tok" + "en=",
    ]
    lower_text = text.lower()
    for marker in forbidden:
        assert marker.lower() not in lower_text


def test_youtube_download_script_imports_without_side_effects():
    root = Path(__file__).resolve().parents[1]
    script = root / "skills" / "chip-video-grab" / "scripts" / "youtube_download.py"
    spec = importlib.util.spec_from_file_location("youtube_download", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.node_js_args() == [] or module.node_js_args()[0] == "--js-runtimes"
