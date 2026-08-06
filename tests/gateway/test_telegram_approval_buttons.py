"""Tests for Telegram inline keyboard approval buttons."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the repo root is importable
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


# ---------------------------------------------------------------------------
# Minimal Telegram mock so TelegramAdapter can be imported
# ---------------------------------------------------------------------------
def _ensure_telegram_mock():
    """Wire up the minimal mocks required to import TelegramAdapter."""
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    # Provide real exception classes so ``except (NetworkError, ...)`` in
    # connect() doesn't blow up under xdist when this mock leaks.
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter, ParseMode
from gateway.config import Platform, PlatformConfig


def _make_adapter(extra=None):
    """Create a TelegramAdapter with mocked internals."""
    config = PlatformConfig(enabled=True, token="test-token", extra=extra or {})
    adapter = TelegramAdapter(config)
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


class _AuthRunner:
    """Minimal runner shim for callback auth tests."""

    def __init__(self, authorized: bool):
        self.authorized = authorized
        self.last_source = None

    async def _handle_message(self, event):
        return None

    def _is_user_authorized(self, source):
        self.last_source = source
        return self.authorized


# ===========================================================================
# send_exec_approval — inline keyboard buttons
# ===========================================================================

class TestTelegramExecApproval:
    """Test the send_exec_approval method sends InlineKeyboard buttons."""

    @pytest.mark.asyncio
    async def test_sends_inline_keyboard(self):
        adapter = _make_adapter()
        mock_msg = MagicMock()
        mock_msg.message_id = 42
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)

        result = await adapter.send_exec_approval(
            chat_id="12345",
            command="rm -rf /important",
            session_key="agent:main:telegram:group:12345:99",
            description="dangerous deletion",
        )

        assert result.success is True
        assert result.message_id == "42"

        adapter._bot.send_message.assert_called_once()
        kwargs = adapter._bot.send_message.call_args[1]
        assert kwargs["chat_id"] == 12345
        assert "rm -rf /important" in kwargs["text"]
        assert "dangerous deletion" in kwargs["text"]
        assert kwargs["reply_markup"] is not None  # InlineKeyboardMarkup


    @pytest.mark.asyncio
    async def test_non_smart_allow_permanent_false_keeps_session(self, monkeypatch):
        adapter = _make_adapter()
        adapter._bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=42))
        buttons = []
        monkeypatch.setattr(
            "plugins.platforms.telegram.adapter.InlineKeyboardButton",
            lambda text, callback_data: buttons.append(text) or text,
        )
        monkeypatch.setattr(
            "plugins.platforms.telegram.adapter.InlineKeyboardMarkup", lambda rows: rows
        )

        await adapter.send_exec_approval(
            chat_id="12345", command="curl example.test", session_key="s",
            allow_permanent=False,
        )

        assert buttons == ["✅ Allow Once", "✅ Session", "❌ Deny"]

    @pytest.mark.asyncio
    async def test_full_approval_keyboard_is_two_by_two(self, monkeypatch):
        """Regression: d48bf743f flattened all buttons into one row (4x1)."""
        adapter = _make_adapter()
        adapter._bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=42))
        captured_rows = []
        monkeypatch.setattr(
            "plugins.platforms.telegram.adapter.InlineKeyboardButton",
            lambda text, callback_data: text,
        )
        monkeypatch.setattr(
            "plugins.platforms.telegram.adapter.InlineKeyboardMarkup",
            lambda rows: captured_rows.extend(rows) or rows,
        )

        await adapter.send_exec_approval(
            chat_id="12345", command="curl example.test", session_key="s",
        )

        assert captured_rows == [
            ["✅ Allow Once", "✅ Session"],
            ["✅ Always", "❌ Deny"],
        ]


    @pytest.mark.asyncio
    async def test_smart_deny_two_buttons_share_one_row(self, monkeypatch):
        """smart_deny yields 2 buttons — they pair into a single readable row."""
        adapter = _make_adapter()
        adapter._bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=42))
        captured_rows = []
        monkeypatch.setattr(
            "plugins.platforms.telegram.adapter.InlineKeyboardButton",
            lambda text, callback_data: text,
        )
        monkeypatch.setattr(
            "plugins.platforms.telegram.adapter.InlineKeyboardMarkup",
            lambda rows: captured_rows.extend(rows) or rows,
        )

        await adapter.send_exec_approval(
            chat_id="12345", command="curl example.test", session_key="s",
            allow_permanent=False, smart_denied=True,
        )

        assert captured_rows == [
            ["✅ Allow Once", "❌ Deny"],
        ]


    @pytest.mark.asyncio
    async def test_send_update_prompt_escapes_dynamic_prompt(self):
        adapter = _make_adapter()
        sent = {}

        async def mock_send_message(**kwargs):
            sent.update(kwargs)
            return SimpleNamespace(message_id=55)

        adapter._bot.send_message = AsyncMock(side_effect=mock_send_message)

        result = await adapter.send_update_prompt(
            chat_id="12345",
            prompt="Fix [issue]_1 and verify *markdown*",
            default="alpha_beta",
            metadata={"thread_id": "999"},
        )

        assert result.success is True
        assert "MARKDOWN_V2" in repr(sent["parse_mode"])
        assert "Fix \\[issue\\]\\_1" in sent["text"]
        assert "alpha\\_beta" in sent["text"]

# _handle_callback_query — approval button clicks
# ===========================================================================

class TestTelegramApprovalCallback:
    """Test the approval callback handling in _handle_callback_query."""


    @pytest.mark.asyncio
    async def test_resume_typing_after_inline_approval(self):
        """Clicking an inline approval button must un-pause the chat's typing.

        Regression for #27853: the text /approve path resumed typing, but the
        ea: callback path did not, so the typing indicator stayed gone for the
        rest of a long-running turn after a button click.
        """
        adapter = _make_adapter()
        adapter._approval_state[5] = "agent:main:telegram:group:12345:99"
        adapter.pause_typing_for_chat("12345")
        assert "12345" in adapter._typing_paused

        query = AsyncMock()
        query.data = "ea:once:5"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.from_user = MagicMock()
        query.from_user.first_name = "Norbert"
        query.from_user.id = "12345"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            with patch("tools.approval.resolve_gateway_approval", return_value=1):
                await adapter._handle_callback_query(update, context)

        assert "12345" not in adapter._typing_paused


    @pytest.mark.asyncio
    async def test_approval_callback_escapes_dynamic_user_name(self):
        adapter = _make_adapter()
        adapter._approval_state[3] = "agent:main:telegram:group:12345:99"

        query = AsyncMock()
        query.data = "ea:once:3"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.from_user = MagicMock()
        query.from_user.first_name = "Alice_Bob"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        query.from_user.id = "12345"

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            with patch("tools.approval.resolve_gateway_approval", return_value=1):
                await adapter._handle_callback_query(update, context)

        edit_kwargs = query.edit_message_text.call_args[1]
        assert "MARKDOWN_V2" in repr(edit_kwargs["parse_mode"])
        assert "Alice\\_Bob" in edit_kwargs["text"]
        assert "Approved once" in edit_kwargs["text"]

    @pytest.mark.asyncio
    async def test_subconscious_pending_intent_approval_runs_transition_packet_and_enqueue(self, tmp_path):
        adapter = _make_adapter()
        room = tmp_path / "room"
        project = tmp_path / "project"
        room.mkdir()
        project.mkdir()
        (room / "posted_pending_intents.json").write_text(
            '{"posted":{"intent_mem0g-health-issue":{"message_id":12854,"path":"pending_intents/intent_mem0g-health-issue.yaml","callback_token":"f2079a0dad52"}},"tokens":{"f2079a0dad52":"intent_mem0g-health-issue"}}\n'
        )

        query = AsyncMock()
        query.data = "subc:y:f2079a0dad52"
        query.message = MagicMock()
        query.message.chat_id = -1003971448755
        query.message.message_id = 12854
        query.message.chat.type = "supergroup"
        query.from_user = MagicMock()
        query.from_user.id = "617744661"
        query.from_user.first_name = 'Evgeny "Chip"'
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"ok":true,"build_packet":"bp.json","shaw_run":"sr.json"}', stderr="")
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*", "SUBC_ROOM": str(room), "SUBC_PROJECT": str(project)}, clear=False):
            with patch("gateway.platforms.telegram.subprocess.run", return_value=completed) as run:
                await adapter._handle_callback_query(update, MagicMock())

        commands = [call.args[0] for call in run.call_args_list]
        assert any("subc_transition.py" in " ".join(cmd) and "approved" in cmd for cmd in commands)
        transition_cmd = commands[0]
        assert "--room" in transition_cmd
        assert "--intent-id" in transition_cmd
        assert "--decision" in transition_cmd
        assert "--approver" in transition_cmd
        assert any("subc_build_packet.py" in " ".join(cmd) for cmd in commands)
        assert any("subc_shaw_enqueue.py" in " ".join(cmd) for cmd in commands)
        state = json.loads((room / "posted_pending_intents.json").read_text())
        entry = state["posted"]["intent_mem0g-health-issue"]
        assert entry["decision"] == "approved"
        assert entry["build_packet"] == "bp.json"
        assert entry["shaw_run"] == "sr.json"
        query.answer.assert_called()
        query.edit_message_text.assert_called_once()
        assert query.edit_message_text.call_args.kwargs["reply_markup"] is None

    @pytest.mark.asyncio
    async def test_subconscious_callback_uses_default_room_when_env_missing(self, tmp_path, monkeypatch):
        adapter = _make_adapter()
        room = tmp_path / "room"
        project = tmp_path / "project"
        room.mkdir()
        project.mkdir()
        (room / "posted_pending_intents.json").write_text(
            '{"posted":{"intent_mem0g-health-issue":{"message_id":12854,"path":"pending_intents/intent_mem0g-health-issue.yaml","callback_token":"tok"}},"tokens":{"tok":"intent_mem0g-health-issue"}}\n'
        )

        query = AsyncMock()
        query.data = "subc:y:tok"
        query.message = MagicMock()
        query.message.chat_id = -1003971448755
        query.message.message_id = 12854
        query.message.chat.type = "supergroup"
        query.from_user = MagicMock()
        query.from_user.id = "617744661"
        query.from_user.first_name = 'Evgeny "Chip"'
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        monkeypatch.delenv("SUBC_ROOM", raising=False)
        monkeypatch.delenv("SUBC_PROJECT", raising=False)
        monkeypatch.setattr("gateway.platforms.telegram.SUBC_DEFAULT_ROOM", room)
        monkeypatch.setattr("gateway.platforms.telegram.SUBC_DEFAULT_PROJECT", project)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"ok":true,"build_packet":"bp.json","shaw_run":"sr.json"}', stderr="")
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            with patch("gateway.platforms.telegram.subprocess.run", return_value=completed):
                await adapter._handle_callback_query(update, MagicMock())

        query.answer.assert_called()
        assert "Failed to resolve" not in query.answer.call_args.kwargs.get("text", "")
        state = json.loads((room / "posted_pending_intents.json").read_text())
        assert "tok" not in state.get("tokens", {})

    @pytest.mark.asyncio
    async def test_subconscious_v3_feedback_button_records_bounded_feedback(self, tmp_path):
        adapter = _make_adapter()
        runtime = tmp_path / "runtime"
        project = tmp_path / "project"
        runtime.mkdir()
        (project / "scripts").mkdir(parents=True)
        proposal_id = "prp_a900471f04148fb763f9c2f15f2c62cd"

        query = AsyncMock()
        query.data = f"subcv3:a:{proposal_id}"
        query.message = MagicMock()
        query.message.chat_id = -1003971448755
        query.message.message_thread_id = 1551
        query.message.chat.type = "supergroup"
        query.from_user = MagicMock()
        query.from_user.id = "617744661"
        query.from_user.first_name = 'Evgeny "Chip"'
        query.answer = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        completed = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"ok":true,"action":"accept","writes_canonical_memory":false}',
            stderr="",
        )
        with patch.dict(os.environ, {
            "TELEGRAM_ALLOWED_USERS": "*",
            "SUBC_V3_RUNTIME": str(runtime),
            "SUBC_V3_PROJECT": str(project),
        }, clear=False):
            with patch("gateway.platforms.telegram.subprocess.run", return_value=completed) as run:
                await adapter._handle_callback_query(update, MagicMock())

        cmd = run.call_args.args[0]
        assert "subc_v3_feedback.py" in " ".join(cmd)
        assert cmd[cmd.index("--proposal-id") + 1] == proposal_id
        assert cmd[cmd.index("--action") + 1] == "accept"
        actor_hash = cmd[cmd.index("--actor-ref-hash") + 1]
        assert actor_hash.startswith("sha256:") and "617744661" not in actor_hash
        query.answer.assert_called_once()
        assert "Accepted" in query.answer.call_args.kwargs["text"]
        query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)

    @pytest.mark.asyncio
    async def test_subconscious_v3_feedback_rejects_unknown_action(self):
        adapter = _make_adapter()
        query = AsyncMock()
        query.data = "subcv3:x:prp_a900471f04148fb763f9c2f15f2c62cd"
        query.message = MagicMock()
        query.message.chat_id = -1003971448755
        query.message.chat.type = "supergroup"
        query.from_user = MagicMock()
        query.from_user.id = "617744661"
        query.answer = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            with patch("gateway.platforms.telegram.subprocess.run") as run:
                await adapter._handle_callback_query(update, MagicMock())

        run.assert_not_called()
        assert "Invalid" in query.answer.call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_deny_button(self):
        adapter = _make_adapter()
        adapter._approval_state[2] = "some-session"

        query = AsyncMock()
        query.data = "ea:deny:2"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.from_user = MagicMock()
        query.from_user.first_name = "Alice"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        query.from_user.id = "12345"

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
                await adapter._handle_callback_query(update, context)

        mock_resolve.assert_called_once_with("some-session", "deny")
        edit_kwargs = query.edit_message_text.call_args[1]
        assert "Denied" in edit_kwargs["text"]

    @pytest.mark.asyncio
    async def test_approval_callback_rejects_user_blocked_by_global_allowlist(self):
        adapter = _make_adapter()
        adapter._approval_state[7] = "agent:main:telegram:group:12345:99"
        runner = _AuthRunner(authorized=False)
        adapter._message_handler = runner._handle_message

        query = AsyncMock()
        query.data = "ea:once:7"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.chat.type = "private"
        query.from_user = MagicMock()
        query.from_user.id = 222
        query.from_user.first_name = "Mallory"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch("tools.approval.resolve_gateway_approval") as mock_resolve:
            await adapter._handle_callback_query(update, context)

        mock_resolve.assert_not_called()
        query.answer.assert_called_once()
        assert "not authorized" in query.answer.call_args[1]["text"].lower()
        query.edit_message_text.assert_not_called()
        assert adapter._approval_state[7] == "agent:main:telegram:group:12345:99"
        assert runner.last_source is not None
        assert runner.last_source.platform == Platform.TELEGRAM
        assert runner.last_source.user_id == "222"
        assert runner.last_source.chat_id == "12345"

    @pytest.mark.asyncio
    async def test_already_resolved(self):
        adapter = _make_adapter()
        # No state for approval_id 99 — already resolved

        query = AsyncMock()
        query.data = "ea:once:99"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.from_user = MagicMock()
        query.from_user.first_name = "Bob"
        query.answer = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        query.from_user.id = "12345"

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            with patch("tools.approval.resolve_gateway_approval") as mock_resolve:
                await adapter._handle_callback_query(update, context)

        # Should NOT resolve — already handled
        mock_resolve.assert_not_called()
        # Should still ack with "already resolved" message
        query.answer.assert_called_once()
        assert "already been resolved" in query.answer.call_args[1]["text"]

    @pytest.mark.asyncio
    async def test_model_picker_callback_not_affected(self):
        """Ensure model picker callbacks still route correctly."""
        adapter = _make_adapter()

        query = AsyncMock()
        query.data = "mp:some_provider"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.from_user = MagicMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        # Model picker callback should be handled (not crash)
        # We just verify it doesn't try to resolve an approval
        with patch("tools.approval.resolve_gateway_approval") as mock_resolve:
            with patch.object(adapter, "_handle_model_picker_callback", new_callable=AsyncMock):
                await adapter._handle_callback_query(update, context)

        mock_resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_prompt_callback_not_affected(self, tmp_path):
        """Ensure update prompt callbacks still work."""
        adapter = _make_adapter()

        query = AsyncMock()
        query.data = "update_prompt:y"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.from_user = MagicMock()
        query.from_user.id = 123
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch("tools.approval.resolve_gateway_approval") as mock_resolve:
            with patch("hermes_constants.get_hermes_home", return_value=tmp_path):
                # Allow the caller — the new fail-closed allowlist gate
                # (#24457) rejects empty TELEGRAM_ALLOWED_USERS, but this
                # test isn't exercising that gate; it's verifying the
                # update_prompt callback still writes the response.
                with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}):
                    await adapter._handle_callback_query(update, context)

        # Should NOT have triggered approval resolution
        mock_resolve.assert_not_called()
        assert (tmp_path / ".update_response").read_text() == "y"

    @pytest.mark.asyncio
    async def test_update_prompt_callback_rejects_unauthorized_user(self, tmp_path):
        """Update prompt buttons should honor TELEGRAM_ALLOWED_USERS."""
        adapter = _make_adapter()

        query = AsyncMock()
        query.data = "update_prompt:y"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.from_user = MagicMock()
        query.from_user.id = 222
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch("hermes_constants.get_hermes_home", return_value=tmp_path):
            with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "111"}):
                await adapter._handle_callback_query(update, context)

        query.answer.assert_called_once()
        assert "not authorized" in query.answer.call_args[1]["text"].lower()
        query.edit_message_text.assert_not_called()
        assert not (tmp_path / ".update_response").exists()

    @pytest.mark.asyncio
    async def test_update_prompt_callback_rejects_user_blocked_by_global_allowlist(self, tmp_path):
        adapter = _make_adapter()
        runner = _AuthRunner(authorized=False)
        adapter._message_handler = runner._handle_message

        query = AsyncMock()
        query.data = "update_prompt:y"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.chat.type = "private"
        query.from_user = MagicMock()
        query.from_user.id = 222
        query.from_user.first_name = "Mallory"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch("hermes_constants.get_hermes_home", return_value=tmp_path):
            with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": ""}):
                await adapter._handle_callback_query(update, context)

        query.answer.assert_called_once()
        assert "not authorized" in query.answer.call_args[1]["text"].lower()
        query.edit_message_text.assert_not_called()
        assert not (tmp_path / ".update_response").exists()
        assert runner.last_source is not None
        assert runner.last_source.platform == Platform.TELEGRAM
        assert runner.last_source.user_id == "222"

    @pytest.mark.asyncio
    async def test_update_prompt_callback_allows_authorized_user(self, tmp_path):
        """Allowed Telegram users can still answer update prompt buttons."""
        adapter = _make_adapter()

        query = AsyncMock()
        query.data = "update_prompt:n"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.from_user = MagicMock()
        query.from_user.id = 111
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch("hermes_constants.get_hermes_home", return_value=tmp_path):
            with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "111"}):
                await adapter._handle_callback_query(update, context)

        query.answer.assert_called_once()
        query.edit_message_text.assert_called_once()
        assert (tmp_path / ".update_response").read_text() == "n"


class TestTelegramGptprofCallback:
    """Regression tests for Chip's /gptprof inline keyboard callbacks."""

    @pytest.mark.asyncio
    async def test_gptprof_profile_button_switches_active_codex_profile(self, tmp_path):
        adapter = _make_adapter()
        hcp = tmp_path / "hcp"
        hcp.mkdir()
        auth = tmp_path / "auth.json"
        config = tmp_path / "config.yaml"
        cache = tmp_path / "cache.json"
        send_buttons = tmp_path / "send_buttons.py"
        send_buttons.write_text("print('noop')\n", encoding="utf-8")

        (hcp / "markov495.json").write_text(json.dumps({
            "email": "markov495@gmail.com",
            "plan": "Pro $200",
            "access_token": "access-markov",
            "refresh_token": "refresh-markov",
        }), encoding="utf-8")
        auth.write_text(json.dumps({
            "codex": {"profile": "mynightfly", "access_token": "old"},
            "credential_pool": {"openai-codex": [{"source": "device_code", "access_token": "old"}]},
        }), encoding="utf-8")
        config.write_text("model:\n  provider: minimax\n  default: MiniMax-M2.7\n", encoding="utf-8")
        cache.write_text(json.dumps({"markov495": {"stale": True}}), encoding="utf-8")

        query = AsyncMock()
        query.data = "gptprof:markov495:gpt-5.5"
        query.message = MagicMock(chat_id=617744661)
        query.from_user = MagicMock(id="617744661", first_name="Chip")
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock(callback_query=query)

        env = {
            "GPTPROF_AUTH_PATH": str(auth),
            "GPTPROF_CONFIG_PATH": str(config),
            "GPTPROF_HCP_DIR": str(hcp),
            "GPTPROF_CACHE_PATH": str(cache),
            "GPTPROF_SEND_BUTTONS": str(send_buttons),
            "GPTPROF_ALLOWED_USER": "617744661",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(adapter, "_gptprof_send_card", return_value=None):
                await adapter._handle_callback_query(update, MagicMock())

        saved_auth = json.loads(auth.read_text(encoding="utf-8"))
        assert saved_auth["codex"]["profile"] == "markov495"
        assert saved_auth["codex"]["email"] == "markov495@gmail.com"
        assert saved_auth["codex"]["access_token"] == "access-markov"
        assert saved_auth["providers"]["openai-codex"]["tokens"]["access_token"] == "access-markov"
        assert saved_auth["providers"]["openai-codex"]["tokens"]["refresh_token"] == "refresh-markov"
        assert saved_auth["providers"]["openai-codex"]["auth_mode"] == "chatgpt"
        assert saved_auth["active_provider"] == "openai-codex"
        pool_entry = saved_auth["credential_pool"]["openai-codex"][0]
        assert pool_entry["source"] == "gptprof:markov495"
        assert pool_entry["profile"] == "markov495"
        assert pool_entry["label"] == "markov495"
        assert pool_entry["access_token"] == "access-markov"
        assert pool_entry["refresh_token"] == "refresh-markov"
        assert pool_entry["priority"] == 0
        assert json.loads(cache.read_text(encoding="utf-8")) == {}
        assert "provider: openai-codex" in config.read_text(encoding="utf-8")
        assert "default: gpt-5.5" in config.read_text(encoding="utf-8")
        query.answer.assert_called()

    @pytest.mark.asyncio
    async def test_gptprof_new_auth_can_target_specific_profile(self, tmp_path):
        adapter = _make_adapter()
        auth = tmp_path / "auth.json"
        auth.write_text(json.dumps({"codex": {"profile": "mynightfly"}}), encoding="utf-8")

        query = AsyncMock()
        query.data = "gptprof:new_auth:gptinvest23"
        query.message = MagicMock(chat_id=617744661)
        query.from_user = MagicMock(id="617744661", first_name="Chip")
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock(callback_query=query)

        def close_created_task(coro):
            coro.close()
            return MagicMock()

        env = {
            "GPTPROF_AUTH_PATH": str(auth),
            "GPTPROF_ALLOWED_USER": "617744661",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(adapter, "_gptprof_post_json", return_value={
                "user_code": "ABCD-EFGH",
                "interval": "5",
                "device_auth_id": "deviceauth-test",
            }) as post_json:
                with patch.object(adapter, "_gptprof_poll_device_auth", AsyncMock()) as poll:
                    with patch("gateway.platforms.telegram.asyncio.create_task", side_effect=close_created_task):
                        await adapter._handle_callback_query(update, MagicMock())

        post_json.assert_called_once()
        poll.assert_called_once()
        assert poll.call_args.args[0] == "gptinvest23"
        query.answer.assert_called_once()
        assert "gptinvest23" in query.answer.call_args.kwargs["text"]
        assert "New auth for gptinvest23" in query.edit_message_text.call_args.kwargs["text"]
        assert "<code>ABCD-EFGH</code>" in query.edit_message_text.call_args.kwargs["text"]
        assert query.edit_message_text.call_args.kwargs["parse_mode"] == ParseMode.HTML


    @pytest.mark.asyncio
    async def test_gptprof_callback_rejects_other_users(self, tmp_path):
        adapter = _make_adapter()
        query = AsyncMock()
        query.data = "gptprof:markov495:gpt-5.5"
        query.message = MagicMock(chat_id=617744661)
        query.from_user = MagicMock(id="999", first_name="Mallory")
        query.answer = AsyncMock()
        update = MagicMock(callback_query=query)

        with patch.dict(os.environ, {"GPTPROF_ALLOWED_USER": "617744661"}, clear=False):
            await adapter._handle_callback_query(update, MagicMock())

        query.answer.assert_called_once()
        assert "authorized" in query.answer.call_args.kwargs["text"]
