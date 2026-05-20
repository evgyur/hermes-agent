import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli import power, power_quick


def test_power_default_modules_exclude_tg_postcraft():
    inventory = power.build_inventory()

    assert "tg" not in inventory["default_modules"]
    assert "postcraft" not in inventory["default_modules"]
    assert inventory["default_exclusion_violations"] == []


def test_power_preset_has_voice_vision_and_no_private_overlay():
    preset = power.power_preset_defaults()

    assert preset["stt"]["enabled"] is True
    assert preset["tts"]["provider"] == "edge"
    assert preset["agent"]["image_input_mode"] == "auto"
    assert preset["auxiliary"]["vision"]["provider"] == "auto"
    assert preset["video_generation"]["provider"] == "piapi"
    assert preset["video_generation"]["piapi"]["api_key_env"] == "PIAPI_API_KEY"
    assert "tg" not in preset["power"]["modules"]
    assert "postcraft" not in preset["power"]["modules"]
    assert preset["power"]["private_overlay_required"] is False
    assert "piapi-video-generation" in preset["power"]["modules"]
    assert preset["power"]["bundled_skills"] == ["piapi-video-toolkit", "reasoning-personas", "rp"]
    assert {"gptprof", "gptt", "mmfast", "xai", "say", "img", "video"} <= set(preset["quick_commands"])
    assert preset["quick_commands"]["say"]["append_args"] is True


def test_inventory_represents_multimodal_smoke_surfaces():
    inventory = power.build_inventory()
    surfaces = {item["id"]: item for item in inventory["smoke_surfaces"]}

    assert {"stt", "tts", "auxiliary_vision", "image_generation", "video_generation"} <= set(surfaces)
    assert surfaces["auxiliary_vision"]["doctor_check"] == "Auxiliary vision"
    assert surfaces["image_generation"]["doctor_check"] == "Image generation"
    assert surfaces["video_generation"]["doctor_check"] == "PiAPI video generation"
    assert surfaces["video_generation"]["module"] == "piapi-video-generation"
    assert all(item["requires_private_key_in_template"] is False for item in surfaces.values())


def test_power_install_dry_run_does_not_save_config():
    with patch("hermes_cli.power.load_config", return_value={"existing": {"value": 1}}), \
         patch("hermes_cli.power.save_config") as save_config:
        updated = power.apply_power_preset(dry_run=True)

    save_config.assert_not_called()
    assert updated["existing"]["value"] == 1
    assert updated["power"]["enabled"] is True


def test_power_install_writes_config_when_not_dry_run():
    with patch("hermes_cli.power.load_config", return_value={}), \
         patch("hermes_cli.power.save_config") as save_config:
        power.apply_power_preset(dry_run=False)

    save_config.assert_called_once()
    saved = save_config.call_args.args[0]
    assert saved["stt"]["provider"] == "local"
    assert saved["tts"]["provider"] == "edge"


def test_collect_power_checks_reports_voice_and_vision(monkeypatch):
    cfg = {
        "stt": {"enabled": True, "provider": "local"},
        "tts": {"provider": "edge"},
        "auxiliary": {"vision": {"provider": "auto"}},
    }
    monkeypatch.setenv("OPENROUTER_API_KEY", "present-for-test")
    monkeypatch.setenv("PIAPI_API_KEY", "present-for-test")
    with patch("hermes_cli.power.load_config", return_value=cfg):
        checks = power.collect_power_checks()

    by_name = {check.name: check for check in checks}
    assert by_name["default module boundary"].status == "ok"
    assert by_name["TTS"].status == "ok"
    assert by_name["Auxiliary vision"].status == "ok"
    assert "Image generation" in by_name
    assert by_name["PiAPI video generation"].status == "ok"
    assert "STT" in by_name


def test_secret_scan_flags_realistic_tokens(tmp_path):
    target = tmp_path / "bad.md"
    target.write_text('token = "sk-' + 'a' * 30 + '"\n', encoding="utf-8")

    with patch("hermes_cli.power.get_project_root", return_value=tmp_path):
        rc = power.run_secret_scan(paths=["bad.md"], json_output=True)

    assert rc == 1


def test_inventory_json_shape(capsys):
    rc = power.run_inventory(json_output=True)
    out = capsys.readouterr().out
    data = json.loads(out)

    assert rc == 0
    assert "default_modules" in data
    assert "smoke_surfaces" in data
    assert data["default_exclusion_violations"] == []


def test_power_quick_gptprof_lists_public_compat_aliases(capsys):
    rc = power_quick.main(["gptprof"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "/gptt" in out
    assert "/mmfast" in out
    assert "private" in out.lower()


def test_power_quick_say_forwards_text_to_tts(capsys):
    with patch("tools.tts_tool.text_to_speech_tool", return_value=json.dumps({"success": True, "media_tag": "MEDIA:/tmp/say.ogg"})) as tts:
        rc = power_quick.main(["say", "hello", "world"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "MEDIA:/tmp/say.ogg"
    tts.assert_called_once_with("hello world")


def test_power_quick_img_forwards_prompt_to_image_tool(capsys):
    with patch("tools.image_generation_tool.image_generate_tool", return_value=json.dumps({"success": True, "image": "https://example.com/image.png"})) as img:
        rc = power_quick.main(["img", "blue", "cat"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "https://example.com/image.png"
    img.assert_called_once_with(prompt="blue cat")


def test_power_quick_video_is_piapi_generation_surface(monkeypatch, capsys):
    monkeypatch.delenv("PIAPI_API_KEY", raising=False)

    rc = power_quick.main(["video", "cinematic", "robot"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "PiAPI video generation" in out
    assert "PIAPI_API_KEY" in out


def test_power_cli_help_and_setup_power_dry_run_route(tmp_path):
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_path / "hermes-home")
    env["PYTHONPATH"] = os.getcwd()

    help_result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "power", "--help"],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert help_result.returncode == 0
    assert "inventory" in help_result.stdout
    assert "secret-scan" in help_result.stdout

    dry_run_result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "setup", "power", "--dry-run"],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert dry_run_result.returncode == 0, dry_run_result.stderr
    assert "Would update Hermes config" in dry_run_result.stdout
    assert not (Path(env["HERMES_HOME"]) / "config.yaml").exists()
