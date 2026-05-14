"""Tests for agent/skill_utils.py."""

from agent.skill_utils import extract_skill_conditions, iter_skill_index_files


def test_metadata_as_dict_with_hermes():
    """Normal case: metadata is a dict containing hermes keys."""
    frontmatter = {
        "metadata": {
            "hermes": {
                "fallback_for_toolsets": ["toolset_a"],
                "requires_toolsets": ["toolset_b"],
                "fallback_for_tools": ["tool_x"],
                "requires_tools": ["tool_y"],
            }
        }
    }
    result = extract_skill_conditions(frontmatter)
    assert result["fallback_for_toolsets"] == ["toolset_a"]
    assert result["requires_toolsets"] == ["toolset_b"]
    assert result["fallback_for_tools"] == ["tool_x"]
    assert result["requires_tools"] == ["tool_y"]


def test_metadata_as_string_does_not_crash():
    """Bug case: metadata is a non-dict truthy value (e.g. a YAML string)."""
    frontmatter = {"metadata": "some text"}
    result = extract_skill_conditions(frontmatter)
    assert result == {
        "fallback_for_toolsets": [],
        "requires_toolsets": [],
        "fallback_for_tools": [],
        "requires_tools": [],
    }


def test_metadata_as_none():
    """metadata key is present but set to null/None."""
    frontmatter = {"metadata": None}
    result = extract_skill_conditions(frontmatter)
    assert result == {
        "fallback_for_toolsets": [],
        "requires_toolsets": [],
        "fallback_for_tools": [],
        "requires_tools": [],
    }


def test_metadata_missing_entirely():
    """metadata key is absent from frontmatter."""
    frontmatter = {"name": "my-skill", "description": "Does stuff."}
    result = extract_skill_conditions(frontmatter)
    assert result == {
        "fallback_for_toolsets": [],
        "requires_toolsets": [],
        "fallback_for_tools": [],
        "requires_tools": [],
    }


def test_iter_skill_index_files_excludes_backups_vendor_and_node_modules(tmp_path):
    """Only active catalog skills should appear in prompt/tool skill indexes."""
    active = tmp_path / "active-skill"
    backup = tmp_path / ".sync-backups" / "snapshot" / "backup-skill"
    suffix_backup = tmp_path / "tg.bak"
    local_backup = tmp_path / "postcraft.local-backup"
    vendored = tmp_path / "some-project" / "vendor" / "vendored-skill"
    node = tmp_path / "node_modules" / "pkg" / "node-skill"

    for skill_dir in (active, backup, suffix_backup, local_backup, vendored, node):
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: x\ndescription: test\n---\nbody\n", encoding="utf-8"
        )

    found = [p.relative_to(tmp_path) for p in iter_skill_index_files(tmp_path, "SKILL.md")]

    assert found == [active.relative_to(tmp_path) / "SKILL.md"]
