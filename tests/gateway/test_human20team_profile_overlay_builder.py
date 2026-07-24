"""P04 tests for the fail-closed Human20Bot profile overlay builder."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml


BUILDER = Path(__file__).resolve().parents[2] / "scripts/build_human20bot_profile_overlay.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("p04_overlay_builder", BUILDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "telegram": {
                    "group_sessions_per_user": True,
                    "thread_sessions_per_user": True,
                    "require_mention": False,
                    "suppress_tool_progress_chats": ["synthetic-existing-chat"],
                    "extra": {},
                    "token": "synthetic-secret-must-not-escape",
                },
                "tools": {
                    "enabled": [
                        "terminal",
                        "file",
                        "web",
                        "search",
                        "delegation",
                        "memory",
                        "cronjob",
                        "messaging",
                    ],
                    "tool_search": {"enabled": "auto"},
                },
                "toolsets": ["synthetic-toolset"],
                "plugins": {"enabled": ["synthetic-plugin"]},
                "providers": {"synthetic": {"api_key": "synthetic-secret"}},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    approval = artifacts / "approvals" / "APP-002.json"
    approval.parent.mkdir(parents=True)
    approval.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "approval_id": "APP-002",
                "class_name": "privacy",
                "status": "consumed",
                "authority_chat_id": "synthetic-authority-chat",
                "authority_source_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    (artifacts / "P02-membership-config.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "extra_patch": {
                    "team_authority_chat_id": "synthetic-authority-chat",
                    "team_membership_positive_ttl_seconds": 60,
                    "team_membership_negative_ttl_seconds": 10,
                    "team_membership_max_cache_entries": 10000,
                },
            }
        ),
        encoding="utf-8",
    )
    return config, approval, artifacts


def test_builder_emits_only_evidence_backed_overlay_and_manifest(tmp_path: Path) -> None:
    config, approval, artifacts = _inputs(tmp_path)
    out = artifacts / "profile-overlay"

    result = _load_builder().build_profile_overlay(
        config_path=config,
        out_dir=out,
        approval_path=approval,
        redact=True,
        no_secrets=True,
    )

    overlay = yaml.safe_load((out / "config.overlay.yaml").read_text(encoding="utf-8"))
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert set(overlay) == {"schema_version", "telegram"}
    assert set(overlay["telegram"]) == {
        "extra",
        "group_sessions_per_user",
        "require_mention",
        "suppress_tool_progress_chats",
        "thread_sessions_per_user",
    }
    assert overlay["telegram"]["extra"]["team_authority_chat_id"] == "synthetic-authority-chat"
    assert overlay["telegram"]["suppress_tool_progress_chats"] == [
        "synthetic-authority-chat",
        "synthetic-existing-chat",
    ]
    assert not ({"tools", "toolsets", "plugins", "providers", "mcp_servers"} & set(overlay))
    assert manifest["status"] == "staged"
    assert manifest["approval_id"] == "APP-002"
    assert manifest["capability_config_sha256"] == result["capability_config_sha256"]
    assert manifest["files"] == ["config.overlay.yaml"]

    material = "\n".join(path.read_text(encoding="utf-8") for path in out.iterdir())
    assert "synthetic-secret" not in material


def test_builder_rejects_authority_mismatch(tmp_path: Path) -> None:
    config, approval, artifacts = _inputs(tmp_path)
    receipt = json.loads(approval.read_text(encoding="utf-8"))
    receipt["authority_chat_id"] = "synthetic-other-chat"
    approval.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="authority"):
        _load_builder().build_profile_overlay(
            config_path=config,
            out_dir=artifacts / "profile-overlay",
            approval_path=approval,
            redact=True,
            no_secrets=True,
        )


def test_builder_rejects_output_symlink(tmp_path: Path) -> None:
    config, approval, artifacts = _inputs(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    out = artifacts / "profile-overlay"
    out.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        _load_builder().build_profile_overlay(
            config_path=config,
            out_dir=out,
            approval_path=approval,
            redact=True,
            no_secrets=True,
        )


def test_builder_requires_redaction_and_secret_guards(tmp_path: Path) -> None:
    config, approval, artifacts = _inputs(tmp_path)
    builder = _load_builder()

    with pytest.raises(ValueError, match="redact"):
        builder.build_profile_overlay(config, artifacts / "one", approval, False, True)
    with pytest.raises(ValueError, match="no-secrets"):
        builder.build_profile_overlay(config, artifacts / "two", approval, True, False)


def test_builder_rejects_unknown_existing_output(tmp_path: Path) -> None:
    config, approval, artifacts = _inputs(tmp_path)
    out = artifacts / "profile-overlay"
    out.mkdir()
    (out / "unexpected.txt").write_text("do not delete", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected"):
        _load_builder().build_profile_overlay(config, out, approval, True, True)
