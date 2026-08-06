"""Tests for Telegram adapter early authorization check.

Verifies that unauthorized users are blocked before any text batching,
event building, or response generation occurs.
"""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageType


def _make_adapter(allow_from=None, allowed_chats=None, group_allowed_chats=None, callback_auth=None, **extra_overrides):
    try:
        from plugins.platforms.telegram.adapter import TelegramAdapter
    except ModuleNotFoundError:  # PR branch before Telegram plugin extraction
        from gateway.platforms.telegram import TelegramAdapter

    extra = {}
    if allow_from is not None:
        extra["allow_from"] = allow_from
    if allowed_chats is not None:
        extra["allowed_chats"] = allowed_chats
    if group_allowed_chats is not None:
        extra["group_allowed_chats"] = group_allowed_chats
    extra.update(extra_overrides)

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="fake-token", extra=extra)
    adapter._bot = SimpleNamespace(id=999, username="test_bot")
    adapter._message_handler = AsyncMock()
    adapter._pending_text_batches = {}
    adapter._pending_text_batch_tasks = {}
    adapter._text_batch_delay_seconds = 0.01
    adapter._text_batch_split_delay_seconds = 0.01
    adapter._mention_patterns = adapter._compile_mention_patterns()
    adapter._forum_lock = asyncio.Lock()
    adapter._forum_command_registered = set()
    adapter._active_sessions = {}
    adapter._pending_messages = {}
    if callback_auth is not None:
        adapter._is_callback_user_authorized = callback_auth
    return adapter


def _make_message(text="hello", *, from_user_id=111, chat_id=-100, chat_type="group"):
    return SimpleNamespace(
        message_id=42,
        text=text,
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=None,
        is_topic_message=False,
        chat=SimpleNamespace(id=chat_id, type=chat_type, title="Test", is_forum=False),
        from_user=SimpleNamespace(id=from_user_id, full_name="Test User", first_name="Test"),
        reply_to_message=None,
        date=None,
        location=None,
        photo=None,
        video=None,
        audio=None,
        voice=None,
        document=None,
        sticker=None,
        media_group_id=None,
    )


@pytest.mark.asyncio
async def test_unauthorized_user_blocked_before_event_building():
    """Unauthorized user's message should be blocked before _build_message_event."""
    adapter = _make_adapter(group_allow_from=["222"])  # Only user 222 allowed in groups

    build_called = False
    original_build = adapter._build_message_event

    def track_build(*a, **kw):
        nonlocal build_called
        build_called = True
        return original_build(*a, **kw)

    adapter._build_message_event = track_build

    update = SimpleNamespace(
        update_id=1,
        message=_make_message(from_user_id=111, chat_type="group"),  # User 111 NOT in group_allow_from
        effective_message=None,
    )

    await adapter._handle_text_message(update, SimpleNamespace())

    assert build_called is False, "build_message_event should not be called for unauthorized user"


@pytest.mark.asyncio
async def test_command_from_unauthorized_user_blocked():
    """Commands from unauthorized users should be blocked."""
    adapter = _make_adapter(group_allow_from=["222"])
    adapter.handle_message = AsyncMock()

    update = SimpleNamespace(
        update_id=1,
        message=_make_message(text="/start", from_user_id=111, chat_type="group"),
        effective_message=None,
    )

    await adapter._handle_command(update, SimpleNamespace())

    adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_location_from_unauthorized_user_blocked():
    """Location messages from unauthorized users should be blocked."""
    adapter = _make_adapter(group_allow_from=["222"])

    msg = _make_message(from_user_id=111, chat_type="group")
    msg.text = None
    msg.location = SimpleNamespace(latitude=53.3498, longitude=-6.2603)

    update = SimpleNamespace(
        update_id=1,
        message=msg,
        effective_message=None,
    )

    # Should not raise — just silently return
    await adapter._handle_location_message(update, SimpleNamespace())


def test_is_user_authorized_from_message_allow_from():
    """_is_user_authorized_from_message should respect adapter-level allow_from for DMs."""
    adapter = _make_adapter(allow_from=["111", "222"])

    msg = _make_message(from_user_id=111, chat_type="dm")
    assert adapter._is_user_authorized_from_message(msg) is True

    msg = _make_message(from_user_id=333, chat_type="dm")
    assert adapter._is_user_authorized_from_message(msg) is False


