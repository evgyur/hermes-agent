from __future__ import annotations

import json
import re
from pathlib import Path

import hermes_cli


ROOT = Path(__file__).resolve().parents[2]


def test_release_manifest_matches_package_and_pins_valid_components():
    manifest = json.loads(
        (ROOT / "powerpack" / "release.json").read_text(encoding="utf-8")
    )

    assert manifest["version"] == hermes_cli.__version__
    assert manifest["data_policy"]["profiles"] == "preserve"
    pins = manifest.get("component_pins")
    assert isinstance(pins, dict) and pins
    assert {
        "hermesdev_exact_topic_checkpoint",
        "h20_keys_groq_stt_transport",
        "jyotish_rectification_continuity",
    }.issubset(pins)
    for component in pins.values():
        assert re.fullmatch(r"[0-9a-f]{40}", component["commit"])
        assert component["asset_path"]
        assert component["deployment"] == "preserve_profile_use_pinned_component"
