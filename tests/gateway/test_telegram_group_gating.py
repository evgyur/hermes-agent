import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from gateway.config import Platform, PlatformConfig, load_gateway_config
from gateway.platforms.base import MessageType
from gateway.session import SessionSource


def _make_adapter(
    require_mention=None,
    require_mention_chats=None,
    require_mention_topics=None,
    reply_trigger_disabled_chats=None,
    free_response_chats=None,
    free_response_topics=None,
    private_chats=None,
    public_chats=None,
    mention_patterns=None,
    exclusive_bot_mentions=None,
    ignore_other_bot_replies_chats=None,
    ignored_threads=None,
    allowed_topics=None,
    allow_from=None,
    group_allow_from=None,
    allowed_chats=None,
    group_allowed_chats=None,
    guest_mode=None,
    observe_unmentioned_group_messages=None,
    bot_username="hermes_bot",
):
    from gateway.platforms.telegram import TelegramAdapter

    extra = {}
    if require_mention is not None:
        extra["require_mention"] = require_mention
    if require_mention_chats is not None:
        extra["require_mention_chats"] = require_mention_chats
    if require_mention_topics is not None:
        extra["require_mention_topics"] = require_mention_topics
    if reply_trigger_disabled_chats is not None:
        extra["reply_trigger_disabled_chats"] = reply_trigger_disabled_chats
    if free_response_chats is not None:
        extra["free_response_chats"] = free_response_chats
    if free_response_topics is not None:
        extra["free_response_topics"] = free_response_topics
    if private_chats is not None:
        extra["private_chats"] = private_chats
    else:
        extra["private_chats"] = []
    if public_chats is not None:
        extra["public_chats"] = public_chats
    else:
        extra["public_chats"] = []
    if mention_patterns is not None:
        extra["mention_patterns"] = mention_patterns
    if exclusive_bot_mentions is not None:
        extra["exclusive_bot_mentions"] = exclusive_bot_mentions
    if ignore_other_bot_replies_chats is not None:
        extra["ignore_other_bot_replies_chats"] = ignore_other_bot_replies_chats
    if ignored_threads is not None:
        extra["ignored_threads"] = ignored_threads
    if allowed_topics is not None:
        extra["allowed_topics"] = allowed_topics
    else:
        # Keep unit tests isolated from TELEGRAM_ALLOWED_TOPICS in the parent
        # environment; production adapters without this explicit key still fall
        # back to the env var.
        extra["allowed_topics"] = []
    if allow_from is not None:
        extra["allow_from"] = allow_from
    if group_allow_from is not None:
        extra["group_allow_from"] = group_allow_from
    if allowed_chats is not None:
        extra["allowed_chats"] = allowed_chats
    else:
        # Keep unit tests isolated from TELEGRAM_ALLOWED_CHATS in the parent
        # environment; production adapters without this explicit key still fall
        # back to the env var.
        extra["allowed_chats"] = []
    if group_allowed_chats is not None:
        extra["group_allowed_chats"] = group_allowed_chats
    else:
        extra["group_allowed_chats"] = []
    if guest_mode is not None:
        extra["guest_mode"] = guest_mode
    if observe_unmentioned_group_messages is not None:
        extra["observe_unmentioned_group_messages"] = observe_unmentioned_group_messages

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="***", extra=extra)
    adapter._bot = SimpleNamespace(id=999, username=bot_username)
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
    # Trigger-gating tests don't exercise the allowlist gate (added by
    # #23795 + #24468).  Force-authorize all senders so the trigger logic
    # under test runs.  Without this, every fake message hits the new
    # fail-closed auth path and gets dropped before trigger evaluation.
    adapter._is_callback_user_authorized = lambda user_id, **_kw: True
    return adapter


def _group_message(
    text="hello",
    *,
    chat_id=-100,
    from_user_id=111,
    from_user_name="Alice Example",
    thread_id=None,
    reply_to_bot=False,
    reply_to_other_bot=False,
    entities=None,
    caption=None,
    caption_entities=None,
):
    reply_to_message = None
    if reply_to_bot:
        reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=999, is_bot=True), message_id=10, text="previous bot reply", caption=None)
    elif reply_to_other_bot:
        reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=555, is_bot=True), message_id=11, text="other bot reply", caption=None)
    return SimpleNamespace(
        message_id=42,
        text=text,
        caption=caption,
        entities=entities or [],
        caption_entities=caption_entities or [],
        message_thread_id=thread_id,
        is_topic_message=thread_id is not None,
        chat=SimpleNamespace(id=chat_id, type="group", title="Test Group", is_forum=thread_id is not None),
        from_user=SimpleNamespace(id=from_user_id, full_name=from_user_name, first_name=from_user_name.split()[0]),
        reply_to_message=reply_to_message,
        date=None,
    )


def _dm_message(
    text="hello",
    *,
    from_user_id=111,
    reply_to_bot=False,
    reply_to_user_id=None,
    reply_to_text="previous bot reply",
    reply_to_message_id=10,
    entities=None,
    caption=None,
    caption_entities=None,
):
    reply_to_message = None
    if reply_to_bot:
        reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=999), message_id=reply_to_message_id, text=reply_to_text, caption=None)
    elif reply_to_user_id is not None:
        reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=reply_to_user_id), message_id=reply_to_message_id, text=reply_to_text, caption=None)
    return SimpleNamespace(
        message_id=43,
        text=text,
        caption=caption,
        entities=entities or [],
        caption_entities=caption_entities or [],
        message_thread_id=None,
        chat=SimpleNamespace(id=from_user_id, type="private", full_name="Alice Example", title=None, is_forum=False),
        from_user=SimpleNamespace(id=from_user_id, full_name="Alice Example", first_name="Alice"),
        reply_to_message=reply_to_message,
        date=None,
    )


def _business_dm_message(text="hello", *, from_user_id=111, business_connection_id="biz-123", **kwargs):
    message = _dm_message(text, from_user_id=from_user_id, **kwargs)
    message.business_connection_id = business_connection_id
    return message


def _mention_entity(text, mention="@hermes_bot"):
    offset = text.index(mention)
    return SimpleNamespace(type="mention", offset=offset, length=len(mention))


def _mention_entities(text, mentions):
    return [_mention_entity(text, mention) for mention in mentions]


def _bot_command_entity(text, command):
    """Entity Telegram emits for a ``/cmd`` or ``/cmd@botname`` token.

    Telegram parses slash commands server-side. For ``/cmd@botname`` the
    client does NOT emit a separate ``mention`` entity — the whole span
    is a single ``bot_command`` entity.
    """
    offset = text.index(command)
    return SimpleNamespace(type="bot_command", offset=offset, length=len(command))


def test_group_messages_can_be_opened_via_config():
    adapter = _make_adapter(require_mention=False)

    assert adapter._should_process_message(_group_message("hello everyone")) is True


def test_unmentioned_group_messages_can_be_observed_without_dispatching():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-100"],
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=1001,
            message=_group_message("side chatter"),
            effective_message=None,
        )

        await adapter._handle_text_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert len(store.messages) == 1
        session_id, message, skip_db = store.messages[0]
        assert session_id == "telegram-group-session"
        assert skip_db is False
        assert message["role"] == "user"
        assert message["content"] == "[Alice Example|111]\nside chatter"
        assert message["observed"] is True
        assert message["message_id"] == "42"
        assert store.sources[0].chat_id == "-100"
        assert store.sources[0].chat_type == "group"
        assert store.sources[0].user_id is None
        assert store.sources[0].user_name is None

    asyncio.run(_run())


