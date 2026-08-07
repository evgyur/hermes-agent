"""TelegramAdapter send-path health gating after reconnect storms.

After sustained Bad Gateway / TimedOut reconnect cycles, the PTB httpx client
can enter a wedged state where ``bot.send_message()`` returns a valid Message
but nothing reaches the recipient.  ``_send_path_degraded`` short-circuits
``send()`` so cron's live-adapter branch falls through to standalone HTTP.
"""
import json
import multiprocessing
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from plugins.platforms.telegram.adapter import TelegramAdapter  # noqa: E402


def _make_adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._bot = MagicMock()
    adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    return adapter


def _write_business_routes_process(
    store_path: str,
    worker: int,
    writes_per_worker: int,
    start_event,
) -> None:
    adapter = _make_adapter()
    adapter._business_connection_store_path = lambda: Path(store_path)
    start_event.wait(timeout=10)
    for offset in range(writes_per_worker):
        chat_id = str(800000000 + worker * writes_per_worker + offset)
        adapter._remember_business_connection_id(chat_id, f"proc-{worker}-{offset}")


@pytest.mark.asyncio
async def test_send_succeeds_when_path_healthy():
    """Healthy adapter delivers normally; send_message is called."""
    adapter = _make_adapter()
    assert adapter._send_path_degraded is False

    result = await adapter.send("123", "hello")

    assert result.success is True
    adapter._bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_legacy_send_records_message_for_durable_business_reply_trigger(monkeypatch):
    """Every successful text send, not only rich sends, must be reply-addressable."""
    adapter = _make_adapter()
    recorded = []
    monkeypatch.setattr(
        "gateway.rich_sent_store.record",
        lambda chat_id, message_id, text: recorded.append(
            (str(chat_id), str(message_id), text)
        ),
    )

    result = await adapter.send("700000002", "Готово. Проверил чат.")

    assert result.success is True
    assert recorded == [("700000002", "42", "Готово. Проверил чат.")]


@pytest.mark.asyncio
async def test_send_short_circuits_when_path_degraded():
    """Degraded adapter returns failure WITHOUT calling send_message,
    so cron's live-adapter branch falls through to standalone HTTP."""
    adapter = _make_adapter()
    adapter._send_path_degraded = True

    result = await adapter.send("123", "hello")

    assert result.success is False
    assert result.error == "send_path_degraded"
    assert result.retryable is True
    adapter._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_safe_business_send_uses_bot_identity_not_business_account():
    """External Telegram Business replies must not be sent as the human account."""
    adapter = _make_adapter()
    adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    adapter.config.extra["business"] = {
        "enabled": True,
        "send_as_account": True,
    }

    result = await adapter.send(
        "8533179145",
        "ОК",
        metadata={
            "business_connection_id": "biz-123",
            "external_safe_mode": True,
            "telegram_business_external_contact": True,
        },
    )

    assert result.success is True
    kwargs = adapter._bot.send_message.await_args.kwargs
    assert "business_connection_id" not in kwargs


@pytest.mark.asyncio
async def test_business_send_uses_bot_identity_by_default():
    """Business metadata must not silently make agent output appear human-authored."""
    adapter = _make_adapter()
    adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))

    result = await adapter.send(
        "8533179145",
        "ОК",
        metadata={"business_connection_id": "biz-123"},
    )

    assert result.success is True
    kwargs = adapter._bot.send_message.await_args.kwargs
    assert "business_connection_id" not in kwargs


@pytest.mark.asyncio
async def test_business_send_as_account_requires_explicit_opt_in():
    """Future trusted paths may opt in explicitly; the default remains fail-closed."""
    adapter = _make_adapter()
    adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))

    result = await adapter.send(
        "8533179145",
        "ОК",
        metadata={
            "business_connection_id": "biz-123",
            "telegram_business_send_as_account": True,
        },
    )

    assert result.success is True
    kwargs = adapter._bot.send_message.await_args.kwargs
    assert kwargs["business_connection_id"] == "biz-123"


