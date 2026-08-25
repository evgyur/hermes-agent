"""Generic security regressions for Telegram Business recipient isolation."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.session import SessionSource, build_session_key
from plugins.platforms.telegram.adapter import TelegramAdapter


OWNER_ID = "700000111"
BLOCKED_CUSTOMER_ID = "700000321"
SAFE_CUSTOMER_ID = "700000654"
BOT_ID = 700000999
BUSINESS_CONNECTION_ID = "business-connection-test"


def _adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={
                "allow_from": [OWNER_ID],
                "business": {
                    "enabled": True,
                    "send_as_account": True,
                    "ignore_owner_echoes": True,
                    "owner_user_ids": [OWNER_ID],
                    "allow_reply_trigger": True,
                    "trigger_words": ["Sigurd", "Сигурд"],
                    "allowed_chats": [],
                    "free_response_chats": [],
                    "auto_transcribe_voice": True,
                },
            },
        )
    )
    adapter._bot = SimpleNamespace(id=BOT_ID, username="testhermesbot")
    return adapter


def _business_message(
    *,
    chat_id: str = BLOCKED_CUSTOMER_ID,
    from_user_id: str = OWNER_ID,
    text: str | None = None,
    voice: object | None = None,
    reply_to_message: object | None = None,
    sender_business_bot: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        caption=None,
        voice=voice,
        audio=None,
        document=None,
        photo=None,
        video=None,
        video_note=None,
        sticker=None,
        animation=None,
        location=None,
        venue=None,
        contact=None,
        chat=SimpleNamespace(
            id=int(chat_id),
            type="private",
            title=None,
            full_name="External Customer",
        ),
        from_user=SimpleNamespace(
            id=int(from_user_id),
            is_bot=False,
            full_name=(
                "Account Owner"
                if from_user_id == OWNER_ID
                else "External Customer"
            ),
        ),
        sender_business_bot=sender_business_bot,
        business_connection_id=BUSINESS_CONNECTION_ID,
        message_thread_id=None,
        reply_to_message=reply_to_message,
        message_id=803217,
        date=None,
    )


def _business_update(message: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        update_id=9001,
        business_message=message,
        effective_message=message,
        message=None,
    )


def test_unset_mock_reply_does_not_forge_business_connection() -> None:
    """Loose MagicMock attributes must not turn an ordinary DM into Business traffic."""
    message = MagicMock()
    message.business_connection_id = None

    assert TelegramAdapter._telegram_supplied_business_connection_id(message) is None


@pytest.mark.asyncio
async def test_owner_auto_transcript_in_customer_chat_is_dropped_before_dispatch() -> None:
    """The exact incident envelope must never become a Hermes turn."""
    adapter = _adapter()
    adapter.handle_message = AsyncMock()
    adapter._enqueue_text_event = MagicMock()
    adapter._cache_replied_media = AsyncMock()
    message = _business_message(
        text="автоматическая транскрипция личного голосового сообщения",
        reply_to_message=SimpleNamespace(
            message_id=822709,
            text=None,
            caption=None,
            voice=SimpleNamespace(file_id="voice-incident"),
            business_connection_id=BUSINESS_CONNECTION_ID,
        ),
    )

    await adapter._handle_text_message(_business_update(message), SimpleNamespace())

    adapter.handle_message.assert_not_awaited()
    adapter._enqueue_text_event.assert_not_called()
    adapter._cache_replied_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_text_requires_explicit_prefix_in_customer_chat() -> None:
    adapter = _adapter()
    adapter.handle_message = AsyncMock()
    adapter._enqueue_text_event = MagicMock()
    adapter._cache_replied_media = AsyncMock()
    message = _business_message(text="обычный личный разговор с клиентом")

    await adapter._handle_text_message(_business_update(message), SimpleNamespace())

    adapter.handle_message.assert_not_awaited()
    adapter._enqueue_text_event.assert_not_called()
    adapter._cache_replied_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_message_in_business_bot_chat_routes_to_plain_owner_dm() -> None:
    """The owner's direct bot chat must not be mistaken for a customer lane."""
    adapter = _adapter()
    adapter._enqueue_text_event = MagicMock()
    adapter._cache_replied_media = AsyncMock()
    message = _business_message(
        chat_id=str(BOT_ID),
        text="привет",
    )

    await adapter._handle_text_message(_business_update(message), SimpleNamespace())

    adapter._enqueue_text_event.assert_called_once()
    event = adapter._enqueue_text_event.call_args.args[0]
    assert event.text == "привет"
    assert event.source.chat_id == OWNER_ID
    assert event.source.user_id == OWNER_ID
    assert event.source.business_connection_id is None
    assert event.source.external_safe_mode is False
    assert ":telegram:dm:" in build_session_key(event.source)