def test_observed_group_context_uses_shared_source_and_prompt_for_later_mentions():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-100"],
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        adapter._session_store = _FakeSessionStore()
        text = "@hermes_bot what did Alice say?"
        msg = _group_message(
            text,
            from_user_id=222,
            from_user_name="Bob Example",
            entities=[_mention_entity(text)],
        )
        event = adapter._build_message_event(msg, MessageType.TEXT, update_id=1003)
        event.text = adapter._clean_bot_trigger_text(event.text)
        event.channel_prompt = "Existing topic prompt"

        event = adapter._apply_telegram_group_observe_attribution(event)

        assert event.source.chat_id == "-100"
        assert event.source.chat_type == "group"
        assert event.source.user_id is None
        assert event.source.user_name is None
        assert event.text == "[Bob Example|222]\nwhat did Alice say?"
        assert "Existing topic prompt" in event.channel_prompt
        assert "observed Telegram group context" in event.channel_prompt
        assert "current new message" in event.channel_prompt

    asyncio.run(_run())


def test_observed_group_context_replays_as_current_message_context_not_user_turns():
    from gateway.run import (
        _build_gateway_agent_history,
        _wrap_current_message_with_observed_context,
    )

    history = [
        {"role": "session_meta", "content": "tool defs"},
        {"role": "user", "content": "[Alice|111]\nAcha que dá fazer estoque?", "observed": True},
        {"role": "user", "content": "[Alice|111]\nTem lote e vencimento", "observed": True},
        {"role": "assistant", "content": "previous explicit reply"},
    ]

    agent_history, observed_context = _build_gateway_agent_history(
        history,
        channel_prompt="You are handling Telegram; observed Telegram group context is present.",
    )
    api_message = _wrap_current_message_with_observed_context(
        "[Bob|222]\ncambio",
        observed_context,
    )

    assert agent_history == [{"role": "assistant", "content": "previous explicit reply"}]
    assert "[Observed Telegram group context - context only, not requests]" in api_message
    assert "[Current addressed message - answer only this" in api_message
    assert "Acha que dá fazer estoque?" in api_message
    assert "Tem lote e vencimento" in api_message
    assert api_message.endswith("[Bob|222]\ncambio")


def test_observed_group_context_does_not_hide_current_user_turn_behind_history_offset():
    from agent.agent_runtime_helpers import repair_message_sequence
    from gateway.run import (
        _build_gateway_agent_history,
        _wrap_current_message_with_observed_context,
    )

    history = [
        {"role": "user", "content": "[Alice|111]\nAcha que dá fazer estoque?", "observed": True},
    ]
    agent_history, observed_context = _build_gateway_agent_history(
        history,
        channel_prompt="observed Telegram group context",
    )
    api_message = _wrap_current_message_with_observed_context("[Bob|222]\ncambio", observed_context)
    messages = list(agent_history) + [{"role": "user", "content": api_message}]

    repair_message_sequence(object(), messages)

    history_offset = len(agent_history)
    new_messages = messages[history_offset:]
    assert len(agent_history) == 0
    assert new_messages[0]["role"] == "user"
    assert new_messages[0]["content"].endswith("[Bob|222]\ncambio")


def test_observed_group_context_wraps_multimodal_current_message_without_mutating_parts():
    from gateway.run import _wrap_current_message_with_observed_context

    original = [
        {"type": "text", "text": "[Bob|222]\nsee this image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]

    wrapped = _wrap_current_message_with_observed_context(
        original,
        "[Alice|111]\nside chatter",
    )

    assert original[0]["text"] == "[Bob|222]\nsee this image"
    assert wrapped[0]["text"].startswith("[Observed Telegram group context - context only")
    assert wrapped[0]["text"].endswith("[Bob|222]\nsee this image")
    assert wrapped[1] == original[1]


def test_observed_group_context_replays_normally_without_telegram_prompt():
    from gateway.run import _build_gateway_agent_history

    history = [
        {"role": "user", "content": "[Alice|111]\nside chatter", "observed": True},
    ]

    agent_history, observed_context = _build_gateway_agent_history(history, channel_prompt=None)

    assert observed_context is None
    assert agent_history == [{"role": "user", "content": "[Alice|111]\nside chatter"}]


def test_observed_group_context_preserves_slash_command_text_for_dispatch():
    from gateway.platforms.base import MessageEvent, MessageType, Platform, SessionSource

    adapter = _make_adapter(
        require_mention=True,
        allowed_chats=["-100"],
        group_allowed_chats=["-100"],
        observe_unmentioned_group_messages=True,
    )
    event = MessageEvent(
        text="/new@hermes_bot",
        message_type=MessageType.COMMAND,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-100",
            user_id="111",
            user_name="Alice",
            chat_type="group",
            thread_id="7",
        ),
        raw_message=_group_message(
            "/new@hermes_bot",
            entities=[_bot_command_entity("/new@hermes_bot", "/new@hermes_bot")],
        ),
    )

    attributed = adapter._apply_telegram_group_observe_attribution(event)

    assert attributed.text == "/new@hermes_bot"
    assert attributed.get_command() == "new"
    assert attributed.source.user_id is None
    assert "observed Telegram group context" in attributed.channel_prompt


def test_unmentioned_group_observe_requires_chat_allowlist_for_shared_context():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=1004,
            message=_group_message("side chatter"),
            effective_message=None,
        )

        await adapter._handle_text_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert store.messages == []

    asyncio.run(_run())


def test_shared_group_observe_source_is_authorized_by_group_allowed_chats(monkeypatch):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-100",
        chat_type="group",
        user_id=None,
        user_name=None,
    )

    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_CHATS", "-100")
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHATS", raising=False)

    assert runner._is_user_authorized(source) is True


def test_unmentioned_group_observe_respects_chat_allowlist():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-200"],
            group_allowed_chats=["-200"],
            observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=1002,
            message=_group_message("side chatter", chat_id=-201),
            effective_message=None,
        )

        await adapter._handle_text_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert store.messages == []

    asyncio.run(_run())


class _FakeSessionEntry:
    session_id = "telegram-group-session"


class _FakeSessionStore:
    def __init__(self):
        self.sources = []
        self.messages = []

    def get_or_create_session(self, source):
        self.sources.append(source)
        return _FakeSessionEntry()

    def append_to_transcript(self, session_id, message, skip_db=False):
        self.messages.append((session_id, message, skip_db))