@pytest.mark.asyncio
async def test_business_send_as_account_honors_trusted_adapter_config():
    """The configured concierge route must not require an extra per-turn flag."""
    adapter = _make_adapter()
    adapter.config.extra["business"] = {
        "enabled": True,
        "send_as_account": True,
    }

    result = await adapter.send(
        "700000002",
        "ОК",
        metadata={"business_connection_id": "biz-123"},
    )

    assert result.success is True
    kwargs = adapter._bot.send_message.await_args.kwargs
    assert kwargs["business_connection_id"] == "biz-123"


@pytest.mark.asyncio
async def test_business_rich_send_honors_trusted_adapter_config():
    adapter = _make_adapter()
    adapter.config.extra["business"] = {
        "enabled": True,
        "send_as_account": True,
    }
    adapter._bot.do_api_request = AsyncMock(return_value={"message_id": 43})

    result = await adapter._try_send_rich(
        "700000002",
        "## Анализ",
        None,
        {"business_connection_id": "biz-123"},
    )

    assert result is not None and result.success is True
    payload = adapter._bot.do_api_request.await_args.kwargs["api_kwargs"]
    assert payload["business_connection_id"] == "biz-123"


@pytest.mark.asyncio
async def test_business_document_send_honors_trusted_adapter_config(tmp_path):
    adapter = _make_adapter()
    adapter.config.extra["business"] = {
        "enabled": True,
        "send_as_account": True,
    }
    adapter._bot.send_document = AsyncMock(return_value=MagicMock(message_id=44))
    artifact = tmp_path / "skill.zip"
    artifact.write_bytes(b"zip")

    result = await adapter.send_document(
        "700000002",
        str(artifact),
        metadata={"business_connection_id": "biz-123"},
    )

    assert result.success is True
    kwargs = adapter._bot.send_document.await_args.kwargs
    assert kwargs["business_connection_id"] == "biz-123"


@pytest.mark.asyncio
async def test_business_edits_preserve_business_connection_id():
    adapter = _make_adapter()
    adapter.config.extra["business"] = {"enabled": True, "send_as_account": True}
    adapter._bot.do_api_request = AsyncMock(return_value=True)
    adapter._bot.edit_message_text = AsyncMock(return_value=MagicMock(message_id=42))
    metadata = {"business_connection_id": "biz-123"}

    rich = await adapter._try_edit_rich("700000002", "42", "## Итог", metadata)
    assert rich is not None and rich.success is True
    assert adapter._bot.do_api_request.await_args.kwargs["api_kwargs"]["business_connection_id"] == "biz-123"

    preview = await adapter.edit_message("700000002", "42", "partial", metadata=metadata)
    assert preview.success is True
    assert adapter._bot.edit_message_text.await_args.kwargs["business_connection_id"] == "biz-123"


def test_first_seen_plain_owner_business_echo_is_rejected_and_route_is_retained(
    tmp_path, monkeypatch
):
    adapter = _make_adapter()
    store = tmp_path / "telegram_business_connections.json"
    monkeypatch.setattr(
        adapter,
        "_business_connection_store_path",
        lambda: store,
        raising=False,
    )
    adapter.config.extra["allow_from"] = ["700000001"]
    adapter.config.extra["business"] = {
        "enabled": True,
        "send_as_account": True,
        "trigger_words": ["Sigurd", "Сигурд"],
    }
    message = SimpleNamespace(
        business_connection_id=None,
        reply_to_message=SimpleNamespace(business_connection_id="biz-new"),
        chat=SimpleNamespace(id=700000002, type="private"),
        from_user=SimpleNamespace(id=700000001, is_bot=False),
        text="Просто исходящее сообщение",
        caption=None,
    )

    assert adapter._should_process_message(message) is False
    assert adapter._known_business_connection_id("700000002") == "biz-new"

    wake = SimpleNamespace(
        business_connection_id=None,
        reply_to_message=None,
        chat=message.chat,
        from_user=message.from_user,
        text="Sigurd, продолжи",
        caption=None,
    )
    assert adapter._should_process_message(wake) is True

    first_seen_wake = SimpleNamespace(
        business_connection_id=None,
        reply_to_message=SimpleNamespace(business_connection_id="biz-reply-wake"),
        chat=SimpleNamespace(id=700000003, type="private"),
        from_user=message.from_user,
        text="Sigurd, собери итог",
        caption=None,
    )
    assert adapter._should_process_message(first_seen_wake) is True
    assert adapter._known_business_connection_id("700000003") == "biz-reply-wake"
    assert (
        adapter._resolve_business_connection_id(first_seen_wake, chat_type="dm")
        == "biz-reply-wake"
    )