@pytest.mark.asyncio
async def test_owner_explicit_business_wake_preserves_connection_and_safe_lane() -> None:
    adapter = _adapter()
    adapter._enqueue_text_event = MagicMock()
    adapter._cache_replied_media = AsyncMock()
    message = _business_message(
        chat_id=SAFE_CUSTOMER_ID,
        text="Sigurd, проверь только этот вопрос",
    )

    await adapter._handle_text_message(_business_update(message), SimpleNamespace())

    adapter._enqueue_text_event.assert_called_once()
    event = adapter._enqueue_text_event.call_args.args[0]
    assert event.text == "проверь только этот вопрос"
    assert event.source.chat_id == SAFE_CUSTOMER_ID
    assert event.source.user_id == OWNER_ID
    assert event.source.business_connection_id == BUSINESS_CONNECTION_ID
    assert event.source.external_safe_mode is True
    assert ":telegram:business:" in build_session_key(event.source)


@pytest.mark.asyncio
async def test_owner_wake_uses_cached_business_connection_when_update_omits_it() -> None:
    adapter = _adapter()
    adapter._enqueue_text_event = MagicMock()
    adapter._cache_replied_media = AsyncMock()
    adapter._known_business_connection_id = MagicMock(
        return_value=BUSINESS_CONNECTION_ID
    )
    message = _business_message(
        chat_id=SAFE_CUSTOMER_ID,
        text="Sigurd, продолжи работу",
    )
    message.business_connection_id = None

    await adapter._handle_text_message(_business_update(message), SimpleNamespace())

    event = adapter._enqueue_text_event.call_args.args[0]
    assert event.source.business_connection_id == BUSINESS_CONNECTION_ID
    assert event.source.external_safe_mode is True
    assert ":telegram:business:" in build_session_key(event.source)


def test_cached_business_connections_are_isolated_by_transport_profile(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter_a = _adapter()
    adapter_b = _adapter()
    adapter_a._owner_profile = "transport-a"
    adapter_b._owner_profile = "transport-b"

    adapter_a._remember_business_connection_id(SAFE_CUSTOMER_ID, "biz-a")
    adapter_b._remember_business_connection_id(SAFE_CUSTOMER_ID, "biz-b")

    assert adapter_a._known_business_connection_id(SAFE_CUSTOMER_ID) == "biz-a"
    assert adapter_b._known_business_connection_id(SAFE_CUSTOMER_ID) == "biz-b"


def test_business_source_round_trip_and_connection_isolation() -> None:
    source_a = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=BLOCKED_CUSTOMER_ID,
        chat_type="dm",
        user_id=OWNER_ID,
        business_connection_id="connection-a",
        external_safe_mode=True,
    )
    source_b = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=BLOCKED_CUSTOMER_ID,
        chat_type="dm",
        user_id=OWNER_ID,
        business_connection_id="connection-b",
        external_safe_mode=True,
    )

    restored = SessionSource.from_dict(source_a.to_dict())

    assert restored.business_connection_id == "connection-a"
    assert restored.external_safe_mode is True
    assert build_session_key(source_a) != build_session_key(source_b)