def test_group_messages_can_require_direct_trigger_via_config():
    adapter = _make_adapter(require_mention=True)

    assert adapter._should_process_message(_group_message("hello everyone")) is False
    assert adapter._should_process_message(_group_message("hi @hermes_bot", entities=[_mention_entity("hi @hermes_bot")])) is True
    assert adapter._should_process_message(_group_message("replying", reply_to_bot=True)) is True
    # Commands must also respect require_mention when it is enabled
    assert adapter._should_process_message(_group_message("/status"), is_command=True) is False
    # Telegram's group command menu sends ``/cmd@botname`` as a single
    # ``bot_command`` entity spanning the whole token (no separate mention
    # entity). We must accept it so the menu works when require_mention is on.
    assert adapter._should_process_message(
        _group_message(
            "/status@hermes_bot",
            entities=[_bot_command_entity("/status@hermes_bot", "/status@hermes_bot")],
        ),
        is_command=True,
    ) is True
    # A bot_command entity addressed at a different bot must not satisfy
    # the mention gate — Telegram groups can host multiple bots that
    # register the same command name.
    assert adapter._should_process_message(
        _group_message(
            "/status@other_bot",
            entities=[_bot_command_entity("/status@other_bot", "/status@other_bot")],
        ),
        is_command=True,
    ) is False
    # Bare ``/status`` (no @botname) must still be dropped in groups with
    # require_mention=True — Telegram delivers it only when the bot's
    # privacy mode is off, and even then we should not respond unless the
    # user explicitly addressed the bot.
    assert adapter._should_process_message(
        _group_message("/status", entities=[_bot_command_entity("/status", "/status")]),
        is_command=True,
    ) is False
    # And commands still pass unconditionally when require_mention is disabled
    adapter_no_mention = _make_adapter(require_mention=False)
    assert adapter_no_mention._should_process_message(_group_message("/status"), is_command=True) is True


def test_free_response_chat_ignores_replies_to_other_bots_when_scoped():
    adapter = _make_adapter(
        require_mention=False,
        free_response_chats=["-200"],
        ignore_other_bot_replies_chats=["-200"],
    )

    assert adapter._should_process_message(_group_message("plain", chat_id=-200)) is True
    assert adapter._should_process_message(
        _group_message("reply to self", chat_id=-200, reply_to_bot=True)
    ) is True
    assert adapter._should_process_message(
        _group_message("reply to other", chat_id=-200, reply_to_other_bot=True)
    ) is False
    assert adapter._should_process_message(
        _group_message(
            "@hermes_bot join this reply",
            chat_id=-200,
            reply_to_other_bot=True,
            entities=[_mention_entity("@hermes_bot join this reply")],
        )
    ) is True
    assert adapter._should_process_message(
        _group_message("reply elsewhere", chat_id=-201, reply_to_other_bot=True)
    ) is True


