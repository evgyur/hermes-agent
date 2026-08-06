"""Tests for Telegram inline keyboard clarify buttons.

Mirrors test_telegram_approval_buttons.py for the new ``send_clarify`` and
``cl:`` callback dispatch added in feat/clarify-gateway-buttons.
"""

import os
import sys
import uuid
from datetime import datetime
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
# Minimal Telegram mock so TelegramAdapter can be imported (mirrors
# test_telegram_approval_buttons.py)
# ---------------------------------------------------------------------------
def _ensure_telegram_mock():
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
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_adapter(extra=None):
    config = PlatformConfig(enabled=True, token="test-token", extra=extra or {})
    adapter = TelegramAdapter(config)
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import goals

    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


def _make_goal_runner(adapter: TelegramAdapter, source: SessionSource):
    session_key = build_session_key(source)
    session_entry = SessionEntry(
        session_key=session_key,
        session_id=f"clarify-button-goal-{uuid.uuid4().hex[:8]}",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type=source.chat_type,
    )

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")},
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._queued_events = {}
    runner._running_agents = {session_key: object()}
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store._generate_session_key.return_value = session_key
    adapter._message_handler = runner._handle_message
    return SimpleNamespace(runner=runner, session=session_entry, session_key=session_key)


def _clear_clarify_state():
    from tools import clarify_gateway as cm
    with cm._lock:
        cm._entries.clear()
        cm._session_index.clear()
        cm._notify_cbs.clear()


# ===========================================================================
# send_clarify — render
# ===========================================================================

class TestTelegramSendClarify:
    """Verify the rendered prompt has buttons or none, and stores state."""

    def setup_method(self):
        _clear_clarify_state()

    @pytest.mark.asyncio
    async def test_multi_choice_renders_buttons_and_other(self):
        adapter = _make_adapter()
        mock_msg = MagicMock()
        mock_msg.message_id = 100
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)

        result = await adapter.send_clarify(
            chat_id="12345",
            question="Which option?",
            choices=["alpha", "beta", "gamma"],
            clarify_id="cid1",
            session_key="sk1",
        )

        assert result.success is True
        assert result.message_id == "100"

        kwargs = adapter._bot.send_message.call_args[1]
        assert kwargs["chat_id"] == 12345
        assert "Which option?" in kwargs["text"]
        # Full option text rendered in the message body (not just buttons)
        assert "1. alpha" in kwargs["text"]
        assert "2. beta" in kwargs["text"]
        assert "3. gamma" in kwargs["text"]
        # InlineKeyboardMarkup with N+1 buttons (3 choices + Other)
        markup = kwargs["reply_markup"]
        assert markup is not None
        # Mocked InlineKeyboardMarkup — just verify it was constructed
        # with rows.  We check state instead of poking the mock structure.
        assert "cid1" in adapter._clarify_state
        assert adapter._clarify_state["cid1"] == "sk1"


        # The button label should be short ("1"), not the long choice
        # (we can't inspect mock button labels directly, but the send
        # succeeded — old truncation code could raise on edge cases)

    @pytest.mark.asyncio
    async def test_html_escapes_question(self):
        adapter = _make_adapter()
        mock_msg = MagicMock()
        mock_msg.message_id = 103
        adapter._bot.send_message = AsyncMock(return_value=mock_msg)

        await adapter.send_clarify(
            chat_id="12345",
            question="<script>alert(1)</script>",
            choices=["x"],
            clarify_id="cid5",
            session_key="sk5",
        )
        kwargs = adapter._bot.send_message.call_args[1]
        # Must NOT contain raw <script> — html.escape should have neutralized
        assert "<script>" not in kwargs["text"]
        assert "&lt;script&gt;" in kwargs["text"]


# ===========================================================================
# Callback dispatch — _handle_callback_query routing for cl:* prefixes
# ===========================================================================