@pytest.mark.asyncio
async def test_plain_bot_dm_to_operator_denied_customer_fails_closed(
    tmp_path, monkeypatch
) -> None:
    registry = tmp_path / "telegram-egress-deny.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "blocked_user_ids": [BLOCKED_CUSTOMER_ID],
                "blocked_usernames": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_TELEGRAM_EGRESS_DENY_FILE", str(registry))
    monkeypatch.setenv("HERMES_TELEGRAM_EGRESS_DENY_REQUIRED", "1")
    adapter = _adapter()
    adapter._rich_send_disabled = True
    adapter._bot = MagicMock(id=BOT_ID, username="testhermesbot")
    adapter._bot.send_message = AsyncMock(
        return_value=SimpleNamespace(message_id=99)
    )

    result = await adapter.send(
        BLOCKED_CUSTOMER_ID,
        "private answer",
        metadata={},
    )

    assert result.success is False
    assert result.error == "telegram_recipient_denied"
    adapter._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_bot_dm_to_non_denied_non_allowlisted_customer_fails_closed() -> None:
    adapter = _adapter()
    adapter._rich_send_disabled = True
    adapter._bot = MagicMock(id=BOT_ID, username="testhermesbot")
    adapter._bot.send_message = AsyncMock(
        return_value=SimpleNamespace(message_id=199)
    )

    result = await adapter.send(SAFE_CUSTOMER_ID, "private answer", metadata={})

    assert result.success is False
    assert result.error == "unsafe_plain_telegram_dm"
    adapter._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_document_denies_non_allowlisted_plain_dm_before_wire(tmp_path) -> None:
    adapter = _adapter()
    adapter._bot = MagicMock(id=BOT_ID, username="testhermesbot")
    adapter._bot.send_document = AsyncMock(
        return_value=SimpleNamespace(message_id=200)
    )
    payload = tmp_path / "private.txt"
    payload.write_text("private", encoding="utf-8")

    result = await adapter.send_document(
        SAFE_CUSTOMER_ID,
        str(payload),
        metadata={},
    )

    assert result.success is False
    assert result.error == "unsafe_plain_telegram_dm"
    adapter._bot.send_document.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "surface",
    [
        "edit_message",
        "delete_message",
        "send_draft",
        "send_update_prompt",
        "send_exec_approval",
        "send_slash_confirm",
        "send_clarify",
        "send_model_picker",
        "send_choice_picker",
        "send_voice",
        "send_multiple_images",
        "send_image_file",
        "send_video",
        "send_image",
        "send_animation",
        "send_typing",
    ],
)
async def test_all_public_egress_surfaces_deny_nonallowlisted_plain_dm_before_wire(
    surface, tmp_path, monkeypatch
) -> None:
    adapter = _adapter()
    bot = MagicMock(id=BOT_ID, username="testhermesbot")
    wire_methods = (
        "send_message",
        "edit_message_text",
        "delete_message",
        "send_message_draft",
        "send_voice",
        "send_audio",
        "send_media_group",
        "send_photo",
        "send_document",
        "send_video",
        "send_animation",
        "send_chat_action",
        "do_api_request",
    )
    for method in wire_methods:
        setattr(bot, method, AsyncMock(return_value=SimpleNamespace(message_id=201)))
    adapter._bot = bot
    adapter._rich_send_disabled = True

    media = tmp_path / "media.bin"
    media.write_bytes(b"test")
    monkeypatch.setattr(
        "plugins.platforms.telegram.adapter._probe_voice_duration_seconds",
        lambda _path: 1,
    )

    calls = {
        "edit_message": lambda: adapter.edit_message(
            SAFE_CUSTOMER_ID, "1", "edit", metadata={}
        ),
        "delete_message": lambda: adapter.delete_message(
            SAFE_CUSTOMER_ID, "1", metadata={}
        ),
        "send_draft": lambda: adapter.send_draft(
            SAFE_CUSTOMER_ID, 1, "draft", metadata={}
        ),
        "send_update_prompt": lambda: adapter.send_update_prompt(
            SAFE_CUSTOMER_ID, "update?", metadata={}
        ),
        "send_exec_approval": lambda: adapter.send_exec_approval(
            SAFE_CUSTOMER_ID, "echo hi", "session", metadata={}
        ),
        "send_slash_confirm": lambda: adapter.send_slash_confirm(
            SAFE_CUSTOMER_ID, "title", "message", "session", "confirm", metadata={}
        ),
        "send_clarify": lambda: adapter.send_clarify(
            SAFE_CUSTOMER_ID, "question", ["yes"], "clarify", "session", metadata={}
        ),
        "send_model_picker": lambda: adapter.send_model_picker(
            SAFE_CUSTOMER_ID, [], "model", "provider", "session", lambda *_: None, metadata={}
        ),
        "send_choice_picker": lambda: adapter.send_choice_picker(
            SAFE_CUSTOMER_ID, "title", [], "session", lambda *_: None, metadata={}
        ),
        "send_voice": lambda: adapter.send_voice(
            SAFE_CUSTOMER_ID, str(media), metadata={}
        ),
        "send_multiple_images": lambda: adapter.send_multiple_images(
            SAFE_CUSTOMER_ID, [("https://example.com/a.png", "a")], metadata={}
        ),
        "send_image_file": lambda: adapter.send_image_file(
            SAFE_CUSTOMER_ID, str(media), metadata={}
        ),
        "send_video": lambda: adapter.send_video(
            SAFE_CUSTOMER_ID, str(media), metadata={}
        ),
        "send_image": lambda: adapter.send_image(
            SAFE_CUSTOMER_ID, "https://example.com/a.png", metadata={}
        ),
        "send_animation": lambda: adapter.send_animation(
            SAFE_CUSTOMER_ID, "https://example.com/a.gif", metadata={}
        ),
        "send_typing": lambda: adapter.send_typing(SAFE_CUSTOMER_ID, metadata={}),
    }

    result = await calls[surface]()

    if hasattr(result, "success"):
        assert result.success is False
        assert result.error == "unsafe_plain_telegram_dm"
    for method in wire_methods:
        getattr(bot, method).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked_chat_id",
    [
        BLOCKED_CUSTOMER_ID,
        f"+{BLOCKED_CUSTOMER_ID}",
        f"0{BLOCKED_CUSTOMER_ID}",
    ],
)
async def test_external_registry_blocks_normalized_ids_before_wire_call(
    tmp_path, monkeypatch, blocked_chat_id
) -> None:
    from gateway.telegram_egress_policy import (
        TelegramEgressDenied,
        denied_recipients,
        guard_telegram_request,
    )

    registry = tmp_path / "telegram-egress-deny.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "blocked_user_ids": [BLOCKED_CUSTOMER_ID],
                "blocked_usernames": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_TELEGRAM_EGRESS_DENY_FILE", str(registry))
    monkeypatch.setenv("HERMES_TELEGRAM_EGRESS_DENY_REQUIRED", "1")
    monkeypatch.delenv("HERMES_TELEGRAM_EGRESS_DENY_IDS", raising=False)
    denied_recipients.cache_clear()
    class _InnerRequest:
        read_timeout = 5

        def __init__(self) -> None:
            self.calls = 0

        async def initialize(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

        async def do_request(self, **kwargs):
            self.calls += 1
            return 200, b"{}"

        async def post(self, **kwargs):
            self.calls += 1
            return {}

        async def retrieve(self, **kwargs):
            return b""

    inner = _InnerRequest()
    guarded = guard_telegram_request(inner)
    request_data = SimpleNamespace(
        parameters=[SimpleNamespace(name="chat_id", value=blocked_chat_id)]
    )
    with pytest.raises(TelegramEgressDenied, match="telegram_recipient_denied"):
        await guarded.post(
            "https://api.telegram.org/test", request_data=request_data
        )

    assert inner.calls == 0
    denied_recipients.cache_clear()


@pytest.mark.asyncio
async def test_loose_business_flags_cannot_forge_a_route() -> None:
    adapter = _adapter()
    adapter._rich_send_disabled = True
    adapter._bot = MagicMock(id=BOT_ID, username="testhermesbot")
    adapter._bot.send_message = AsyncMock(
        return_value=SimpleNamespace(message_id=101)
    )

    result = await adapter.send(
        SAFE_CUSTOMER_ID,
        "must not escape",
        metadata={
            "business_connection_id": BUSINESS_CONNECTION_ID,
            "external_safe_mode": True,
            "telegram_business_external_contact": True,
        },
    )

    assert result.success is False
    assert result.error == "unsafe_telegram_business_route"
    adapter._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_business_bot_echo_is_dropped_before_dispatch() -> None:
    adapter = _adapter()
    adapter.handle_message = AsyncMock()
    adapter._enqueue_text_event = MagicMock()
    adapter._cache_replied_media = AsyncMock()
    message = _business_message(
        text="own outbound echoed by Telegram",
        sender_business_bot=SimpleNamespace(id=BOT_ID),
    )

    await adapter._handle_text_message(_business_update(message), SimpleNamespace())

    adapter.handle_message.assert_not_awaited()
    adapter._enqueue_text_event.assert_not_called()


@pytest.mark.asyncio
async def test_business_send_requires_and_propagates_exact_connection() -> None:
    adapter = _adapter()
    adapter._rich_send_disabled = True
    adapter._bot = MagicMock(id=BOT_ID, username="testhermesbot")
    adapter._bot.send_message = AsyncMock(
        return_value=SimpleNamespace(message_id=100)
    )

    result = await adapter.send(
        SAFE_CUSTOMER_ID,
        "safe scoped answer",
        metadata={
            "business_connection_id": BUSINESS_CONNECTION_ID,
            "external_safe_mode": True,
            "telegram_business_external_contact": True,
            "route_envelope": {
                "version": 1,
                "platform": "telegram",
                "runtime_profile": "default",
                "transport_profile": "default",
                "chat_id": SAFE_CUSTOMER_ID,
                "thread_id": None,
                "user_id": OWNER_ID,
                "business_connection_id": BUSINESS_CONNECTION_ID,
                "external_safe_mode": True,
            },
        },
    )

    assert result.success is True
    assert adapter._bot.send_message.await_args.kwargs["business_connection_id"] == (
        BUSINESS_CONNECTION_ID
    )


def _business_route_metadata() -> dict:
    return {
        "business_connection_id": BUSINESS_CONNECTION_ID,
        "external_safe_mode": True,
        "telegram_business_external_contact": True,
        "route_envelope": {
            "version": 1,
            "platform": "telegram",
            "runtime_profile": "default",
            "transport_profile": "default",
            "chat_id": SAFE_CUSTOMER_ID,
            "thread_id": None,
            "user_id": OWNER_ID,
            "business_connection_id": BUSINESS_CONNECTION_ID,
            "external_safe_mode": True,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "wire_method"),
    [
        ("edit_message", "edit_message_text"),
        ("delete_message", "delete_business_messages"),
        ("send_draft", "send_message_draft"),
        ("send_update_prompt", "send_message"),
        ("send_exec_approval", "send_message"),
        ("send_slash_confirm", "send_message"),
        ("send_clarify", "send_message"),
        ("send_model_picker", "send_message"),
        ("send_choice_picker", "send_message"),
        ("send_voice", "send_voice"),
        ("send_audio", "send_audio"),
        ("send_multiple_images", "send_media_group"),
        ("send_image_file", "send_photo"),
        ("send_document", "send_document"),
        ("send_video", "send_video"),
        ("send_image", "send_photo"),
        ("send_animation", "send_animation"),
        ("send_typing", "send_chat_action"),
    ],
)
async def test_business_egress_surfaces_propagate_exact_connection_id(
    surface, wire_method, tmp_path, monkeypatch
) -> None:
    adapter = _adapter()
    bot = MagicMock(id=BOT_ID, username="testhermesbot")
    for method in {
        "send_message",
        "edit_message_text",
        "delete_business_messages",
        "send_message_draft",
        "send_voice",
        "send_audio",
        "send_media_group",
        "send_photo",
        "send_document",
        "send_video",
        "send_animation",
        "send_chat_action",
        "do_api_request",
    }:
        setattr(bot, method, AsyncMock(return_value=SimpleNamespace(message_id=202)))
    adapter._bot = bot
    adapter._rich_send_disabled = True
    metadata = _business_route_metadata()

    voice = tmp_path / "voice.ogg"
    audio = tmp_path / "audio.mp3"
    media = tmp_path / "media.bin"
    for path in (voice, audio, media):
        path.write_bytes(b"test")
    monkeypatch.setattr(
        "plugins.platforms.telegram.adapter._probe_voice_duration_seconds",
        lambda _path: 1,
    )

    calls = {
        "edit_message": lambda: adapter.edit_message(
            SAFE_CUSTOMER_ID, "1", "edit", metadata=metadata
        ),
        "delete_message": lambda: adapter.delete_message(
            SAFE_CUSTOMER_ID, "1", metadata=metadata
        ),
        "send_draft": lambda: adapter.send_draft(
            SAFE_CUSTOMER_ID, 1, "draft", metadata=metadata
        ),
        "send_update_prompt": lambda: adapter.send_update_prompt(
            SAFE_CUSTOMER_ID, "update?", metadata=metadata
        ),
        "send_exec_approval": lambda: adapter.send_exec_approval(
            SAFE_CUSTOMER_ID, "echo hi", "session", metadata=metadata
        ),
        "send_slash_confirm": lambda: adapter.send_slash_confirm(
            SAFE_CUSTOMER_ID, "title", "message", "session", "confirm", metadata=metadata
        ),
        "send_clarify": lambda: adapter.send_clarify(
            SAFE_CUSTOMER_ID, "question", ["yes"], "clarify", "session", metadata=metadata
        ),
        "send_model_picker": lambda: adapter.send_model_picker(
            SAFE_CUSTOMER_ID, [], "model", "provider", "session", lambda *_: None, metadata=metadata
        ),
        "send_choice_picker": lambda: adapter.send_choice_picker(
            SAFE_CUSTOMER_ID,
            "title",
            [{"value": "a", "label": "A"}],
            "session",
            lambda *_: None,
            metadata=metadata,
        ),
        "send_voice": lambda: adapter.send_voice(
            SAFE_CUSTOMER_ID, str(voice), metadata=metadata
        ),
        "send_audio": lambda: adapter.send_voice(
            SAFE_CUSTOMER_ID, str(audio), metadata=metadata
        ),
        "send_multiple_images": lambda: adapter.send_multiple_images(
            SAFE_CUSTOMER_ID, [("https://example.com/a.png", "a")], metadata=metadata
        ),
        "send_image_file": lambda: adapter.send_image_file(
            SAFE_CUSTOMER_ID, str(media), metadata=metadata
        ),
        "send_document": lambda: adapter.send_document(
            SAFE_CUSTOMER_ID, str(media), metadata=metadata
        ),
        "send_video": lambda: adapter.send_video(
            SAFE_CUSTOMER_ID, str(media), metadata=metadata
        ),
        "send_image": lambda: adapter.send_image(
            SAFE_CUSTOMER_ID, "https://example.com/a.png", metadata=metadata
        ),
        "send_animation": lambda: adapter.send_animation(
            SAFE_CUSTOMER_ID, "https://example.com/a.gif", metadata=metadata
        ),
        "send_typing": lambda: adapter.send_typing(SAFE_CUSTOMER_ID, metadata=metadata),
    }

    await calls[surface]()

    wire = getattr(bot, wire_method)
    wire.assert_awaited()
    assert wire.await_args.kwargs["business_connection_id"] == BUSINESS_CONNECTION_ID


def test_delivery_ledger_quarantines_legacy_telegram_rows_without_route_envelope(
    tmp_path, monkeypatch
) -> None:
    from gateway import delivery_ledger

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(delivery_ledger, "_db_path", lambda: db_path)
    monkeypatch.setattr(delivery_ledger, "_owner_alive", lambda *_: False)
    conn = delivery_ledger._connect()
    with conn:
        conn.execute(
            """INSERT INTO delivery_obligations
               (obligation_id, session_key, platform, chat_id, thread_id,
                content, state, attempts, created_at, updated_at,
                owner_pid, owner_started_at)
               VALUES (?, ?, 'telegram', ?, NULL, ?, 'pending', 0, 1, 1, 999, 1)""",
            (
                "legacy-ambiguous",
                f"agent:main:telegram:dm:{BLOCKED_CUSTOMER_ID}",
                BLOCKED_CUSTOMER_ID,
                "must never be replayed to the customer",
            ),
        )
    conn.close()

    claimed = delivery_ledger.sweep_recoverable(
        now=2,
        deliverable_platforms={"telegram"},
    )

    assert claimed == []
    check = sqlite3.connect(db_path)
    try:
        state, error = check.execute(
            "SELECT state, last_error FROM delivery_obligations "
            "WHERE obligation_id='legacy-ambiguous'"
        ).fetchone()
    finally:
        check.close()
    assert state == "abandoned"
    assert error == "ambiguous_route_envelope"


def test_delivery_ledger_round_trips_immutable_business_route(tmp_path, monkeypatch) -> None:
    from gateway import delivery_ledger

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(delivery_ledger, "_db_path", lambda: db_path)
    monkeypatch.setattr(delivery_ledger, "_owner_alive", lambda *_: False)
    route_envelope = {
        "version": 1,
        "platform": "telegram",
        "runtime_profile": "default",
        "transport_profile": "default",
        "chat_id": SAFE_CUSTOMER_ID,
        "thread_id": None,
        "user_id": OWNER_ID,
        "business_connection_id": BUSINESS_CONNECTION_ID,
        "external_safe_mode": True,
    }

    delivery_ledger.record_obligation(
        obligation_id="business-safe",
        session_key="agent:main:telegram:business:scoped",
        platform="telegram",
        chat_id=SAFE_CUSTOMER_ID,
        thread_id=None,
        content="safe scoped answer",
        route_envelope=route_envelope,
    )
    delivery_ledger.mark_failed("business-safe", "restart")

    claimed = delivery_ledger.sweep_recoverable(
        now=10,
        deliverable_platforms={"telegram"},
    )

    assert len(claimed) == 1
    assert claimed[0]["route_envelope"] == route_envelope