def test_live_plugin_adapter_honors_per_chat_mention_reply_gate():
    """The plugin adapter loaded by production must honor require_mention_chats."""
    from plugins.platforms.telegram.adapter import TelegramAdapter as PluginTelegramAdapter

    adapter = object.__new__(PluginTelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(
        enabled=True,
        token="***",
        extra={
            "require_mention": False,
            "require_mention_chats": ["-100123"],
            "free_response_chats": [],
            "allowed_chats": [],
            "allowed_topics": [],
            "ignored_threads": [],
            "group_allowed_chats": [],
            "private_chats": [],
            "public_chats": [],
        },
    )
    adapter._bot = SimpleNamespace(id=999, username="hermes_bot")
    adapter._dm_topic_chat_ids = set()
    adapter._mention_patterns = adapter._compile_mention_patterns()

    assert adapter._should_process_message(
        _group_message("ordinary public chatter", chat_id=-100123)
    ) is False
    assert adapter._should_process_message(
        _group_message("replying to Hermes", chat_id=-100123, reply_to_bot=True)
    ) is True
    assert adapter._should_process_message(
        _group_message(
            "hi @hermes_bot",
            chat_id=-100123,
            entities=[_mention_entity("hi @hermes_bot")],
        )
    ) is True
    assert adapter._should_process_message(
        _group_message("ordinary private-group chatter", chat_id=-100456)
    ) is True


def test_private_dms_remain_unrestricted_without_explicit_chat_policy():
    adapter = _make_adapter(require_mention=False)

    assert adapter._should_process_message(_dm_message("hello there")) is True
    assert adapter._should_process_message(
        _dm_message("hi @hermes_bot", entities=[_mention_entity("hi @hermes_bot")])
    ) is True
    assert adapter._should_process_message(_dm_message("replying", reply_to_bot=True)) is True
    assert adapter._should_process_message(_dm_message("Sigurd, status")) is True


def test_business_messages_are_wake_word_or_mention_only_by_default():
    adapter = _make_adapter(require_mention=False)
    adapter.config.extra["business"] = {"enabled": True, "trigger_words": ["Sigurd"]}

    assert adapter._message_matches_business_trigger(_dm_message("plain reply", reply_to_bot=True)) is False
    assert adapter._message_matches_business_trigger(_dm_message("Sigurd, check this")) is True
    assert adapter._message_matches_business_trigger(
        _dm_message("hi @hermes_bot", entities=[_mention_entity("hi @hermes_bot")])
    ) is True


def test_business_dm_dispatch_requires_trigger():
    adapter = _make_adapter(require_mention=False)
    adapter.config.extra["business"] = {"enabled": True, "trigger_words": ["Sigurd"]}

    assert adapter._should_process_message(_business_dm_message("plain customer reply")) is False
    assert adapter._should_process_message(_business_dm_message("Sigurd, посчитай")) is True
    assert adapter._should_process_message(
        _business_dm_message("hi @hermes_bot", entities=[_mention_entity("hi @hermes_bot")])
    ) is True


def test_business_dm_ignored_user_id_suppresses_reflected_human_account_echo():
    adapter = _make_adapter(require_mention=False)
    adapter.config.extra["business"] = {
        "enabled": True,
        "trigger_words": ["Sigurd"],
        "ignore_user_ids": ["617744661"],
    }

    assert adapter._should_process_message(
        _business_dm_message("Sigurd, echo from delegated human account", from_user_id=617744661)
    ) is False
    assert adapter._should_process_message(
        _business_dm_message("Sigurd, real customer", from_user_id=111)
    ) is True


def test_business_dm_private_chat_owner_plain_echoes_are_fail_closed_but_wake_words_dispatch():
    adapter = _make_adapter(
        require_mention=False,
        private_chats=["617744661", "111"],
        allow_from=["617744661"],
    )
    adapter.config.extra["business"] = {"enabled": True, "trigger_words": ["Sigurd", "Сигурд"]}

    assert adapter._should_process_message(
        _business_dm_message("plain mirrored owner echo", from_user_id=617744661)
    ) is False
    assert adapter._should_process_message(
        _business_dm_message("Sigurd, mirrored owner command", from_user_id=617744661)
    ) is True
    assert adapter._should_process_message(
        _business_dm_message("Сигурд, проверь", from_user_id=617744661)
    ) is True
    assert adapter._should_process_message(
        _business_dm_message("Sigurd, real customer", from_user_id=111)
    ) is True


def test_business_dm_private_chat_owner_reply_to_business_assistant_echo_dispatches(monkeypatch):
    adapter = _make_adapter(
        require_mention=False,
        private_chats=["617744661"],
        allow_from=["617744661"],
    )
    adapter.config.extra["business"] = {
        "enabled": True,
        "trigger_words": ["Sigurd"],
        "allow_reply_trigger": True,
    }
    outbound = "Да, смогу. Но возврат — финансовое действие, поэтому сначала сверю оплату и покажу тебе точную строку."
    monkeypatch.setattr(
        adapter,
        "_recent_outbound_echo_entries",
        lambda chat_id, now=None: [(9999999999.0, adapter._self_echo_normalize(outbound), "fingerprint")],
    )

    assert adapter._should_process_message(
        _business_dm_message(
            "сам всё выясни",
            from_user_id=617744661,
            reply_to_user_id=617744661,
            reply_to_text=outbound,
        )
    ) is True


def test_business_reply_to_durable_sent_id_is_owned_after_echo_ttl(monkeypatch):
    adapter = _make_adapter(require_mention=False)
    message = _business_dm_message(
        "customer@example.com",
        from_user_id=70001,
        reply_to_user_id=70002,
        reply_to_text="Готово. Пришли Google-почту — открою адресно.",
        reply_to_message_id=90001,
    )
    from gateway import rich_sent_store

    monkeypatch.setattr(
        rich_sent_store,
        "lookup",
        lambda chat_id, message_id: "Готово. Пришли Google-почту — открою адресно."
        if (str(chat_id), str(message_id)) == ("70001", "90001")
        else None,
    )
    monkeypatch.setattr(adapter, "_recent_outbound_echo_entries", lambda *_args, **_kwargs: [])

    assert adapter._is_reply_to_own_outbound_text(message) is True


def test_business_owner_reply_recovers_cached_connection_for_response(monkeypatch):
    adapter = _make_adapter(
        require_mention=False,
        private_chats=["70002"],
        allow_from=["70002"],
    )
    message = _dm_message(
        "customer@example.com дай доступ",
        from_user_id=70002,
        reply_to_user_id=70002,
        reply_to_text="Готово. Пришли Google-почту — открою адресно.",
        reply_to_message_id=90001,
    )
    message.chat.id = 70001
    from gateway import rich_sent_store

    monkeypatch.setattr(
        rich_sent_store,
        "lookup",
        lambda chat_id, message_id: "Готово. Пришли Google-почту — открою адресно."
        if (str(chat_id), str(message_id)) == ("70001", "90001")
        else None,
    )
    monkeypatch.setattr(
        adapter,
        "_known_business_connection_id",
        lambda chat_id: "biz-123" if str(chat_id) == "70001" else None,
        raising=False,
    )

    event = adapter._build_message_event(message, MessageType.TEXT)

    assert event.source.business_connection_id == "biz-123"
    assert event.source.external_safe_mode is True


def test_business_dm_external_reply_to_assistant_bypasses_owner_only_private_policy(monkeypatch):
    adapter = _make_adapter(
        require_mention=False,
        private_chats=["70002"],
        allow_from=["70002"],
    )
    adapter.config.extra["business"] = {
        "enabled": True,
        "trigger_words": ["Sigurd"],
        "allow_reply_trigger": True,
    }
    monkeypatch.setattr(adapter, "_is_reply_to_own_outbound_text", lambda _message: True)

    assert adapter._should_process_message(
        _business_dm_message(
            "customer@example.com",
            from_user_id=70001,
            reply_to_user_id=70002,
            reply_to_text="Готово. Пришли Google-почту — открою адресно.",
            reply_to_message_id=90001,
        )
    ) is True


def test_business_dm_external_wake_word_does_not_bypass_owner_only_private_policy():
    adapter = _make_adapter(
        require_mention=False,
        private_chats=["70002"],
        allow_from=["70002"],
    )
    adapter.config.extra["business"] = {
        "enabled": True,
        "trigger_words": ["Sigurd"],
        "allow_reply_trigger": True,
    }

    assert adapter._should_process_message(
        _business_dm_message("Sigurd, unrelated customer", from_user_id=70001)
    ) is False


def test_business_dm_private_chat_owner_plain_echo_guard_survives_legacy_knob_but_wake_word_dispatches():
    adapter = _make_adapter(
        require_mention=False,
        private_chats=["617744661"],
        allow_from=["617744661"],
    )
    adapter.config.extra["business"] = {
        "enabled": True,
        "trigger_words": ["Sigurd"],
        "ignore_owner_echoes": False,
    }

    assert adapter._should_process_message(
        _business_dm_message("plain reflected owner echo", from_user_id=617744661)
    ) is False
    assert adapter._should_process_message(
        _business_dm_message("Sigurd, reflected owner command", from_user_id=617744661)
    ) is True


def test_business_dm_explicit_ignore_user_id_still_suppresses_trigger():
    adapter = _make_adapter(
        require_mention=False,
        private_chats=["617744661"],
        allow_from=["617744661"],
    )
    adapter.config.extra["business"] = {
        "enabled": True,
        "trigger_words": ["Sigurd"],
        "ignore_user_ids": ["617744661"],
    }

    assert adapter._should_process_message(
        _business_dm_message("Sigurd, explicit ignored user", from_user_id=617744661)
    ) is False


def test_business_voice_auto_transcribe_bypasses_wake_word_gate(monkeypatch):
    async def _run():
        adapter = _make_adapter(require_mention=False)
        adapter.config.extra["business"] = {
            "enabled": True,
            "trigger_words": ["Sigurd"],
            "auto_transcribe_voice": True,
        }
        adapter.send = AsyncMock()
        adapter._message_handler = AsyncMock()

        class FakeVoice:
            file_size = 128

            async def get_file(self):
                return SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(b"ogg")))

        message = _business_dm_message("", from_user_id=222, business_connection_id="biz-voice")
        message.voice = FakeVoice()
        message.audio = None
        update = SimpleNamespace(update_id=5001, message=message, effective_message=None)

        monkeypatch.setattr("gateway.platforms.telegram.cache_audio_from_bytes", lambda *_args, **_kw: "/tmp/voice.ogg")
        monkeypatch.setattr(
            "tools.transcription_tools.transcribe_audio",
            lambda path: {"success": True, "transcript": "Привет, это тест", "provider": "groq"},
        )

        await adapter._handle_media_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        adapter.send.assert_awaited_once()
        assert adapter.send.await_args is not None
        args, kwargs = adapter.send.await_args
        assert args[0] == "222"
        assert "Привет, это тест" in args[1]
        assert kwargs["reply_to"] == "43"
        assert kwargs["metadata"] == {"business_connection_id": "biz-voice"}

    asyncio.run(_run())


def test_business_voice_auto_transcribe_is_config_gated():
    adapter = _make_adapter(require_mention=False)
    adapter.config.extra["business"] = {"enabled": True, "trigger_words": ["Sigurd"]}
    message = _business_dm_message("", from_user_id=222, business_connection_id="biz-voice")
    message.voice = SimpleNamespace(file_size=128)
    message.audio = None

    assert adapter._should_auto_transcribe_business_voice(message) is False


def test_business_message_source_keeps_business_connection_id():
    adapter = _make_adapter(require_mention=False)
    message = _business_dm_message("Sigurd, ping", from_user_id=95948382)

    event = adapter._build_message_event(message, MessageType.TEXT, update_id=123)

    assert event.source.chat_id == "95948382"
    assert event.source.user_id == "95948382"
    assert event.source.business_connection_id == "biz-123"
    assert event.source.external_safe_mode is True


def test_business_bot_dialog_mirror_is_dropped_even_with_trigger():
    adapter = _make_adapter(require_mention=False)
    adapter.config.extra["business"] = {"enabled": True, "trigger_words": ["Sigurd"], "allow_reply_trigger": True}
    message = _business_dm_message("Sigurd, ping", from_user_id=617744661, reply_to_bot=True)
    # Telegram Business can mirror Chip's direct DM with this bot as a Business
    # update whose chat id is the bot id. Processing it duplicates the normal DM
    # and can render the reply as the connected human/business account.
    message.chat.id = 999

    assert adapter._should_process_message(message) is False


