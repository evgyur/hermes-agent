"""Security regressions for Telegram Business recipient isolation.

These fixtures model the 2026-08-24 incident exactly: the account owner sent
an audio message inside a customer conversation and Hermes treated it as a
bot command, then delivered the answer to the customer as a plain bot DM.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.session import SessionSource, build_session_key
from plugins.platforms.telegram.adapter import TelegramAdapter


OWNER_ID = "617744661"
VLAD_ID = "268754981"
SAFE_CUSTOMER_ID = "777000123"
BOT_ID = 8533179145
BUSINESS_CONNECTION_ID = "FzZ5OU7SQEidHQAAxmDPRBoxdSQ"


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
    adapter._bot = SimpleNamespace(id=BOT_ID, username="chipshermesbot")
    return adapter


def _business_message(
    *,
    chat_id: str = VLAD_ID,
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
            full_name="Vlad Telegramin",
        ),
        from_user=SimpleNamespace(
            id=int(from_user_id),
            is_bot=False,
            full_name="Evgeny Chip" if from_user_id == OWNER_ID else "Vlad Telegramin",
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
    message = _business_message(text="обычный личный разговор с Владом")

    await adapter._handle_text_message(_business_update(message), SimpleNamespace())

    adapter.handle_message.assert_not_awaited()
    adapter._enqueue_text_event.assert_not_called()
    adapter._cache_replied_media.assert_not_awaited()


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


def test_business_source_round_trip_and_connection_isolation() -> None:
    source_a = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=VLAD_ID,
        chat_type="dm",
        user_id=OWNER_ID,
        business_connection_id="connection-a",
        external_safe_mode=True,
    )
    source_b = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=VLAD_ID,
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
async def test_plain_bot_dm_to_non_allowlisted_customer_fails_closed() -> None:
    adapter = _adapter()
    adapter._rich_send_disabled = True
    adapter._bot = MagicMock(id=BOT_ID, username="chipshermesbot")
    adapter._bot.send_message = AsyncMock(
        return_value=SimpleNamespace(message_id=99)
    )

    result = await adapter.send(VLAD_ID, "private answer", metadata={})

    assert result.success is False
    assert result.error == "telegram_recipient_denied"
    adapter._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_chat_id", [VLAD_ID, f"+{VLAD_ID}", f"0{VLAD_ID}"])
async def test_builtin_vlad_deny_survives_missing_registry_and_blocks_wire_call(
    tmp_path, monkeypatch, blocked_chat_id
) -> None:
    from gateway.telegram_egress_policy import (
        TelegramEgressDenied,
        denied_recipients,
        guard_telegram_request,
    )

    monkeypatch.setenv(
        "HERMES_TELEGRAM_EGRESS_DENY_FILE", str(tmp_path / "missing.json")
    )
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
    adapter._bot = MagicMock(id=BOT_ID, username="chipshermesbot")
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
    adapter._bot = MagicMock(id=BOT_ID, username="chipshermesbot")
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
                f"agent:main:telegram:dm:{VLAD_ID}",
                VLAD_ID,
                "must never be replayed to Vlad",
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