def test_allowlist_dm_with_explicit_pair_behavior_reaches_gateway(monkeypatch):
    """Allowlist + unauthorized_dm_behavior:pair must not early-drop unknown DMs.

    Regression for the gap left by #40863: early intake rejection discarded
    unauthorized DMs before gateway pairing could run, even when the operator
    explicitly set telegram.unauthorized_dm_behavior: pair.
    """
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111")

    class Runner:
        def _is_user_authorized(self, source):
            return source.user_id == "111"

        def _get_unauthorized_dm_behavior(self, platform, *, profile=None):
            assert platform == Platform.TELEGRAM
            return "pair"

def test_group_message_uses_group_allow_from_without_granting_dm_access():
    """Group-only senders must pass intake without inheriting DM access."""
    adapter = _make_adapter(
        allow_from=["111"],
        group_allow_from=["244"],
    )

    group_msg = _make_message(from_user_id=244, chat_type="group")
    assert adapter._is_user_authorized_from_message(group_msg) is True

    dm_msg = _make_message(from_user_id=244, chat_id=244, chat_type="private")
    assert adapter._is_user_authorized_from_message(dm_msg) is False

    platform_wide_group_msg = _make_message(from_user_id=111, chat_type="group")
    assert adapter._is_user_authorized_from_message(platform_wide_group_msg) is True


def test_group_message_uses_per_chat_allowlist_before_global_group_allowlist(monkeypatch):
    """A per-chat group allowlist must override the global group sender list."""
    monkeypatch.setenv(
        "TELEGRAM_PER_CHAT_GROUP_ALLOWED_USERS",
        '{"-100": ["244"]}',
    )
    adapter = _make_adapter(
        allow_from=["111"],
        group_allow_from=["222"],
    )

    allowed_in_target = _make_message(from_user_id=244, chat_id=-100, chat_type="group")
    assert adapter._is_user_authorized_from_message(allowed_in_target) is True

    global_user_in_target = _make_message(from_user_id=222, chat_id=-100, chat_type="group")
    assert adapter._is_user_authorized_from_message(global_user_in_target) is False

    global_user_elsewhere = _make_message(from_user_id=222, chat_id=-200, chat_type="group")
    assert adapter._is_user_authorized_from_message(global_user_elsewhere) is True

    dm_from_per_chat_user = _make_message(from_user_id=244, chat_id=244, chat_type="private")
    assert adapter._is_user_authorized_from_message(dm_from_per_chat_user) is False


def test_apply_yaml_config_exports_per_chat_group_allowlist():
    """YAML per-chat sender policy must reach the adapter/runner auth path."""
    env_key = "TELEGRAM_PER_CHAT_GROUP_ALLOWED_USERS"
    original = os.environ.pop(env_key, None)
    try:
        try:
            from plugins.platforms.telegram.adapter import _apply_yaml_config
        except ModuleNotFoundError:  # PR branch before Telegram plugin extraction
            pytest.skip("Telegram plugin config hook is unavailable")

        mapping = {"-100": ["244"]}
        _apply_yaml_config({}, {"per_chat_group_allow_from": mapping})

        assert os.environ[env_key] == '{"-100": ["244"]}'
    finally:
        os.environ.pop(env_key, None)
        if original is not None:
            os.environ[env_key] = original


def test_is_user_authorized_from_message_wildcard():
    """_is_user_authorized_from_message should accept wildcard '*'."""
    adapter = _make_adapter(allow_from=["*"])

    msg = _make_message(from_user_id=999)
    assert adapter._is_user_authorized_from_message(msg) is True


def test_is_user_authorized_from_message_no_from_user():
    """_is_user_authorized_from_message should return True for messages without from_user."""
    adapter = _make_adapter(allow_from=["111"])

    msg = _make_message()
    msg.from_user = None
    assert adapter._is_user_authorized_from_message(msg) is True


def test_is_user_authorized_from_message_callback():
    """_is_user_authorized_from_message should use _is_callback_user_authorized."""
    adapter = _make_adapter(callback_auth=lambda uid, **_kw: uid == "555")

    msg = _make_message(from_user_id=555)
    assert adapter._is_user_authorized_from_message(msg) is True

    msg = _make_message(from_user_id=666)
    assert adapter._is_user_authorized_from_message(msg) is False