def test_bot_dialog_mirror_without_business_id_is_dropped():
    adapter = _make_adapter(require_mention=False)
    message = _business_dm_message(
        "🎙 Расшифровка голосового: тест",
        from_user_id=617744661,
    )
    message.business_connection_id = None
    # Telegram can omit business_connection_id on the reflected text update.
    # A legitimate private user chat can never have chat.id equal to this bot's id.
    message.chat.id = 999

    assert adapter._should_process_message(message) is False


def test_business_third_party_wake_still_dispatches():
    adapter = _make_adapter(require_mention=False)
    adapter.config.extra["business"] = {"enabled": True, "trigger_words": ["Sigurd"]}
    message = _business_dm_message("Sigurd, ping", from_user_id=95948382)

    assert adapter._should_process_message(message) is True


def test_business_bot_dialog_mirror_voice_is_not_auto_transcribed():
    adapter = _make_adapter(require_mention=False)
    adapter.config.extra["business"] = {"enabled": True, "auto_transcribe_voice": True}
    message = _business_dm_message("", from_user_id=617744661, business_connection_id="biz-voice")
    message.chat.id = 999
    message.voice = SimpleNamespace(file_size=128)
    message.audio = None

    assert adapter._should_auto_transcribe_business_voice(message) is False


def test_business_reply_trigger_requires_explicit_opt_in():
    adapter = _make_adapter(require_mention=False)
    adapter.config.extra["business"] = {
        "enabled": True,
        "trigger_words": ["Sigurd"],
        "allow_reply_trigger": True,
    }

    assert adapter._message_matches_business_trigger(_dm_message("plain reply", reply_to_bot=True)) is True


def test_business_free_response_chat_bypasses_trigger_requirement():
    adapter = _make_adapter(require_mention=False)
    adapter.config.extra["business"] = {
        "enabled": True,
        "trigger_words": ["Sigurd"],
        "free_response_chats": ["6442556885"],
    }

    assert adapter._message_matches_business_trigger(_dm_message("plain message", from_user_id=6442556885)) is True
    assert adapter._message_matches_business_trigger(_dm_message("plain message", from_user_id=617744661)) is False


def test_free_response_chats_bypass_mention_requirement():
    adapter = _make_adapter(require_mention=True, free_response_chats=["-200"])

    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200)) is True
    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-201)) is False


def test_free_response_topics_bypass_mention_requirement_only_for_that_topic():
    adapter = _make_adapter(
        require_mention=False,
        require_mention_chats=["-200"],
        private_chats=["617744661"],
        public_chats=["-200"],
        free_response_topics=["-200:777"],
    )

    assert adapter._should_process_message(_group_message("plain", chat_id=-200, thread_id=777)) is True
    assert adapter._should_process_message(_group_message("plain", chat_id=-200, thread_id=778)) is False
    assert adapter._should_process_message(
        _group_message(
            "hi @hermes_bot",
            chat_id=-200,
            thread_id=778,
            entities=[_mention_entity("hi @hermes_bot")],
        )
    ) is True
    assert adapter._should_process_message(_group_message("plain", chat_id=-201, thread_id=777)) is False


def test_require_mention_topics_override_free_response_chat_only_for_that_topic():
    adapter = _make_adapter(
        require_mention=False,
        free_response_chats=["-200"],
        require_mention_topics=["-200:777"],
    )

    assert adapter._should_process_message(_group_message("plain", chat_id=-200, thread_id=777)) is False
    assert adapter._should_process_message(
        _group_message("reply", chat_id=-200, thread_id=777, reply_to_bot=True)
    ) is True
    assert adapter._should_process_message(
        _group_message(
            "hi @hermes_bot",
            chat_id=-200,
            thread_id=777,
            entities=[_mention_entity("hi @hermes_bot")],
        )
    ) is True
    assert adapter._should_process_message(_group_message("plain", chat_id=-200, thread_id=778)) is True


def test_config_bridges_require_mention_topics(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  require_mention_topics:\n"
        "    - -1003770669948:14804\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_REQUIRE_MENTION_TOPICS", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_REQUIRE_MENTION_TOPICS"] == "-1003770669948:14804"
    assert config.platforms[Platform.TELEGRAM].extra["require_mention_topics"] == [
        "-1003770669948:14804"
    ]


def test_config_bridges_ignore_other_bot_replies_chats(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  ignore_other_bot_replies_chats:\n"
        "    - '-200'\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_IGNORE_OTHER_BOT_REPLIES_CHATS", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_IGNORE_OTHER_BOT_REPLIES_CHATS"] == "-200"
    assert config.platforms[Platform.TELEGRAM].extra["ignore_other_bot_replies_chats"] == ["-200"]


def test_require_mention_chats_force_direct_trigger_only_for_listed_chat():
    adapter = _make_adapter(require_mention=False, require_mention_chats=["-200"])

    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200)) is False
    assert adapter._should_process_message(
        _group_message(
            "hi @hermes_bot",
            chat_id=-200,
            entities=[_mention_entity("hi @hermes_bot")],
        )
    ) is True
    assert adapter._should_process_message(_group_message("replying", chat_id=-200, reply_to_bot=True)) is True
    assert adapter._should_process_message(_group_message("/status", chat_id=-200), is_command=True) is False
    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-201)) is True


def test_reply_trigger_disabled_chats_require_direct_mention_in_listed_chat():
    adapter = _make_adapter(
        require_mention=False,
        require_mention_chats=["-200"],
        reply_trigger_disabled_chats=["-200"],
    )

    assert adapter._should_process_message(
        _group_message("replying", chat_id=-200, reply_to_bot=True)
    ) is False
    assert adapter._should_process_message(
        _group_message(
            "hi @hermes_bot",
            chat_id=-200,
            reply_to_bot=True,
            entities=[_mention_entity("hi @hermes_bot")],
        )
    ) is True
    assert adapter._should_process_message(
        _group_message("replying", chat_id=-201, reply_to_bot=True)
    ) is True


def test_reply_trigger_disabled_chat_still_allows_configured_wake_pattern():
    adapter = _make_adapter(
        require_mention=False,
        require_mention_chats=["-200"],
        reply_trigger_disabled_chats=["-200"],
        mention_patterns=[r"^\s*human20\b"],
    )

    assert adapter._should_process_message(
        _group_message("human20 status", chat_id=-200, reply_to_bot=True)
    ) is True



def test_explicit_chat_policy_private_public_and_unknown_chats():
    adapter = _make_adapter(
        require_mention=False,
        private_chats=["12345", "-300"],
        public_chats=["-200"],
    )

    assert adapter._should_process_message(_dm_message("hello", from_user_id=12345)) is True
    assert adapter._should_process_message(_dm_message("hello", from_user_id=99999)) is False
    assert adapter._should_process_message(_group_message("plain private", chat_id=-300)) is True
    assert adapter._should_process_message(_group_message("plain public", chat_id=-200)) is False
    assert adapter._should_process_message(
        _group_message(
            "hi @hermes_bot",
            chat_id=-200,
            entities=[_mention_entity("hi @hermes_bot")],
        )
    ) is True
    assert adapter._should_process_message(_group_message("reply public", chat_id=-200, reply_to_bot=True)) is True
    assert adapter._should_process_message(_group_message("unknown", chat_id=-400)) is False

