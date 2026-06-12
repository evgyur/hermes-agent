import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.telegram import TelegramAdapter
from gateway.platforms.base import SendResult


def _adapter(extra=None):
    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    config = PlatformConfig(enabled=True, token="123:ABC", extra=extra or {})
    adapter.config = config
    adapter._reply_to_mode = "first"
    adapter._disable_link_previews = False
    adapter._rich_message_chat_ids = adapter._coerce_str_set_extra("rich_message_chats")
    adapter._rich_message_min_chars = adapter._coerce_int_extra("rich_message_min_chars", 500)
    adapter._bot = object()
    adapter._send_path_degraded = False
    async def _no_guard(*a, **k):
        return None

    async def _no_typing(*a, **k):
        return None

    adapter._inline_preview_guard_send_result = _no_guard
    adapter._inline_preview_guard_replacement = lambda *a, **k: None
    adapter._remember_recent_outbound_text = lambda *a, **k: None
    adapter.send_typing = _no_typing
    return adapter


def test_rich_chat_ids_parse_json_string_from_config_set():
    adapter = _adapter({"rich_message_chats": '["-1003747790806"]'})

    assert adapter._rich_message_chat_ids == {"-1003747790806"}


def test_chipline_sections_convert_to_rich_markdown_headings():
    adapter = _adapter({"rich_message_chats": ["-1003747790806"]})

    assert adapter._rich_markdown_from_content("Вердикт.\n\n➊ Лучший отель\n┈ цена") == "Вердикт.\n\n## Лучший отель\n┈ цена"


@pytest.mark.asyncio
async def test_send_routes_configured_travel_report_to_send_rich_message(monkeypatch):
    adapter = _adapter({"rich_message_chats": ["-1003747790806"]})
    calls = []

    async def fake_send_rich(chat_id, content, *, reply_to_id=None, thread_kwargs=None, metadata=None):
        calls.append({
            "chat_id": chat_id,
            "content": content,
            "reply_to_id": reply_to_id,
            "thread_kwargs": thread_kwargs,
        })
        return SendResult(success=True, message_id="777", raw_response={"rich_message": True})

    monkeypatch.setattr(adapter, "_send_rich_message", fake_send_rich)

    result = await adapter.send(
        "-1003747790806",
        "Берём первый вариант.\n\n➊ Отель у пляжа\n┈ $120, фото: ![room](https://example.com/room.jpg)",
        reply_to="42",
    )

    assert result.success is True
    assert result.message_id == "777"
    assert calls and calls[0]["reply_to_id"] == 42


@pytest.mark.asyncio
async def test_finalize_edit_routes_configured_travel_report_to_rich_edit(monkeypatch):
    adapter = _adapter({"rich_message_chats": ["-1003747790806"]})
    calls = []

    async def fake_edit_rich(chat_id, message_id, content, *, metadata=None):
        calls.append((chat_id, message_id, content))
        return SendResult(success=True, message_id=message_id, raw_response={"rich_message": True})

    monkeypatch.setattr(adapter, "_edit_rich_message", fake_edit_rich)

    result = await adapter.edit_message(
        "-1003747790806",
        "777",
        "Подборка.\n\n➊ Отель\n┈ $120",
        finalize=True,
    )

    assert result.success is True
    assert calls == [("-1003747790806", "777", "Подборка.\n\n➊ Отель\n┈ $120")]


@pytest.mark.asyncio
async def test_rich_edit_failure_sends_rich_replacement_instead_of_plain_text(monkeypatch):
    adapter = _adapter({"rich_message_chats": ["-1003747790806"]})
    calls = []
    deleted = []

    async def fake_edit_rich(chat_id, message_id, content, *, metadata=None):
        raise RuntimeError("Bad Request")

    async def fake_send_rich(chat_id, content, *, reply_to_id=None, thread_kwargs=None, metadata=None):
        calls.append({
            "chat_id": chat_id,
            "content": content,
            "reply_to_id": reply_to_id,
            "thread_kwargs": thread_kwargs,
        })
        return SendResult(success=True, message_id="888", raw_response={"rich_message": True})

    async def fake_delete(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    async def fail_plain_edit(*args, **kwargs):
        raise AssertionError("plain text edit fallback must not run when rich replacement succeeds")

    monkeypatch.setattr(adapter, "_edit_rich_message", fake_edit_rich)
    monkeypatch.setattr(adapter, "_send_rich_message", fake_send_rich)
    monkeypatch.setattr(adapter, "delete_message", fake_delete)
    adapter._bot = type("Bot", (), {"edit_message_text": fail_plain_edit})()

    result = await adapter.edit_message(
        "-1003747790806",
        "777",
        "Подборка.\n\n➊ Отель\n┈ $120",
        finalize=True,
    )

    assert result.success is True
    assert result.message_id == "888"
    assert calls and calls[0]["chat_id"] == "-1003747790806"
    assert deleted == [("-1003747790806", "777")]