def test_unknown_dm_with_no_allowlist_passes_to_pairing(monkeypatch):
    """Unknown DMs must still reach the gateway pairing flow when no allowlist exists."""
    for key in (
        "TELEGRAM_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_USERS",
        "TELEGRAM_GROUP_ALLOWED_CHATS",
        "TELEGRAM_ALLOW_ALL_USERS",
        "GATEWAY_ALLOWED_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(key, raising=False)

    runner = Runner()
    adapter = _make_adapter()
    adapter._message_handler = runner.handle
    msg = _make_message(from_user_id=999, chat_id=999, chat_type="private")

    assert adapter._is_user_authorized_from_message(msg) is True


def test_allowlist_dm_without_pair_behavior_still_early_rejects(monkeypatch):
    """Allowlist without pairing opt-in keeps the #9337/#40863 silent drop."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111")

    class Runner:
        def _is_user_authorized(self, source):
            return source.user_id == "111"

        def _get_unauthorized_dm_behavior(self, platform, *, profile=None):
            return "ignore"

        async def handle(self, event):
            return None

    runner = Runner()
    adapter = _make_adapter()
    adapter._message_handler = runner.handle
    msg = _make_message(from_user_id=999, chat_id=999, chat_type="private")

    assert adapter._is_user_authorized_from_message(msg) is False


def test_allow_from_dm_with_pair_override_reaches_gateway():
    """Adapter allow_from + unauthorized_dm_behavior:pair still forwards DMs."""
    adapter = _make_adapter(
        allow_from=["111"],
        unauthorized_dm_behavior="pair",
    )
    msg = _make_message(from_user_id=999, chat_id=999, chat_type="dm")
    assert adapter._is_user_authorized_from_message(msg) is True


def test_allowlist_group_with_pair_behavior_still_early_rejects(monkeypatch):
    """Pairing is DM-only — unauthorized group senders stay blocked early."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111")

    class Runner:
        def _is_user_authorized(self, source):
            return source.user_id == "111"

        def _get_unauthorized_dm_behavior(self, platform, *, profile=None):
            return "pair"

        async def handle(self, event):
            return None

    runner = Runner()
    adapter = _make_adapter(group_allow_from=["111"])
    adapter._message_handler = runner.handle
    msg = _make_message(from_user_id=999, chat_id=-100, chat_type="group")

    assert adapter._is_user_authorized_from_message(msg) is False


@pytest.mark.asyncio
async def test_unauthorized_dm_with_pair_behavior_builds_event(monkeypatch):
    """Unknown DM under pair behavior must reach event construction."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111")

    class Runner:
        def _is_user_authorized(self, source):
            return source.user_id == "111"

        def _get_unauthorized_dm_behavior(self, platform, *, profile=None):
            return "pair"

        async def handle(self, event):
            return None

    runner = Runner()
    adapter = _make_adapter()
    adapter._message_handler = runner.handle
    build_called = False
    original_build = adapter._build_message_event

    def track_build(*a, **kw):
        nonlocal build_called
        build_called = True
        return original_build(*a, **kw)

    adapter._build_message_event = track_build
    adapter._enqueue_text_event = lambda event: None
    adapter._ensure_forum_commands = AsyncMock()
    adapter._cache_replied_media = AsyncMock()
    adapter._apply_telegram_group_observe_attribution = lambda event: event
    adapter._clean_bot_trigger_text = lambda text: text
    adapter._should_process_message = lambda *a, **kw: True

    update = SimpleNamespace(
        update_id=1,
        message=_make_message(from_user_id=999, chat_id=999, chat_type="private"),
        effective_message=None,
    )
    await adapter._handle_text_message(update, SimpleNamespace())
    assert build_called is True


def test_runner_auth_gets_group_user_allowlist_context(monkeypatch):
    """Group user allowlists need a group-shaped source, not a DM-shaped one."""
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_USERS", "111")
    seen_sources = []

    class Runner:
        def _is_user_authorized(self, source):
            seen_sources.append(source)
            return source.chat_type == "group" and source.chat_id == "-100" and source.user_id == "111"

        async def handle(self, event):
            return None

    runner = Runner()
    adapter = _make_adapter()
    adapter._message_handler = runner.handle
    msg = _make_message(from_user_id=111, chat_id=-100, chat_type="group")

    assert adapter._is_user_authorized_from_message(msg) is True
    assert seen_sources
    assert seen_sources[0].chat_type == "group"
    assert seen_sources[0].chat_id == "-100"


@pytest.mark.asyncio
async def test_unmentioned_group_location_from_removed_user_not_observed():
    """Removed users must not persist unmentioned group locations into observed context."""
    adapter = _make_adapter(
        group_allow_from=["222"],
        allowed_chats=["-100"],
        group_allowed_chats=["-100"],
        require_mention=True,
        observe_unmentioned_group_messages=True,
    )
    observed = []
    adapter._observe_unmentioned_group_message = lambda *args, **kwargs: observed.append((args, kwargs))

    msg = _make_message(text=None, from_user_id=111, chat_id=-100, chat_type="group")
    msg.location = SimpleNamespace(latitude=53.3498, longitude=-6.2603)
    update = SimpleNamespace(update_id=1, message=msg, effective_message=None)

    await adapter._handle_location_message(update, SimpleNamespace())

    assert observed == []