def test_guest_mode_allows_only_direct_mentions_outside_allowed_chats():
    adapter = _make_adapter(
        require_mention=True,
        allowed_chats=["-200"],
        guest_mode=True,
        mention_patterns=[r"^\s*chompy\b"],
    )

    mentioned = _group_message(
        "hi @hermes_bot",
        chat_id=-201,
        entities=[_mention_entity("hi @hermes_bot")],
    )
    assert adapter._should_process_message(mentioned) is True
    assert adapter._should_process_message(_group_message("reply", chat_id=-201, reply_to_bot=True)) is False
    assert adapter._should_process_message(_group_message("chompy status", chat_id=-201)) is False
    assert adapter._should_process_message(_group_message("hello", chat_id=-201)) is False


def test_guest_mode_defaults_to_false_for_allowed_chat_bypass():
    adapter = _make_adapter(require_mention=True, allowed_chats=["-200"], guest_mode=False)

    mentioned = _group_message(
        "hi @hermes_bot",
        chat_id=-201,
        entities=[_mention_entity("hi @hermes_bot")],
    )
    assert adapter._should_process_message(mentioned) is False


def test_guest_mode_mention_dropped_in_ignored_thread():
    """A guest mention in an ignored thread is still dropped — thread gate runs first."""
    adapter = _make_adapter(
        require_mention=True,
        allowed_chats=["-200"],
        guest_mode=True,
        ignored_threads=[42],
    )
    mentioned = _group_message(
        "hi @hermes_bot",
        chat_id=-201,
        entities=[_mention_entity("hi @hermes_bot")],
        thread_id=42,
    )
    assert adapter._should_process_message(mentioned) is False


def test_ignored_threads_drop_group_messages_before_other_gates():
    adapter = _make_adapter(require_mention=False, free_response_chats=["-200"], ignored_threads=[31, "42"])

    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200, thread_id=31)) is False
    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200, thread_id=42)) is False
    assert adapter._should_process_message(_group_message("hello everyone", chat_id=-200, thread_id=99)) is True


def test_allowed_topics_drop_other_forum_topics_before_other_gates():
    adapter = _make_adapter(require_mention=False, allowed_chats=["-100"], allowed_topics=["8"])

    assert adapter._should_process_message(_group_message("hello", chat_id=-100, thread_id=8)) is True
    assert adapter._should_process_message(_group_message("hello", chat_id=-100, thread_id=11)) is False
    assert adapter._should_process_message(
        _group_message("hi @hermes_bot", chat_id=-100, thread_id=11, entities=[_mention_entity("hi @hermes_bot")])
    ) is False


def test_allowed_topics_do_not_filter_dms():
    adapter = _make_adapter(require_mention=False, allowed_topics=["8"])

    assert adapter._should_process_message(_dm_message("hello")) is True


def test_allowed_topics_treat_missing_thread_as_general_topic():
    adapter = _make_adapter(require_mention=False, allowed_topics=["1"])

    assert adapter._should_process_message(_group_message("hello", thread_id=None)) is True
    assert adapter._should_process_message(_group_message("hello", thread_id=8)) is False


def test_regex_mention_patterns_allow_custom_wake_words():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"^\s*chompy\b"])

    assert adapter._should_process_message(_group_message("chompy status")) is True
    assert adapter._should_process_message(_group_message("   chompy help")) is True
    assert adapter._should_process_message(_group_message("hey chompy")) is False


def test_invalid_regex_patterns_are_ignored():
    adapter = _make_adapter(require_mention=True, mention_patterns=[r"(", r"^\s*chompy\b"])

    assert adapter._should_process_message(_group_message("chompy status")) is True
    assert adapter._should_process_message(_group_message("hello everyone")) is False


