"""Tests for agent/skill_commands.py — skill slash command scanning and platform filtering."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import tools.skills_tool as skills_tool_module
from agent.skill_commands import (
    build_preloaded_skills_prompt,
    build_skill_invocation_message,
    is_postcraft_loaded_message,
    is_postcraft_trigger_message,
    is_sco_loaded_message,
    is_shalimov_craft_loaded_message,
    maybe_build_postcraft_autoload_message,
    postcraft_reasoning_config,
    resolve_skill_command_key,
    resolve_writing_skill_runtime,
    scan_skill_commands,
    shalimov_craft_reasoning_config,
    writing_skill_reasoning_config,
    writing_skill_runtime_config,
)


def _make_skill(
    skills_dir, name, frontmatter_extra="", body="Do the thing.", category=None
):
    """Helper to create a minimal skill directory with SKILL.md."""
    if category:
        skill_dir = skills_dir / category / name
    else:
        skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = f"""\
---
name: {name}
description: Description for {name}.
{frontmatter_extra}---

# {name}

{body}
"""
    (skill_dir / "SKILL.md").write_text(content)
    return skill_dir


def _symlink_category(skills_dir: Path, linked_root: Path, category: str) -> Path:
    """Create a category symlink under skills_dir pointing outside the tree."""
    external_category = linked_root / category
    external_category.mkdir(parents=True, exist_ok=True)
    symlink_path = skills_dir / category
    try:
        symlink_path.symlink_to(external_category, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable in test environment: {exc}")
    return external_category


class TestScanSkillCommands:

    def test_bundled_present_skill_registers_slash_command_and_slides_mode(self):
        """The bundled /present skill must expose /present and document slides routing."""
        repo_root = Path(__file__).resolve().parents[2]
        bundled_skills = repo_root / "skills"

        with patch("tools.skills_tool.SKILLS_DIR", bundled_skills):
            result = scan_skill_commands()
            message = build_skill_invocation_message(
                "/present",
                user_instruction="slides roadmap for launch",
            )

        assert "/present" in result
        assert result["/present"]["name"] == "present"
        assert message is not None
        assert "/present slides" in message
        assert "separate fullscreen deck mode" in message

    def test_bundled_decision_rp_and_superpowers_register_slash_commands(self):
        """Public distro convenience skills should be invokable as slash commands."""
        repo_root = Path(__file__).resolve().parents[2]
        bundled_skills = repo_root / "skills"

        with patch("tools.skills_tool.SKILLS_DIR", bundled_skills):
            result = scan_skill_commands()
            decision = build_skill_invocation_message(
                "/decision",
                user_instruction="ship now or wait?",
            )
            rp = build_skill_invocation_message(
                "/rp",
                user_instruction="stress-test this plan",
            )
            superpowers = build_skill_invocation_message(
                "/superpowers",
                user_instruction="fix the failing test suite",
            )

        assert "/decision" in result
        assert result["/decision"]["name"] == "decision"
        assert decision is not None
        assert "Phase 1: Decision deconstruction" in decision

        assert "/rp" in result
        assert result["/rp"]["name"] == "rp"
        assert rp is not None
        assert "Reasoning Personas Shortcut" in rp

        assert "/superpowers" in result
        assert result["/superpowers"]["name"] == "superpowers"
        assert superpowers is not None
        assert "Rigorous Execution Mode" in superpowers

    def test_empty_dir(self, tmp_path):
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            result = scan_skill_commands()
        assert result == {}






    def test_loads_skill_invocation_from_symlinked_skill_dir(self, tmp_path):
        """Slash commands should load skills symlinked under the local skills dir."""
        external_root = tmp_path / "external"
        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        real_skill_dir = _make_skill(
            external_root,
            "impeccable",
            body="Apply impeccable design craft.",
        )
        symlink_path = skills_root / "impeccable"
        try:
            symlink_path.symlink_to(real_skill_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable in test environment: {exc}")

        with patch("tools.skills_tool.SKILLS_DIR", skills_root):
            result = scan_skill_commands()
            message = build_skill_invocation_message("/impeccable")

        assert "/impeccable" in result
        assert message is not None
        assert "Apply impeccable design craft." in message

    def test_get_skill_commands_rescans_when_platform_scope_changes(self, tmp_path):
        """Platform-specific disabled-skill caches must not leak across platforms.

        Regression test for #14536: a gateway process serving Telegram
        and Discord concurrently would seed the process-global cache
        with whichever platform scanned first, and subsequent
        ``get_skill_commands()`` calls from the other platform silently
        inherited that filter.
        """
        import agent.skill_commands as sc_mod
        from agent.skill_commands import get_skill_commands

        def _disabled_skills():
            platform = os.getenv("HERMES_PLATFORM")
            if platform == "telegram":
                return {"telegram-only"}
            if platform == "discord":
                return {"discord-only"}
            return set()

        with (
            patch("tools.skills_tool.SKILLS_DIR", tmp_path),
            patch("tools.skills_tool._get_disabled_skill_names", side_effect=_disabled_skills),
            patch.object(sc_mod, "_skill_commands", {}),
            patch.object(sc_mod, "_skill_commands_platform", None),
        ):
            _make_skill(tmp_path, "shared")
            _make_skill(tmp_path, "telegram-only")
            _make_skill(tmp_path, "discord-only")

            with patch.dict(os.environ, {"HERMES_PLATFORM": "telegram"}):
                telegram_commands = dict(get_skill_commands())

            assert "/shared" in telegram_commands
            assert "/discord-only" in telegram_commands
            assert "/telegram-only" not in telegram_commands

            with patch.dict(os.environ, {"HERMES_PLATFORM": "discord"}):
                discord_commands = dict(get_skill_commands())

            assert "/shared" in discord_commands
            assert "/telegram-only" in discord_commands
            assert "/discord-only" not in discord_commands

            # Switching back to telegram must also rescan — not re-serve
            # the discord view that was just cached.
            with patch.dict(os.environ, {"HERMES_PLATFORM": "telegram"}):
                telegram_again = dict(get_skill_commands())

            assert "/telegram-only" not in telegram_again
            assert "/discord-only" in telegram_again

    def test_get_skill_commands_rescans_when_session_platform_changes(self, tmp_path):
        """``HERMES_SESSION_PLATFORM`` from the gateway session context must
        also trigger a rescan, not just ``HERMES_PLATFORM`` (#14536).

        Exercises the real ContextVar path: the gateway sets the active
        adapter via ``set_session_vars(platform=...)`` and the resolver
        reads it via ``get_session_env``. Setting ``HERMES_SESSION_PLATFORM``
        in ``os.environ`` would only test ``get_session_env``'s legacy
        env-var fallback — a regression that swapped ``get_session_env``
        for plain ``os.getenv`` would still pass while breaking concurrent
        gateway sessions, which is the bug the ContextVar plumbing exists
        to prevent in the first place.
        """
        import agent.skill_commands as sc_mod
        from agent.skill_commands import get_skill_commands
        from gateway.session_context import (
            clear_session_vars,
            get_session_env,
            set_session_vars,
        )

        def _disabled_skills():
            platform = (
                os.getenv("HERMES_PLATFORM")
                or get_session_env("HERMES_SESSION_PLATFORM")
            )
            if platform == "telegram":
                return {"telegram-only"}
            if platform == "discord":
                return {"discord-only"}
            return set()

        with (
            patch("tools.skills_tool.SKILLS_DIR", tmp_path),
            patch("tools.skills_tool._get_disabled_skill_names", side_effect=_disabled_skills),
            patch.object(sc_mod, "_skill_commands", {}),
            patch.object(sc_mod, "_skill_commands_platform", None),
        ):
            _make_skill(tmp_path, "shared")
            _make_skill(tmp_path, "telegram-only")
            _make_skill(tmp_path, "discord-only")

            # First simulated gateway request: telegram handler.
            tokens = set_session_vars(platform="telegram")
            try:
                telegram_commands = dict(get_skill_commands())
            finally:
                clear_session_vars(tokens)

            assert "/shared" in telegram_commands
            assert "/discord-only" in telegram_commands
            assert "/telegram-only" not in telegram_commands

            # Second simulated gateway request: discord handler. The cache
            # was just populated for telegram; the rescan trigger must fire
            # off the ContextVar change, not just an env-var change.
            tokens = set_session_vars(platform="discord")
            try:
                discord_commands = dict(get_skill_commands())
            finally:
                clear_session_vars(tokens)

            assert "/shared" in discord_commands
            assert "/telegram-only" in discord_commands
            assert "/discord-only" not in discord_commands

    def test_get_skill_commands_rescans_when_leaving_platform_scope(self, tmp_path, monkeypatch):
        """Returning to no-platform-scope (CLI / cron / RL) after a gateway
        session must rescan so the unfiltered view is repopulated (#14536).

        A long-lived process running both gateway sessions and bare CLI
        invocations would otherwise stay stuck on whichever platform's
        filter was last applied.
        """
        import agent.skill_commands as sc_mod
        from agent.skill_commands import get_skill_commands

        def _disabled_skills():
            if os.getenv("HERMES_PLATFORM") == "telegram":
                return {"telegram-only"}
            return set()

        with (
            patch("tools.skills_tool.SKILLS_DIR", tmp_path),
            patch("tools.skills_tool._get_disabled_skill_names", side_effect=_disabled_skills),
            patch.object(sc_mod, "_skill_commands", {}),
            patch.object(sc_mod, "_skill_commands_platform", None),
        ):
            _make_skill(tmp_path, "shared")
            _make_skill(tmp_path, "telegram-only")

            monkeypatch.setenv("HERMES_PLATFORM", "telegram")
            telegram_commands = dict(get_skill_commands())
            assert "/telegram-only" not in telegram_commands

            # Drop back to no platform scope — bare CLI / cron / RL rollouts.
            monkeypatch.delenv("HERMES_PLATFORM", raising=False)
            bare_commands = dict(get_skill_commands())

            assert "/telegram-only" in bare_commands
            assert sc_mod._skill_commands_platform is None






    # -- core-command collision guard (#31204 / #53450) ---------------------




    # -- inter-skill slug collision dedup (#50304 / #63305) ------------------

    def test_slug_collision_keeps_first_skill(self, tmp_path):
        """Two skills whose names normalize to the same slug do not clobber.

        ``git_helper`` and ``git-helper`` are distinct frontmatter names but
        both reduce to the ``/git-helper`` command. The first one scanned must
        keep the command rather than being silently overwritten by the second.
        """
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            # ``a-first`` sorts before ``z-second`` so the index walk visits the
            # underscore-named skill first; that one must win the slash command.
            first = tmp_path / "a-first"
            first.mkdir()
            (first / "SKILL.md").write_text(
                "---\nname: git_helper\ndescription: First skill.\n---\n\nBody.\n"
            )
            second = tmp_path / "z-second"
            second.mkdir()
            (second / "SKILL.md").write_text(
                "---\nname: git-helper\ndescription: Second skill.\n---\n\nBody.\n"
            )
            result = scan_skill_commands()
        assert "/git-helper" in result
        # First-wins: the entry resolves to the first skill, not the shadowing one.
        assert result["/git-helper"]["name"] == "git_helper"
        assert result["/git-helper"]["skill_dir"] == str(first)

    def test_slug_collision_warns(self, tmp_path, caplog):
        """A slug collision emits a warning so the user can diagnose the
        shadowed skill."""
        import logging as _logging
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            first = tmp_path / "a-first"
            first.mkdir()
            (first / "SKILL.md").write_text(
                "---\nname: my-skill\ndescription: First.\n---\n\nBody.\n"
            )
            second = tmp_path / "z-second"
            second.mkdir()
            (second / "SKILL.md").write_text(
                "---\nname: my_skill\ndescription: Second.\n---\n\nBody.\n"
            )
            with caplog.at_level(_logging.WARNING, logger="agent.skill_commands"):
                scan_skill_commands()
        assert any("already claimed" in r.message for r in caplog.records)


class TestResolveSkillCommandKey:
    """Telegram bot-command names disallow hyphens, so the menu registers
    skills with hyphens swapped for underscores. When Telegram autocomplete
    sends the underscored form back, we need to find the hyphenated key.
    """

    def test_hyphenated_form_matches_directly(self, tmp_path):
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            _make_skill(tmp_path, "claude-code")
            scan_skill_commands()
            assert resolve_skill_command_key("claude-code") == "/claude-code"



    def test_unknown_command_returns_none(self, tmp_path):
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            _make_skill(tmp_path, "claude-code")
            scan_skill_commands()
            assert resolve_skill_command_key("does_not_exist") is None
            assert resolve_skill_command_key("does-not-exist") is None




class TestBuildPreloadedSkillsPrompt:
    def test_builds_prompt_for_multiple_named_skills(self, tmp_path):
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            _make_skill(tmp_path, "first-skill")
            _make_skill(tmp_path, "second-skill")
            prompt, loaded, missing = build_preloaded_skills_prompt(
                ["first-skill", "second-skill"]
            )

        assert missing == []
        assert loaded == ["first-skill", "second-skill"]
        assert "first-skill" in prompt
        assert "second-skill" in prompt
        assert "preloaded" in prompt.lower()

    def test_forwards_task_id_to_skill_usage(self, tmp_path):
        with (
            patch("tools.skills_tool.SKILLS_DIR", tmp_path),
            patch("tools.skill_usage.bump_use") as bump_use,
        ):
            _make_skill(tmp_path, "preloaded-skill")
            _prompt, loaded, missing = build_preloaded_skills_prompt(
                ["preloaded-skill"],
                task_id="task-preloaded",
            )

        assert loaded == ["preloaded-skill"]
        assert missing == []
        bump_use.assert_called_once_with(
            "preloaded-skill",
            task_id="task-preloaded",
        )


    def test_skips_disabled_skill(self, tmp_path, monkeypatch):
        """A globally-disabled skill must not be force-loaded via -s /
        HERMES_TUI_SKILLS preloading (mirrors the bundle gate, #59156)."""
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            _make_skill(tmp_path, "enabled-skill", body="Enabled content.")
            _make_skill(tmp_path, "disabled-skill", body="SECRET DISABLED CONTENT.")

            import agent.skill_utils as su_module
            monkeypatch.setattr(
                su_module, "get_disabled_skill_names", lambda platform=None: {"disabled-skill"}
            )

            prompt, loaded, missing = build_preloaded_skills_prompt(
                ["enabled-skill", "disabled-skill"]
            )

        assert loaded == ["enabled-skill"]
        assert missing == ["disabled-skill"]
        assert "SECRET DISABLED CONTENT." not in prompt
        assert "enabled-skill" in prompt



class TestBuildSkillInvocationMessage:



    def test_forwards_task_id_to_skill_usage(self, tmp_path):
        with (
            patch("tools.skills_tool.SKILLS_DIR", tmp_path),
            patch("tools.skill_usage.bump_use") as bump_use,
        ):
            _make_skill(tmp_path, "test-skill")
            scan_skill_commands()
            msg = build_skill_invocation_message(
                "/test-skill",
                task_id="task-slash",
            )

        assert msg is not None
        bump_use.assert_called_once_with("test-skill", task_id="task-slash")


    def test_uses_shared_skill_loader_for_secure_setup(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TENOR_API_KEY", raising=False)
        calls = []

        def fake_secret_callback(var_name, prompt, metadata=None):
            calls.append((var_name, prompt, metadata))
            os.environ[var_name] = "stored-in-test"
            return {
                "success": True,
                "stored_as": var_name,
                "validated": False,
                "skipped": False,
            }

        monkeypatch.setattr(
            skills_tool_module,
            "_secret_capture_callback",
            fake_secret_callback,
            raising=False,
        )

        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            _make_skill(
                tmp_path,
                "test-skill",
                frontmatter_extra=(
                    "required_environment_variables:\n"
                    "  - name: TENOR_API_KEY\n"
                    "    prompt: Tenor API key\n"
                ),
            )
            scan_skill_commands()
            msg = build_skill_invocation_message("/test-skill", "do stuff")

        assert msg is not None
        assert "test-skill" in msg
        assert len(calls) == 1
        assert calls[0][0] == "TENOR_API_KEY"

    def test_gateway_still_loads_skill_but_returns_setup_guidance(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("TENOR_API_KEY", raising=False)

        def fail_if_called(var_name, prompt, metadata=None):
            raise AssertionError(
                "gateway flow should not try secure in-band secret capture"
            )

        monkeypatch.setattr(
            skills_tool_module,
            "_secret_capture_callback",
            fail_if_called,
            raising=False,
        )

        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            from gateway.session_context import clear_session_vars, set_session_vars

            tokens = set_session_vars(platform="telegram")
            try:
                _make_skill(
                    tmp_path,
                    "test-skill",
                    frontmatter_extra=(
                        "required_environment_variables:\n"
                        "  - name: TENOR_API_KEY\n"
                        "    prompt: Tenor API key\n"
                    ),
                )
                scan_skill_commands()
                msg = build_skill_invocation_message("/test-skill", "do stuff")
            finally:
                clear_session_vars(tokens)

        assert msg is not None
        assert "local cli" in msg.lower()


    def test_supporting_file_hint_uses_file_path_argument(self, tmp_path):
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            skill_dir = _make_skill(tmp_path, "test-skill")
            references = skill_dir / "references"
            references.mkdir()
            (references / "api.md").write_text("reference")
            scan_skill_commands()
            msg = build_skill_invocation_message("/test-skill", "do stuff")

        assert msg is not None
        assert 'file_path="<path>"' in msg


class TestSkillDirectoryHeader:
    """The activation message must expose the absolute skill directory and
    explain how to resolve relative paths, so skills with bundled scripts
    don't force the agent into a second ``skill_view()`` round-trip."""

    def test_header_contains_absolute_skill_dir(self, tmp_path):
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            skill_dir = _make_skill(tmp_path, "abs-dir-skill")
            scan_skill_commands()
            msg = build_skill_invocation_message("/abs-dir-skill", "go")

        assert msg is not None
        assert f"[Skill directory: {skill_dir}]" in msg
        assert "Resolve any relative paths" in msg

    def test_supporting_files_shown_with_absolute_paths(self, tmp_path):
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            skill_dir = _make_skill(tmp_path, "scripted-skill")
            (skill_dir / "scripts").mkdir()
            (skill_dir / "scripts" / "run.js").write_text("console.log('hi')")
            scan_skill_commands()
            msg = build_skill_invocation_message("/scripted-skill")

        assert msg is not None
        # The supporting-files block must emit both the relative form (so the
        # agent can call skill_view on it) and the absolute form (so it can
        # run the script directly via terminal).
        assert "scripts/run.js" in msg
        assert str(skill_dir / "scripts" / "run.js") in msg
        assert f"node {skill_dir}/scripts/foo.js" in msg


class TestTemplateVarSubstitution:
    """``${HERMES_SKILL_DIR}`` and ``${HERMES_SESSION_ID}`` in SKILL.md body
    are replaced before the agent sees the content."""

    def test_substitutes_skill_dir(self, tmp_path):
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            skill_dir = _make_skill(
                tmp_path,
                "templated",
                body="Run: node ${HERMES_SKILL_DIR}/scripts/foo.js",
            )
            scan_skill_commands()
            msg = build_skill_invocation_message("/templated")

        assert msg is not None
        assert f"node {skill_dir}/scripts/foo.js" in msg
        # The literal template token must not leak through.
        assert "${HERMES_SKILL_DIR}" not in msg.split("[Skill directory:")[0]



    def test_disable_template_vars_via_config(self, tmp_path):
        with (
            patch("tools.skills_tool.SKILLS_DIR", tmp_path),
            patch(
                "agent.skill_commands._load_skills_config",
                return_value={"template_vars": False},
            ),
        ):
            _make_skill(
                tmp_path,
                "no-sub",
                body="Run: node ${HERMES_SKILL_DIR}/scripts/foo.js",
            )
            scan_skill_commands()
            msg = build_skill_invocation_message("/no-sub")

        assert msg is not None
        # Template token must survive when substitution is disabled.
        assert "${HERMES_SKILL_DIR}/scripts/foo.js" in msg


class TestInlineShellExpansion:
    """Inline ``!`cmd`` snippets in SKILL.md run before the agent sees the
    content — but only when the user has opted in via config."""



    def test_inline_shell_runs_in_skill_directory(self, tmp_path):
        """Inline snippets get the skill dir as CWD so relative paths work."""
        with (
            patch("tools.skills_tool.SKILLS_DIR", tmp_path),
            patch(
                "agent.skill_commands._load_skills_config",
                return_value={"template_vars": True, "inline_shell": True,
                              "inline_shell_timeout": 5},
            ),
        ):
            skill_dir = _make_skill(
                tmp_path,
                "dyn-cwd",
                body="Here: !`pwd`",
            )
            scan_skill_commands()
            msg = build_skill_invocation_message("/dyn-cwd")

        assert msg is not None
        assert f"Here: {skill_dir}" in msg

    def test_inline_shell_timeout_does_not_break_message(self, tmp_path):
        with (
            patch("tools.skills_tool.SKILLS_DIR", tmp_path),
            patch(
                "agent.skill_commands._load_skills_config",
                return_value={"template_vars": True, "inline_shell": True,
                              "inline_shell_timeout": 1},
            ),
        ):
            _make_skill(
                tmp_path,
                "dyn-slow",
                body="Slow: !`sleep 5 && printf DYN_MARKER`",
            )
            scan_skill_commands()
            msg = build_skill_invocation_message("/dyn-slow")

        assert msg is not None
        # Timeout is surfaced as a marker instead of propagating as an error,
        # and the rest of the skill message still renders.
        assert "inline-shell timeout" in msg
        # The command's intended stdout never made it through — only the
        # timeout marker (which echoes the command text) survives.
        assert "DYN_MARKER" not in msg.replace("sleep 5 && printf DYN_MARKER", "")


class TestPostcraftAutoload:
    def test_detects_editorial_writing_requests(self):
        assert is_postcraft_trigger_message("перепиши этот текст человеческим языком")
        assert is_postcraft_trigger_message("напиши пост про память агентов")
        assert is_postcraft_trigger_message("make it tighter and humanize this draft")
        assert not is_postcraft_trigger_message("/status")
        assert not is_postcraft_trigger_message("проверь порт 443")

    def test_reasoning_config_is_xhigh(self):
        assert postcraft_reasoning_config() == {"enabled": True, "effort": "xhigh"}

    def test_autoloads_postcraft_skill_file_backed(self, tmp_path):
        _make_skill(
            tmp_path,
            "postcraft",
            body="Postcraft rail: run quality_check and write alive Russian text.",
        )
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            msg = maybe_build_postcraft_autoload_message(
                "перепиши этот черновик",
                task_id="test-session",
            )

        assert msg is not None
        assert "postcraft" in msg.lower()
        assert "reasoning=xhigh" in msg
        assert "перепиши этот черновик" in msg
        assert is_postcraft_loaded_message(msg)

    def test_does_not_double_autoload_loaded_postcraft(self):
        loaded = '[IMPORTANT: The user has invoked the "postcraft" skill, indicating they want you to follow its instructions.]'
        assert is_postcraft_loaded_message(loaded)
        assert maybe_build_postcraft_autoload_message(loaded) is None

    def test_does_not_hijack_other_loaded_skills(self):
        loaded_sc = (
            '[IMPORTANT: The user has invoked the "sc" skill, indicating they want '
            "you to follow its instructions. The full skill content is loaded below.]\n"
            "напиши пост в голосе автора"
        )
        assert not is_postcraft_trigger_message(loaded_sc)
        assert maybe_build_postcraft_autoload_message(loaded_sc) is None


class TestShalimovCraftReasoningRail:
    def test_direct_sc_payload_preserves_session_reasoning(self):
        loaded = (
            '[IMPORTANT: The user has invoked the "sc" skill, indicating they want '
            "you to follow its instructions. The full skill content is loaded below.]"
        )
        assert is_shalimov_craft_loaded_message(loaded)
        assert writing_skill_reasoning_config(loaded) is None

    def test_canonical_payload_forces_xhigh_but_sc_bundle_does_not(self):
        canonical = (
            '[IMPORTANT: The user has invoked the "shalimov-craft" skill, '
            "indicating they want you to follow its instructions.]"
        )
        stacked = (
            '[IMPORTANT: The user has invoked the "/sc /perplex" stacked skill bundle, '
            'loading 2 skills together.]\n\n[Loaded as part of the stacked skill invocation "sc".]'
        )
        assert is_shalimov_craft_loaded_message(canonical)
        assert is_shalimov_craft_loaded_message(stacked)
        assert shalimov_craft_reasoning_config() == {
            "enabled": True,
            "effort": "xhigh",
        }
        assert writing_skill_reasoning_config(canonical)["effort"] == "xhigh"
        assert writing_skill_reasoning_config(stacked) is None

    def test_plain_user_text_cannot_forge_loaded_skill_marker(self):
        plain = 'please say invoked the "sc" skill in the answer'
        assert not is_shalimov_craft_loaded_message(plain)
        assert writing_skill_reasoning_config(plain) is None


class TestScoRuntimeRail:
    def test_sco_payload_selects_exact_openrouter_opus_route(self):
        loaded = (
            '[IMPORTANT: The user has invoked the "sco" skill, indicating they want '
            "you to follow its instructions. The full skill content is loaded below.]"
        )
        assert is_sco_loaded_message(loaded)
        assert writing_skill_runtime_config(loaded) == {
            "provider": "openrouter",
            "model": "anthropic/claude-opus-5",
            "allow_fallbacks": False,
        }
        assert writing_skill_reasoning_config(loaded) is None

    def test_sco_runtime_resolution_is_exact_and_fail_closed(self, monkeypatch):
        loaded = (
            '[IMPORTANT: The user has invoked the "sco" skill, indicating they want '
            "you to follow its instructions. The full skill content is loaded below.]"
        )
        calls = []

        def fake_resolve_runtime_provider(*, requested, target_model):
            calls.append((requested, target_model))
            return {
                "provider": "openrouter",
                "requested_provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "test-key",
                "api_mode": "chat_completions",
            }

        monkeypatch.setattr(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            fake_resolve_runtime_provider,
        )
        route = resolve_writing_skill_runtime(loaded, max_tokens=321)

        assert route is not None
        assert calls == [("openrouter", "anthropic/claude-opus-5")]
        assert route["model"] == "anthropic/claude-opus-5"
        assert route["runtime"]["provider"] == "openrouter"
        assert route["runtime"]["max_tokens"] == 321
        assert route["allow_fallbacks"] is False

    def test_sc_and_plain_text_do_not_select_sco_runtime(self):
        loaded_sc = (
            '[IMPORTANT: The user has invoked the "sc" skill, indicating they want '
            "you to follow its instructions. The full skill content is loaded below.]"
        )
        assert not is_sco_loaded_message(loaded_sc)
        assert writing_skill_runtime_config(loaded_sc) is None
        assert writing_skill_runtime_config("describe /sco") is None