def test_business_owner_ids_can_be_narrower_than_telegram_allowlist():
    adapter = _make_adapter()
    adapter.config.extra["allow_from"] = ["700000001", "700000009"]
    adapter.config.extra["business"] = {
        "enabled": True,
        "owner_user_ids": ["700000001"],
        "trigger_words": ["Sigurd", "Сигурд"],
    }

    def message(sender_id: int):
        return SimpleNamespace(
            business_connection_id="biz-new",
            reply_to_message=None,
            chat=SimpleNamespace(id=700000002, type="private"),
            from_user=SimpleNamespace(id=sender_id, is_bot=False),
            text="Sigurd, продолжи",
            caption=None,
        )

    assert adapter._business_owner_ids() == {"700000001"}
    assert adapter._is_business_owner_wake_trigger(message(700000001)) is True
    assert adapter._is_business_owner_wake_trigger(message(700000009)) is False
    assert adapter._should_process_message(message(700000009)) is False


def test_business_blocked_chat_rejects_external_updates_and_owner_wakes():
    adapter = _make_adapter()
    adapter.config.extra["allow_from"] = ["700000001"]
    adapter.config.extra["business"] = {
        "enabled": True,
        "owner_user_ids": ["700000001"],
        "blocked_chats": ["700000002"],
        "trigger_words": ["Sigurd", "Сигурд"],
    }

    def message(sender_id: int, text: str):
        return SimpleNamespace(
            business_connection_id="biz-new",
            reply_to_message=None,
            chat=SimpleNamespace(id=700000002, type="private"),
            from_user=SimpleNamespace(id=sender_id, is_bot=False),
            text=text,
            caption=None,
        )

    assert adapter._should_process_message(message(700000002, "location update")) is False
    assert adapter._should_process_message(message(700000001, "Sigurd, продолжи")) is False


def test_business_wake_word_must_be_an_explicit_command_prefix():
    adapter = _make_adapter()
    adapter.config.extra["allow_from"] = ["700000001"]
    adapter.config.extra["business"] = {
        "enabled": True,
        "send_as_account": True,
        "trigger_words": ["Sigurd", "Сигурд"],
    }
    message = SimpleNamespace(
        business_connection_id="biz-new",
        reply_to_message=None,
        chat=SimpleNamespace(id=700000002, type="private"),
        from_user=SimpleNamespace(id=700000001, is_bot=False),
        text="Я уже обсуждал это с Сигурдом вчера",
        caption=None,
    )

    assert adapter._is_business_owner_wake_trigger(message) is False


def test_business_owner_wake_recovers_the_verified_connection_for_peer_chat(monkeypatch):
    adapter = _make_adapter()
    adapter.config.extra["allow_from"] = ["700000001"]
    adapter.config.extra["business"] = {
        "enabled": True,
        "send_as_account": True,
        "trigger_words": ["Sigurd", "Сигурд"],
    }
    monkeypatch.setattr(
        adapter,
        "_known_business_connection_id",
        lambda chat_id: "biz-123" if str(chat_id) == "700000002" else None,
        raising=False,
    )
    wake = SimpleNamespace(
        business_connection_id=None,
        chat=SimpleNamespace(
            id=700000002,
            type="private",
            title=None,
            full_name="Peer",
        ),
        from_user=SimpleNamespace(
            id=700000001,
            is_bot=False,
            full_name="Owner",
        ),
        text="Sigurd, пришли файл",
        caption=None,
        message_id=123,
        message_thread_id=None,
        reply_to_message=None,
        date=None,
    )
    plain = SimpleNamespace(**{**vars(wake), "text": "Просто сообщение"})

    assert adapter._resolve_business_connection_id(wake, chat_type="dm") == "biz-123"
    assert adapter._resolve_business_connection_id(plain, chat_type="dm") is None
    assert adapter._should_process_message(wake) is True
    assert adapter._should_process_message(plain) is False

    from gateway.platforms.base import MessageType

    event = adapter._build_message_event(wake, MessageType.TEXT)
    assert event.source.business_connection_id == "biz-123"
    assert event.source.external_safe_mode is False