def test_config_bridges_telegram_group_settings(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  require_mention: true\n"
        "  guest_mode: true\n"
        "  exclusive_bot_mentions: true\n"
        "  observe_unmentioned_group_messages: true\n"
        "  mention_patterns:\n"
        "    - \"^\\\\s*chompy\\\\b\"\n"
        "  require_mention_chats:\n"
        "    - \"-456\"\n"
        "  private_chats:\n"
        "    - \"12345\"\n"
        "  public_chats:\n"
        "    - \"-789\"\n"
        "  free_response_chats:\n"
        "    - \"-123\"\n"
        "  allowed_chats:\n"
        "    - \"-100\"\n"
        "  group_allowed_chats:\n"
        "    - \"-100\"\n"
        "  allowed_topics:\n"
        "    - 8\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_REQUIRE_MENTION", raising=False)
    monkeypatch.delenv("TELEGRAM_MENTION_PATTERNS", raising=False)
    monkeypatch.delenv("TELEGRAM_EXCLUSIVE_BOT_MENTIONS", raising=False)
    monkeypatch.delenv("TELEGRAM_GUEST_MODE", raising=False)
    monkeypatch.delenv("TELEGRAM_REQUIRE_MENTION_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_PRIVATE_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_PUBLIC_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_OBSERVE_UNMENTIONED_GROUP_MESSAGES", raising=False)
    monkeypatch.delenv("TELEGRAM_FREE_RESPONSE_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_GROUP_ALLOWED_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_TOPICS", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_REQUIRE_MENTION"] == "true"
    assert __import__("os").environ["TELEGRAM_GUEST_MODE"] == "true"
    assert __import__("os").environ["TELEGRAM_OBSERVE_UNMENTIONED_GROUP_MESSAGES"] == "true"
    assert __import__("os").environ["TELEGRAM_EXCLUSIVE_BOT_MENTIONS"] == "true"
    assert json.loads(__import__("os").environ["TELEGRAM_MENTION_PATTERNS"]) == [r"^\s*chompy\b"]
    assert __import__("os").environ["TELEGRAM_REQUIRE_MENTION_CHATS"] == "-456"
    assert __import__("os").environ["TELEGRAM_PRIVATE_CHATS"] == "12345"
    assert __import__("os").environ["TELEGRAM_PUBLIC_CHATS"] == "-789"
    assert __import__("os").environ["TELEGRAM_FREE_RESPONSE_CHATS"] == "-123"
    assert __import__("os").environ["TELEGRAM_ALLOWED_CHATS"] == "-100"
    assert __import__("os").environ["TELEGRAM_GROUP_ALLOWED_CHATS"] == "-100"
    assert __import__("os").environ["TELEGRAM_ALLOWED_TOPICS"] == "8"
    tg_cfg = config.platforms.get(Platform.TELEGRAM)
    assert tg_cfg is not None
    assert tg_cfg.extra.get("guest_mode") is True
    assert tg_cfg.extra.get("require_mention_chats") == ["-456"]
    assert tg_cfg.extra.get("private_chats") == ["12345"]
    assert tg_cfg.extra.get("public_chats") == ["-789"]
    assert tg_cfg.extra.get("allowed_chats") == ["-100"]
    assert tg_cfg.extra.get("group_allowed_chats") == ["-100"]
    assert tg_cfg.extra.get("allowed_topics") == [8]
    assert tg_cfg.extra.get("exclusive_bot_mentions") is True
    assert tg_cfg.extra.get("observe_unmentioned_group_messages") is True


def test_config_bridges_telegram_user_allowlists(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  allow_from:\n"
        "    - \"111\"\n"
        "    - \"222\"\n"
        "  group_allow_from:\n"
        "    - \"333\"\n"
        "  group_allowed_chats:\n"
        "    - \"-100\"\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("TELEGRAM_GROUP_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("TELEGRAM_GROUP_ALLOWED_CHATS", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_ALLOWED_USERS"] == "111,222"
    assert __import__("os").environ["TELEGRAM_GROUP_ALLOWED_USERS"] == "333"
    assert __import__("os").environ["TELEGRAM_GROUP_ALLOWED_CHATS"] == "-100"


def test_config_env_overrides_telegram_user_allowlists(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  allow_from: \"111\"\n"
        "  group_allow_from: \"222\"\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "999")
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_USERS", "888")

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_ALLOWED_USERS"] == "999"
    assert __import__("os").environ["TELEGRAM_GROUP_ALLOWED_USERS"] == "888"


def test_dm_allow_from_is_enforced_by_gateway_authorization_not_trigger_gate():
    adapter = _make_adapter(allow_from=["111", "222"])

    assert adapter._should_process_message(_dm_message("hello", from_user_id=111)) is True
    assert adapter._should_process_message(_dm_message("hello", from_user_id=333)) is True


def test_group_allow_from_is_enforced_by_gateway_authorization_not_trigger_gate():
    adapter = _make_adapter(group_allow_from=["111"])

    assert adapter._should_process_message(_group_message("hello", from_user_id=333)) is True


def test_top_level_require_mention_bridges_to_telegram(monkeypatch, tmp_path):
    """require_mention at the config.yaml top level (alongside group_sessions_per_user)
    must behave identically to telegram.require_mention: true (#3979).
    """
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    # Intentionally no "telegram:" section — keys are at the top level.
    (hermes_home / "config.yaml").write_text(
        "require_mention: true\n"
        "group_sessions_per_user: true\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_REQUIRE_MENTION", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ.get("TELEGRAM_REQUIRE_MENTION") == "true"

    # The adapter's extra dict must also carry the setting so that
    # _telegram_require_mention() works even without the env var.
    tg_cfg = config.platforms.get(__import__("gateway.config", fromlist=["Platform"]).Platform.TELEGRAM)
    if tg_cfg is not None:
        assert tg_cfg.extra.get("require_mention") is True


def test_top_level_require_mention_does_not_override_telegram_section(monkeypatch, tmp_path):
    """When telegram.require_mention is explicitly set, top-level require_mention
    must not override it (platform-specific config takes precedence).
    """
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "require_mention: true\n"
        "telegram:\n"
        "  require_mention: false\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_REQUIRE_MENTION", raising=False)

    config = load_gateway_config()

    assert config is not None
    # The telegram-specific "false" must win over the top-level "true".
    assert __import__("os").environ.get("TELEGRAM_REQUIRE_MENTION") == "false"


def test_config_bridges_reply_trigger_disabled_chats(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  reply_trigger_disabled_chats:\n"
        "    - -1003770669948\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_REPLY_TRIGGER_DISABLED_CHATS", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_REPLY_TRIGGER_DISABLED_CHATS"] == "-1003770669948"


def test_config_bridges_telegram_ignored_threads(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "telegram:\n"
        "  ignored_threads:\n"
        "    - 31\n"
        "    - \"42\"\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("TELEGRAM_IGNORED_THREADS", raising=False)

    config = load_gateway_config()

    assert config is not None
    assert __import__("os").environ["TELEGRAM_IGNORED_THREADS"] == "31,42"


# ---------------------------------------------------------------------------
# Helpers for location / media observe+attribution tests
# ---------------------------------------------------------------------------

def _group_location_message(
    *,
    chat_id=-100,
    from_user_id=111,
    from_user_name="Alice Example",
    lat=37.7749,
    lon=-122.4194,
):
    return SimpleNamespace(
        message_id=50,
        text=None,
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=None,
        is_topic_message=False,
        chat=SimpleNamespace(id=chat_id, type="group", title="Test Group", is_forum=False),
        from_user=SimpleNamespace(
            id=from_user_id, full_name=from_user_name,
            first_name=from_user_name.split()[0],
        ),
        reply_to_message=None,
        date=None,
        location=SimpleNamespace(latitude=lat, longitude=lon),
        venue=None,
        sticker=None,
        photo=None,
        video=None,
        audio=None,
        voice=None,
        document=None,
    )


def _group_voice_message(
    *,
    chat_id=-100,
    from_user_id=111,
    from_user_name="Alice Example",
    caption=None,
):
    return SimpleNamespace(
        message_id=51,
        text=None,
        caption=caption,
        entities=[],
        caption_entities=[],
        message_thread_id=None,
        is_topic_message=False,
        chat=SimpleNamespace(id=chat_id, type="group", title="Test Group", is_forum=False),
        from_user=SimpleNamespace(
            id=from_user_id, full_name=from_user_name,
            first_name=from_user_name.split()[0],
        ),
        reply_to_message=None,
        date=None,
        location=None,
        venue=None,
        sticker=None,
        photo=None,
        video=None,
        audio=None,
        voice=SimpleNamespace(
            get_file=AsyncMock(side_effect=Exception("simulated download failure"))
        ),
        document=None,
    )


# ---------------------------------------------------------------------------
# Observe + attribution parity: location messages
# ---------------------------------------------------------------------------

def test_unmentioned_location_message_observed_in_group():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-100"],
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=2001,
            message=_group_location_message(),
            effective_message=None,
        )

        await adapter._handle_location_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert len(store.messages) == 1
        _, message, _ = store.messages[0]
        assert message["observed"] is True
        assert store.sources[0].user_id is None

    asyncio.run(_run())


def test_triggered_location_message_uses_shared_session_in_observe_mode():
    async def _run():
        adapter = _make_adapter(
            require_mention=False,
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        adapter.handle_message = AsyncMock()
        update = SimpleNamespace(
            update_id=2002,
            message=_group_location_message(),
            effective_message=None,
        )

        await adapter._handle_location_message(update, SimpleNamespace())

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.call_args[0][0]
        assert event.source.user_id is None
        assert "[Alice Example|111]" in event.text

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Observe + attribution parity: media messages (voice as representative)
# ---------------------------------------------------------------------------

def test_unmentioned_voice_message_observed_in_group():
    async def _run():
        adapter = _make_adapter(
            require_mention=True,
            allowed_chats=["-100"],
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        update = SimpleNamespace(
            update_id=3001,
            message=_group_voice_message(),
            effective_message=None,
        )

        await adapter._handle_media_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert len(store.messages) == 1
        _, message, _ = store.messages[0]
        assert message["observed"] is True
        assert store.sources[0].user_id is None

    asyncio.run(_run())


def test_triggered_voice_message_uses_shared_session_in_observe_mode():
    async def _run():
        adapter = _make_adapter(
            require_mention=False,
            group_allowed_chats=["-100"],
            observe_unmentioned_group_messages=True,
        )
        adapter.handle_message = AsyncMock()
        update = SimpleNamespace(
            update_id=3002,
            message=_group_voice_message(caption="check this audio"),
            effective_message=None,
        )

        await adapter._handle_media_message(update, SimpleNamespace())

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.call_args[0][0]
        assert event.source.user_id is None
        assert "[Alice Example|111]" in event.text

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Replied-to media caching
# ---------------------------------------------------------------------------

def test_text_reply_to_photo_caches_referenced_media(monkeypatch, tmp_path):
    async def _run():
        adapter = _make_adapter(require_mention=False)
        adapter.handle_message = AsyncMock()
        cached_path = tmp_path / "reply_photo.png"
        monkeypatch.setattr(
            "gateway.platforms.base.cache_image_from_bytes",
            lambda _data, ext=".jpg": str(cached_path),
        )
        file_obj = SimpleNamespace(
            file_path="photos/replied.png",
            download_as_bytearray=AsyncMock(return_value=bytearray(b"\x89PNG\r\n\x1a\n reply")),
        )
        photo = SimpleNamespace(file_size=1234, get_file=AsyncMock(return_value=file_obj))
        replied = SimpleNamespace(
            message_id=51,
            text=None,
            caption=None,
            photo=[photo],
            video=None,
            audio=None,
            voice=None,
            document=None,
        )
        msg = _group_message("what's in this image?", reply_to_bot=False)
        msg.reply_to_message = replied
        update = SimpleNamespace(update_id=3010, message=msg, effective_message=msg)

        await adapter._handle_text_message(update, SimpleNamespace())
        await asyncio.sleep(0.05)

        adapter.handle_message.assert_awaited_once()
        await_args = adapter.handle_message.await_args
        assert await_args is not None
        event = await_args.args[0]
        assert event.reply_to_message_id == "51"
        assert event.media_urls == [str(cached_path)]
        assert event.media_types == ["image/png"]
        assert event.message_type == MessageType.PHOTO

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Observed-media caching (unmentioned group attachments)
# ---------------------------------------------------------------------------

def _group_photo_message(*, chat_id=-100, caption="Veja esta foto", file_size=1024):
    file_obj = SimpleNamespace(
        file_path="photos/observed.png",
        download_as_bytearray=AsyncMock(return_value=bytearray(b"\x89PNG\r\n\x1a\n observed")),
    )
    photo = SimpleNamespace(file_size=file_size, get_file=AsyncMock(return_value=file_obj))
    return SimpleNamespace(
        message_id=52, text=None, caption=caption, entities=[], caption_entities=[],
        message_thread_id=None, is_topic_message=False,
        chat=SimpleNamespace(id=chat_id, type="group", title="Test Group", is_forum=False),
        from_user=SimpleNamespace(id=111, full_name="Alice Example", first_name="Alice"),
        reply_to_message=None, date=None, location=None, venue=None,
        sticker=None, photo=[photo], video=None, audio=None, voice=None, document=None,
    )


def _group_document_message(*, chat_id=-100, caption="Este arquivo", document=None):
    file_obj = SimpleNamespace(
        file_path="documents/report.pdf",
        download_as_bytearray=AsyncMock(return_value=bytearray(b"%PDF observed bytes")),
    )
    document = document or SimpleNamespace(
        file_name="RESULTADO BIOLOGICO - PROTOCOLO 103- URBAN.pdf",
        mime_type="application/pdf", file_size=1024,
        get_file=AsyncMock(return_value=file_obj),
    )
    return SimpleNamespace(
        message_id=53, text=None, caption=caption, entities=[], caption_entities=[],
        message_thread_id=None, is_topic_message=False,
        chat=SimpleNamespace(id=chat_id, type="group", title="Test Group", is_forum=False),
        from_user=SimpleNamespace(id=111, full_name="Alice Example", first_name="Alice"),
        reply_to_message=None, date=None, location=None, venue=None,
        sticker=None, photo=None, video=None, audio=None, voice=None, document=document,
    )


def test_unmentioned_photo_observed_with_cached_path(monkeypatch, tmp_path):
    async def _run():
        adapter = _make_adapter(
            require_mention=True, allowed_chats=["-100"],
            group_allowed_chats=["-100"], observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        cached_path = tmp_path / "img_abc_observed.png"
        monkeypatch.setattr(
            "gateway.platforms.base.cache_image_from_bytes",
            lambda _data, ext=".jpg": str(cached_path),
        )
        update = SimpleNamespace(update_id=3003, message=_group_photo_message(), effective_message=None)

        await adapter._handle_media_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert len(store.messages) == 1
        _, message, _ = store.messages[0]
        assert message["observed"] is True
        assert "Veja esta foto" in message["content"]
        assert "image" in message["content"]
        assert str(cached_path) in message["content"]
        assert store.sources[0].user_id is None

    asyncio.run(_run())


def test_unmentioned_document_observed_with_cached_path(monkeypatch, tmp_path):
    async def _run():
        adapter = _make_adapter(
            require_mention=True, allowed_chats=["-100"],
            group_allowed_chats=["-100"], observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        cached_path = tmp_path / "doc_abc_report.pdf"
        monkeypatch.setattr(
            "gateway.platforms.base.cache_document_from_bytes",
            lambda _data, _filename: str(cached_path),
        )
        update = SimpleNamespace(update_id=3004, message=_group_document_message(), effective_message=None)

        await adapter._handle_media_message(update, SimpleNamespace())

        adapter._message_handler.assert_not_awaited()
        assert len(store.messages) == 1
        _, message, _ = store.messages[0]
        assert message["observed"] is True
        assert "Este arquivo" in message["content"]
        assert str(cached_path) in message["content"]

    asyncio.run(_run())


def test_unmentioned_large_document_observed_without_download(monkeypatch):
    async def _run():
        adapter = _make_adapter(
            require_mention=True, allowed_chats=["-100"],
            group_allowed_chats=["-100"], observe_unmentioned_group_messages=True,
        )
        adapter._max_doc_bytes = 100
        store = _FakeSessionStore()
        adapter._session_store = store
        cache_doc = Mock(return_value="/tmp/huge.pdf")
        monkeypatch.setattr("gateway.platforms.base.cache_document_from_bytes", cache_doc)
        document = SimpleNamespace(
            file_name="huge.pdf", mime_type="application/pdf",
            file_size=101, get_file=AsyncMock(),
        )
        update = SimpleNamespace(
            update_id=3005, message=_group_document_message(document=document), effective_message=None,
        )

        await adapter._handle_media_message(update, SimpleNamespace())

        cache_doc.assert_not_called()
        document.get_file.assert_not_called()
        _, message, _ = store.messages[0]
        assert "too large" in message["content"]
        assert "/tmp/huge.pdf" not in message["content"]

    asyncio.run(_run())


def test_unmentioned_unsupported_document_observed_without_caching(monkeypatch):
    async def _run():
        adapter = _make_adapter(
            require_mention=True, allowed_chats=["-100"],
            group_allowed_chats=["-100"], observe_unmentioned_group_messages=True,
        )
        store = _FakeSessionStore()
        adapter._session_store = store
        cache_doc = Mock(return_value="/tmp/malware.exe")
        monkeypatch.setattr("gateway.platforms.base.cache_document_from_bytes", cache_doc)
        file_obj = SimpleNamespace(
            file_path="documents/malware.exe",
            download_as_bytearray=AsyncMock(return_value=bytearray(b"MZ")),
        )
        document = SimpleNamespace(
            file_name="malware.exe", mime_type="application/x-msdownload",
            file_size=2, get_file=AsyncMock(return_value=file_obj),
        )
        update = SimpleNamespace(
            update_id=3006, message=_group_document_message(document=document), effective_message=None,
        )

        await adapter._handle_media_message(update, SimpleNamespace())

        cache_doc.assert_not_called()
        _, message, _ = store.messages[0]
        assert "unsupported" in message["content"].lower()

    asyncio.run(_run())