class TestTelegramClarifyCallback:
    """Verify clicking a button resolves the clarify primitive."""

    def setup_method(self):
        _clear_clarify_state()

    @pytest.mark.asyncio
    async def test_numeric_choice_resolves_with_choice_text(self):
        from tools import clarify_gateway as cm

        adapter = _make_adapter()
        # Pre-register a clarify entry so the callback can look up the choice text
        cm.register("cidA", "sk-cb", "Pick", ["red", "green", "blue"])
        adapter._clarify_state["cidA"] = "sk-cb"

        query = AsyncMock()
        query.data = "cl:cidA:1"  # green
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.text = "Pick"
        query.from_user = MagicMock()
        query.from_user.id = "777"
        query.from_user.first_name = "Tester"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            await adapter._handle_callback_query(update, context)

        # State popped
        assert "cidA" not in adapter._clarify_state
        # Wait shouldn't be needed — resolve_gateway_clarify is sync.
        # The entry's response should be set.
        # We test by reading the entry's response directly.
        with cm._lock:
            entry = cm._entries.get("cidA")
        # Entry might be popped by wait_for_response, but here we never
        # called wait — so it's still in _entries with response set.
        assert entry is not None
        assert entry.response == "green"
        assert entry.event.is_set()
        query.answer.assert_called_once()
        query.edit_message_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_other_button_flips_to_text_mode(self):
        from tools import clarify_gateway as cm

        adapter = _make_adapter()
        cm.register("cidB", "sk-cb-other", "Pick", ["x", "y"])
        adapter._clarify_state["cidB"] = "sk-cb-other"

        query = AsyncMock()
        query.data = "cl:cidB:other"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.text = "Pick"
        query.from_user = MagicMock()
        query.from_user.id = "777"
        query.from_user.first_name = "Tester"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            await adapter._handle_callback_query(update, context)

        # Entry should now be in text-capture mode
        pending = cm.get_pending_for_session("sk-cb-other")
        assert pending is not None
        assert pending.clarify_id == "cidB"
        assert pending.awaiting_text is True
        # State NOT popped — the user still needs to type their answer
        assert "cidB" in adapter._clarify_state
        # Entry NOT yet resolved
        with cm._lock:
            entry = cm._entries.get("cidB")
        assert entry is not None
        assert not entry.event.is_set()

    @pytest.mark.asyncio
    async def test_already_resolved(self):
        adapter = _make_adapter()
        # No state for cidGone

        query = AsyncMock()
        query.data = "cl:cidGone:0"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.text = "Pick"
        query.from_user = MagicMock()
        query.from_user.id = "777"
        query.from_user.first_name = "Tester"
        query.answer = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            await adapter._handle_callback_query(update, context)

        query.answer.assert_called_once()
        # Should NOT resolve anything
        assert "already" in query.answer.call_args[1]["text"].lower()

    @pytest.mark.asyncio
    async def test_stale_supergoal_start_button_dispatches_goal_message(self):
        adapter = _make_adapter()
        handled = []

        async def _handler(event):
            handled.append(event)
            return "ok"

        adapter._message_handler = _handler
        query = AsyncMock()
        query.data = "cl:gone:0"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.chat.type = "private"
        query.message.chat.full_name = "Tester"
        query.message.message_id = 44
        query.message.message_thread_id = None
        query.message.text = (
            "Одобрение SuperGoal\n\n"
            "SUPERGOAL_GOAL_BODY: Run `.supergoal/demo` and finish with SUPERGOAL_RUN_COMPLETE.\n\n"
            "1. Start now\n2. Adjust assumption"
        )
        query.from_user = MagicMock()
        query.from_user.id = "777"
        query.from_user.first_name = "Tester"
        query.from_user.full_name = "Tester User"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            await adapter._handle_callback_query(update, context)

        query.answer.assert_called_once()
        assert "Starting" in query.answer.call_args[1]["text"]
        assert len(handled) == 1
        assert handled[0].text == "/goal Run `.supergoal/demo` and finish with SUPERGOAL_RUN_COMPLETE."
        assert handled[0].source.chat_id == "12345"

    def test_supergoal_callback_extractor_strips_launch_metadata(self):
        prompt = (
            "Одобрение SuperGoal\n\n"
            "SUPERGOAL_GOAL_BODY: Run `.supergoal/demo`.\n"
            "DONE_CONDITION:\n"
            "Finish only after SUPERGOAL_RUN_COMPLETE.\n\n"
            "1. Start now\n2. Adjust"
        )

        body = TelegramAdapter._extract_supergoal_body_from_callback_text(prompt)

        assert body == "Run `.supergoal/demo`."

    def test_supergoal_callback_extractor_keeps_numbered_goal_body(self):
        prompt = (
            "SUPERGOAL_GOAL_BODY:\n"
            "Execute these steps:\n"
            "1. Read `.supergoal/STATE.md`.\n"
            "2. Run the next phase.\n\n"
            "Кнопки\n"
            "1. Start now\n2. Adjust"
        )

        body = TelegramAdapter._extract_supergoal_body_from_callback_text(prompt)

        assert "1. Read `.supergoal/STATE.md`." in body
        assert "2. Run the next phase." in body
        assert "Кнопки" not in body

    @pytest.mark.asyncio
    async def test_live_supergoal_start_button_without_runner_does_not_queue_broken_slash(self):
        from tools import clarify_gateway as cm

        adapter = _make_adapter()
        cm.register("cidSG", "sk-sg", "Pick", ["Start now", "Adjust"])
        adapter._clarify_state["cidSG"] = "sk-sg"

        query = AsyncMock()
        query.data = "cl:cidSG:0"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.chat.type = "private"
        query.message.chat.full_name = "Tester"
        query.message.message_id = 45
        query.message.message_thread_id = None
        query.message.text = (
            "Одобрение SuperGoal\n\n"
            "SUPERGOAL_GOAL_BODY: Run `.supergoal/demo` and finish with SUPERGOAL_RUN_COMPLETE.\n\n"
            "1. Start now\n2. Adjust assumption"
        )
        query.from_user = MagicMock()
        query.from_user.id = "777"
        query.from_user.first_name = "Tester"
        query.from_user.full_name = "Tester User"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            await adapter._handle_callback_query(update, context)

        assert "sk-sg" not in adapter._pending_messages
        with cm._lock:
            entry = cm._entries.get("cidSG")
        assert entry is not None
        assert entry.response == "Start now"

    def test_supergroup_topic_callback_uses_group_session_key_shape(self):
        adapter = _make_adapter()

        query = SimpleNamespace()
        query.message = MagicMock()
        query.message.chat_id = -1003971448755
        query.message.chat.type = "supergroup"
        query.message.chat.title = "Sigurd // Dev"
        query.message.message_id = 16624
        query.message.message_thread_id = 1858
        query.from_user = MagicMock()
        query.from_user.id = "617744661"
        query.from_user.full_name = 'Evgeny "Chip"'

        event = adapter._build_supergoal_callback_event(query, "Run `.supergoal/demo`.")

        assert event is not None
        assert event.source.chat_type == "group"
        assert event.source.thread_id == "1858"
        assert build_session_key(event.source) == "agent:main:telegram:group:-1003971448755:1858"

    @pytest.mark.asyncio
    async def test_live_supergoal_start_button_starts_goal_via_bound_runner(self, hermes_home):
        from hermes_cli.goals import GoalManager
        from tools import clarify_gateway as cm

        adapter = _make_adapter()
        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="777",
            chat_id="12345",
            user_name="Tester User",
            chat_type="dm",
        )
        bound = _make_goal_runner(adapter, source)
        cm.register("cidSGRun", bound.session_key, "Pick", ["Start now", "Adjust"])
        adapter._clarify_state["cidSGRun"] = bound.session_key

        query = AsyncMock()
        query.data = "cl:cidSGRun:0"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.chat.type = "private"
        query.message.chat.full_name = "Tester"
        query.message.message_id = 46
        query.message.message_thread_id = None
        query.message.text = (
            "Одобрение SuperGoal\n\n"
            "SUPERGOAL_GOAL_BODY: Run `.supergoal/demo` and finish with SUPERGOAL_RUN_COMPLETE.\n\n"
            "1. Start now\n2. Adjust assumption"
        )
        query.from_user = MagicMock()
        query.from_user.id = "777"
        query.from_user.first_name = "Tester"
        query.from_user.full_name = "Tester User"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            await adapter._handle_callback_query(update, context)

        state = GoalManager(bound.session.session_id).state
        assert state is not None
        assert state.status == "active"
        assert state.goal == "Run `.supergoal/demo` and finish with SUPERGOAL_RUN_COMPLETE."
        assert bound.session_key not in adapter._pending_messages
        assert bound.runner._session_reasoning_overrides[bound.session_key]["effort"] == "xhigh"
        assert bound.runner._consume_goal_callback_started_session(bound.session_key) is True
        assert bound.runner._consume_goal_callback_started_session(bound.session_key) is False
        assert await bound.runner._enqueue_goal_kickoff_prompt(
            session_entry=bound.session,
            source=source,
        ) is True
        queued = adapter._pending_messages[bound.session_key]
        assert queued.text.startswith("[Continuing toward your standing goal]\nGoal: ")
        assert state.goal in queued.text
        assert not queued.text.startswith(state.goal)
        assert not adapter._pending_messages[bound.session_key].text.startswith("/goal")

        with cm._lock:
            entry = cm._entries.get("cidSGRun")
        assert entry is not None
        assert entry.response == "Start now"

    @pytest.mark.asyncio
    async def test_live_supergoal_start_button_uses_clarify_owner_session(self, hermes_home):
        from hermes_cli.goals import GoalManager
        from tools import clarify_gateway as cm

        adapter = _make_adapter()
        source_a = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="111",
            chat_id="-1001",
            user_name="Alice",
            chat_name="Ops group",
            chat_type="group",
        )
        source_b = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="222",
            chat_id="-1001",
            user_name="Bob",
            chat_name="Ops group",
            chat_type="group",
        )
        key_a = build_session_key(source_a)
        key_b = build_session_key(source_b)
        session_a = SessionEntry(
            session_key=key_a,
            session_id=f"clarify-owner-{uuid.uuid4().hex[:8]}",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            platform=Platform.TELEGRAM,
            chat_type="group",
        )
        session_b = SessionEntry(
            session_key=key_b,
            session_id=f"clarify-clicker-{uuid.uuid4().hex[:8]}",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            platform=Platform.TELEGRAM,
            chat_type="group",
        )
        runner = object.__new__(GatewayRunner)
        runner.config = GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")},
        )
        runner.adapters = {Platform.TELEGRAM: adapter}
        runner._queued_events = {}
        runner.session_store = MagicMock()
        runner.session_store._entries = {key_a: session_a, key_b: session_b}
        runner.session_store._ensure_loaded = MagicMock()
        runner.session_store.get_or_create_session.side_effect = AssertionError(
            "forced SuperGoal start must use clarify owner session key"
        )
        adapter._message_handler = runner._handle_message
        cm.register("cidSGOwner", key_a, "Pick", ["Start now", "Adjust"])
        adapter._clarify_state["cidSGOwner"] = key_a

        query = AsyncMock()
        query.data = "cl:cidSGOwner:0"
        query.message = MagicMock()
        query.message.chat_id = -1001
        query.message.chat.type = "group"
        query.message.chat.title = "Ops group"
        query.message.message_id = 47
        query.message.message_thread_id = None
        query.message.text = (
            "Одобрение SuperGoal\n\n"
            "SUPERGOAL_GOAL_BODY: Run `.supergoal/demo` and finish with SUPERGOAL_RUN_COMPLETE.\n\n"
            "1. Start now\n2. Adjust assumption"
        )
        query.from_user = MagicMock()
        query.from_user.id = "222"
        query.from_user.first_name = "Bob"
        query.from_user.full_name = "Bob User"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            await adapter._handle_callback_query(update, context)

        state_a = GoalManager(session_a.session_id).state
        state_b = GoalManager(session_b.session_id).state
        assert state_a is not None
        assert state_a.status == "active"
        assert state_a.goal == "Run `.supergoal/demo` and finish with SUPERGOAL_RUN_COMPLETE."
        assert state_b is None
        assert key_a in adapter._pending_messages
        assert key_b not in adapter._pending_messages

    @pytest.mark.asyncio
    async def test_unauthorized_user_rejected(self):
        from tools import clarify_gateway as cm

        adapter = _make_adapter()
        cm.register("cidC", "sk-auth", "Pick", ["a", "b"])
        adapter._clarify_state["cidC"] = "sk-auth"

        # Hook up a runner that says NOT authorized
        class _DenyRunner:
            async def _handle_message(self, event):
                return None
            def _is_user_authorized(self, source):
                return False

        adapter._message_handler = _DenyRunner()._handle_message

        query = AsyncMock()
        query.data = "cl:cidC:0"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.chat.type = "private"
        query.message.text = "Pick"
        query.from_user = MagicMock()
        query.from_user.id = "999"
        query.from_user.first_name = "Mallory"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()

        await adapter._handle_callback_query(update, context)

        # Must not resolve, must answer with not-authorized message
        with cm._lock:
            entry = cm._entries.get("cidC")
        assert entry is not None
        assert not entry.event.is_set()
        query.answer.assert_called_once()
        assert "not authorized" in query.answer.call_args[1]["text"].lower()
        # State preserved
        assert adapter._clarify_state["cidC"] == "sk-auth"