def test_business_owner_reply_to_durable_concierge_message_dispatches_and_recovers_route(
    monkeypatch,
):
    adapter = _make_adapter()
    adapter.config.extra["allow_from"] = ["700000001"]
    adapter.config.extra["business"] = {
        "enabled": True,
        "send_as_account": True,
        "allow_reply_trigger": True,
        "trigger_words": ["Sigurd", "Сигурд"],
    }
    monkeypatch.setattr(
        "gateway.rich_sent_store.lookup",
        lambda chat_id, message_id: (
            "Готово. Проверил чат."
            if (str(chat_id), str(message_id)) == ("700000002", "90001")
            else None
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_known_business_connection_id",
        lambda chat_id: "biz-123" if str(chat_id) == "700000002" else None,
        raising=False,
    )
    message = SimpleNamespace(
        business_connection_id=None,
        chat=SimpleNamespace(
            id=700000002,
            type="private",
            title=None,
            full_name="Peer",
        ),
        from_user=SimpleNamespace(
            id=700000001,
            is_bot=False,
            full_name="Owner",
        ),
        text="Напиши пост аккуратно",
        caption=None,
        message_id=90002,
        message_thread_id=None,
        reply_to_message=SimpleNamespace(
            message_id=90001,
            business_connection_id=None,
            text="Готово. Проверил чат.",
            caption=None,
            from_user=SimpleNamespace(id=700000001, is_bot=False),
        ),
        date=None,
    )

    assert adapter._should_process_message(message) is True
    assert adapter._resolve_business_connection_id(message, chat_type="dm") == "biz-123"

    from gateway.platforms.base import MessageType

    event = adapter._build_message_event(message, MessageType.TEXT)
    assert event.source.business_connection_id == "biz-123"
    assert event.source.external_safe_mode is False


def test_business_owner_reply_trigger_requires_durable_sent_message_hit(monkeypatch):
    adapter = _make_adapter()
    adapter.config.extra["allow_from"] = ["700000001"]
    adapter.config.extra["business"] = {
        "enabled": True,
        "allow_reply_trigger": True,
        "trigger_words": ["Sigurd", "Сигурд"],
    }
    monkeypatch.setattr("gateway.rich_sent_store.lookup", lambda *_args: None)
    message = SimpleNamespace(
        business_connection_id=None,
        chat=SimpleNamespace(id=700000002, type="private"),
        from_user=SimpleNamespace(id=700000001, is_bot=False),
        text="обычный ответ человеку",
        caption=None,
        reply_to_message=SimpleNamespace(
            message_id=90001,
            business_connection_id=None,
            text="сообщение собеседника",
            caption=None,
            from_user=SimpleNamespace(id=700000002, is_bot=False),
        ),
    )

    assert adapter._should_process_message(message) is False


def test_single_verified_business_connection_recovers_a_new_peer_chat(tmp_path, monkeypatch):
    adapter = _make_adapter()
    store = tmp_path / "telegram_business_connections.json"
    monkeypatch.setattr(
        adapter,
        "_business_connection_store_path",
        lambda: store,
        raising=False,
    )

    adapter._remember_business_connection_id("700000002", "biz-123")

    assert adapter._known_business_connection_id("700000003") == "biz-123"


def test_business_connection_store_preserves_concurrent_updates(tmp_path, monkeypatch):
    adapter = _make_adapter()
    store = tmp_path / "telegram_business_connections.json"
    monkeypatch.setattr(
        adapter,
        "_business_connection_store_path",
        lambda: store,
        raising=False,
    )
    workers = 8
    writes_per_worker = 5
    barrier = threading.Barrier(workers)

    def write_routes(worker: int) -> None:
        barrier.wait()
        for offset in range(writes_per_worker):
            chat_id = str(700000000 + worker * writes_per_worker + offset)
            adapter._remember_business_connection_id(chat_id, f"biz-{worker}-{offset}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(write_routes, range(workers)))

    payload = json.loads(store.read_text(encoding="utf-8"))
    assert len(payload) == workers * writes_per_worker


def test_business_connection_store_preserves_cross_process_updates(tmp_path):
    store = tmp_path / "telegram_business_connections.json"
    workers = 6
    writes_per_worker = 4
    context = multiprocessing.get_context("spawn" if sys.platform == "win32" else "fork")
    start_event = context.Event()
    processes = [
        context.Process(
            target=_write_business_routes_process,
            args=(str(store), worker, writes_per_worker, start_event),
        )
        for worker in range(workers)
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    payload = json.loads(store.read_text(encoding="utf-8"))
    assert len(payload) == workers * writes_per_worker


@pytest.mark.asyncio
async def test_send_retries_without_business_connection_id_on_business_peer_invalid():
    """Telegram can mark an inbound DM with a stale Business connection id.

    If Bot API rejects the reply with Business_peer_invalid, retry the same
    message as a normal bot DM instead of dropping the response.
    """
    adapter = _make_adapter()
    adapter._bot.send_message = AsyncMock(
        side_effect=[Exception("Business_peer_invalid"), MagicMock(message_id=99)]
    )

    result = await adapter.send(
        "6442556885",
        "ОК",
        metadata={
            "business_connection_id": "biz-123",
            "telegram_business_send_as_account": True,
        },
    )

    assert result.success is True
    assert result.message_id == "99"
    assert adapter._bot.send_message.await_count == 2
    first_kwargs = adapter._bot.send_message.await_args_list[0].kwargs
    second_kwargs = adapter._bot.send_message.await_args_list[1].kwargs
    assert first_kwargs["business_connection_id"] == "biz-123"
    assert "business_connection_id" not in second_kwargs


@pytest.mark.asyncio
async def test_reconnect_storm_keeps_degraded_after_failed_restart(monkeypatch):
    """A failed polling restart must keep outbound delivery fail-closed."""
    adapter = _make_adapter()
    adapter._app = MagicMock()
    adapter._app.updater = MagicMock()
    adapter._app.updater.running = True
    adapter._app.updater.stop = AsyncMock()
    # First start_polling attempt fails — the reconnect handler must leave the
    # flag set (path still unhealthy) and not clear it prematurely.
    adapter._app.updater.start_polling = AsyncMock(side_effect=OSError("still down"))
    adapter._app.bot = MagicMock()
    adapter._app.bot.get_me = AsyncMock(return_value=MagicMock())
    adapter._polling_error_callback_ref = AsyncMock()
    monkeypatch.setattr(adapter, "_drain_polling_connections", AsyncMock())
    monkeypatch.setattr(
        "plugins.platforms.telegram.adapter.Update", MagicMock(ALL_TYPES=[])
    )
    # Suppress the self-rescheduled retry so the test doesn't recurse.
    monkeypatch.setattr(
        "plugins.platforms.telegram.adapter.asyncio.ensure_future", MagicMock()
    )

    with patch("plugins.platforms.telegram.adapter.asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(OSError("Bad Gateway"))
    # start_polling failed → path still degraded.
    assert adapter._send_path_degraded is True

@pytest.mark.asyncio
async def test_successful_reconnect_waits_for_polling_progress(monkeypatch):
    """start_polling() alone must not clear degradation before getUpdates moves."""
    adapter = _make_adapter()
    adapter._app = MagicMock()
    adapter._app.updater = MagicMock()
    adapter._app.updater.running = True
    adapter._app.updater.stop = AsyncMock()
    adapter._app.updater.start_polling = AsyncMock()
    adapter._app.bot = MagicMock()
    adapter._app.bot.get_me = AsyncMock(return_value=MagicMock())
    adapter._polling_error_callback_ref = AsyncMock()
    monkeypatch.setattr(adapter, "_drain_polling_connections", AsyncMock())
    monkeypatch.setattr(
        "plugins.platforms.telegram.adapter.Update", MagicMock(ALL_TYPES=[])
    )
    # Don't let the deferred probe run — prove the clear happens in the
    # reconnect handler itself, not in _verify_polling_after_reconnect.
    monkeypatch.setattr(
        adapter, "_verify_polling_after_reconnect", AsyncMock()
    )

    with patch("plugins.platforms.telegram.adapter.asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(OSError("Bad Gateway"))

    assert adapter._send_path_degraded is True
    assert adapter._polling_network_error_count == 1