# ===========================================================================
# Base adapter fallback render — text numbered list
# ===========================================================================

class TestBaseAdapterClarifyFallback:
    """Adapters without button overrides should render numbered text."""

    @pytest.mark.asyncio
    async def test_numbered_text_fallback(self):
        from gateway.platforms.base import BasePlatformAdapter, SendResult

        # Subclass just enough to instantiate
        class _Stub(BasePlatformAdapter):
            name = "stub"

            def __init__(self):
                # Skip base __init__ — we're not exercising it
                self.sent: list = []

            async def connect(self): pass
            async def disconnect(self): pass
            async def send(self, chat_id, content, **kw):
                self.sent.append({"chat_id": chat_id, "content": content})
                return SendResult(success=True, message_id="1")
            async def edit(self, *a, **k): return SendResult(success=False)
            async def get_history(self, *a, **k): return []
            async def get_chat_info(self, *a, **k): return {}

        adapter = _Stub()

        result = await adapter.send_clarify(
            chat_id="c",
            question="Pick a fruit",
            choices=["apple", "banana"],
            clarify_id="x",
            session_key="s",
        )
        assert result.success is True
        assert len(adapter.sent) == 1
        text = adapter.sent[0]["content"]
        assert "Pick a fruit" in text
        assert "1." in text and "apple" in text
        assert "2." in text and "banana" in text

    @pytest.mark.asyncio
    async def test_open_ended_fallback_renders_question_only(self):
        from gateway.platforms.base import BasePlatformAdapter, SendResult

        class _Stub(BasePlatformAdapter):
            name = "stub"
            def __init__(self):
                self.sent: list = []
            async def connect(self): pass
            async def disconnect(self): pass
            async def send(self, chat_id, content, **kw):
                self.sent.append(content)
                return SendResult(success=True, message_id="1")
            async def edit(self, *a, **k): return SendResult(success=False)
            async def get_history(self, *a, **k): return []
            async def get_chat_info(self, *a, **k): return {}

        adapter = _Stub()
        await adapter.send_clarify(
            chat_id="c",
            question="Free form?",
            choices=None,
            clarify_id="x",
            session_key="s",
        )
        assert "Free form?" in adapter.sent[0]
        # No numbered list — choices were empty
        assert "1." not in adapter.sent[0]
