"""Tests for Telegram declarative auto-skill routes and preview guards."""

import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType
from gateway.platforms.telegram import (
    TelegramAdapter,
    _looks_like_inline_tg_preview,
    _tg_preview_echo_fingerprint,
)


def _make_adapter(extra):
    adapter = object.__new__(TelegramAdapter)
    adapter.config = PlatformConfig(enabled=True, token="test-token", extra=extra)
    adapter._auto_skill_routes = adapter._load_auto_skill_routes()
    return adapter


def test_auto_skill_routes_load_valid_routes():
    adapter = _make_adapter({
        "auto_skill_routes": [
            {
                "skill": "tg",
                "chats": [-1001234567890, "123456789"],
                "match": {"urls": True, "media": ["photo", "video"]},
            }
        ]
    })

    assert adapter._auto_skill_routes == [
        {
            "skill": "tg",
            "chats": {"-1001234567890", "123456789"},
            "match_urls": True,
            "match_media": {"photo", "video"},
        }
    ]


def test_text_url_gets_skill_prefix_for_matching_chat():
    adapter = _make_adapter({
        "auto_skill_routes": [
            {"skill": "tg", "chats": ["123"], "match": {"urls": True}}
        ]
    })

    assert adapter._auto_skill_prefix_for_text("123", "https://example.com/post") == "/tg "


def test_text_without_url_does_not_trigger():
    adapter = _make_adapter({
        "auto_skill_routes": [
            {"skill": "tg", "chats": ["123"], "match": {"urls": True}}
        ]
    })

    assert adapter._auto_skill_prefix_for_text("123", "обычный вопрос") is None


def test_finished_tg_preview_text_does_not_auto_route_even_with_urls():
    adapter = _make_adapter({
        "auto_skill_routes": [
            {"skill": "tg", "chats": ["123"], "match": {"urls": True}}
        ]
    })
    preview = (
        "USDH уступает место USDC\n"
        "⠀\n"
        "Готовый TG-пост с несколькими смысловыми блоками.\n"
        "⠀\n"
        "Источник: https://x.com/HyperliquidX/status/2054895699498619143"
    )

    assert adapter._auto_skill_prefix_for_text("123", preview) is None


def test_existing_slash_command_does_not_get_prefixed():
    adapter = _make_adapter({
        "auto_skill_routes": [
            {"skill": "tg", "chats": ["123"], "match": {"urls": True}}
        ]
    })

    assert adapter._auto_skill_prefix_for_text("123", "/status https://example.com") is None


def test_media_photo_and_video_get_skill_prefix():
    adapter = _make_adapter({
        "auto_skill_routes": [
            {"skill": "tg", "chats": ["123"], "match": {"media": ["photo", "video"]}}
        ]
    })

    assert adapter._auto_skill_prefix_for_media("123", MessageType.PHOTO) == "/tg "
    assert adapter._auto_skill_prefix_for_media("123", MessageType.VIDEO) == "/tg "


def test_unmatched_chat_does_not_trigger():
    adapter = _make_adapter({
        "auto_skill_routes": [
            {"skill": "tg", "chats": ["123"], "match": {"urls": True, "media": ["photo"]}}
        ]
    })

    assert adapter._auto_skill_prefix_for_text("999", "https://example.com") is None
    assert adapter._auto_skill_prefix_for_media("999", MessageType.PHOTO) is None



def test_inline_tg_preview_detector_matches_finished_html_post():
    text = (
        "<b>GPT-5.5 закрывает бенч</b>\n"
        "⠀\n"
        "Короткий пост с телеграм-разделителями.\n\n"
        "Ещё один абзац.\n"
        "⠀\n"
        "Источник: ProgramBench"
    )

    assert _looks_like_inline_tg_preview(text) is True


def test_inline_tg_preview_detector_ignores_operator_report():
    assert _looks_like_inline_tg_preview("готово.\n\n➊ проверка\n┈ tests: pass") is False


def test_inline_preview_guard_loads_from_config():
    adapter = _make_adapter({
        "inline_preview_guard": {"enabled": True, "chats": [-1009876543210]}
    })
    adapter._inline_preview_guard = adapter._load_inline_preview_guard()

    assert adapter._inline_preview_guard["enabled"] is True
    assert adapter._inline_preview_guard["chats"] >= {"-1009876543210"}


def test_external_preview_echo_is_ignored_by_exact_message_id(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    message_id_path = tmp_path / "message_id.txt"
    state_path.write_text(
        json.dumps({"ok": True, "verified": True, "chat_id": "-1009876543210", "message_ids": ["730"]}),
        encoding="utf-8",
    )
    message_id_path.write_text("730\n", encoding="utf-8")
    monkeypatch.setenv("TG_PREVIEW_STATE_FILE", str(state_path))
    monkeypatch.setenv("TG_PREVIEW_MESSAGE_ID_FILE", str(message_id_path))
    adapter = _make_adapter({"inline_preview_guard": {"enabled": True, "chats": ["-1009876543210"]}})
    adapter._inline_preview_guard = adapter._load_inline_preview_guard()
    message = SimpleNamespace(
        message_id=730,
        chat=SimpleNamespace(id=-1009876543210),
        from_user=SimpleNamespace(id=123456789, username="PreviewBridge"),
        text="USDH уступает место USDC\n⠀\nИсточник: Hyperliquid",
        caption=None,
    )

    assert adapter._is_external_tg_preview_echo(message) is True


def test_non_preview_message_id_is_not_ignored(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    message_id_path = tmp_path / "message_id.txt"
    state_path.write_text(json.dumps({"ok": True, "verified": True, "message_ids": ["730"]}), encoding="utf-8")
    message_id_path.write_text("730\n", encoding="utf-8")
    monkeypatch.setenv("TG_PREVIEW_STATE_FILE", str(state_path))
    monkeypatch.setenv("TG_PREVIEW_MESSAGE_ID_FILE", str(message_id_path))
    adapter = _make_adapter({"inline_preview_guard": {"enabled": True, "chats": ["-1009876543210"]}})
    adapter._inline_preview_guard = adapter._load_inline_preview_guard()
    message = SimpleNamespace(
        message_id=731,
        chat=SimpleNamespace(id=-1009876543210),
        from_user=SimpleNamespace(id=123456789, username="PreviewBridge"),
        text="/tg новый ручной запрос",
        caption=None,
    )

    assert adapter._is_external_tg_preview_echo(message) is False


def test_external_preview_echo_is_ignored_by_pending_fingerprint(tmp_path, monkeypatch):
    pending_path = tmp_path / "pending.jsonl"
    visible = (
        "Apple не стала витриной OpenAI\n"
        "⠀\n"
        "Готовый TG-пост с несколькими смысловыми блоками.\n"
        "⠀\n"
        "Источник: Bloomberg"
    )
    pending_path.write_text(
        json.dumps(
            {
                "chat_id": "-1009876543210",
                "sha256": _tg_preview_echo_fingerprint(visible),
                "created_at": time.time(),
                "expires_at": time.time() + 300,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TG_PREVIEW_PENDING_ECHO_FILE", str(pending_path))
    adapter = _make_adapter({"inline_preview_guard": {"enabled": True, "chats": ["-1009876543210"]}})
    adapter._inline_preview_guard = adapter._load_inline_preview_guard()
    message = SimpleNamespace(
        message_id=999,
        chat=SimpleNamespace(id=-1009876543210),
        from_user=SimpleNamespace(id=123456789, username="PreviewBridge"),
        text=None,
        caption=visible,
    )

    assert adapter._is_external_tg_preview_echo(message) is True


def test_manual_slash_tg_media_is_not_ignored_by_pending_fingerprint(tmp_path, monkeypatch):
    pending_path = tmp_path / "pending.jsonl"
    pending_path.write_text(
        json.dumps(
            {
                "chat_id": "-1009876543210",
                "sha256": hashlib.sha256(b"different").hexdigest(),
                "created_at": time.time(),
                "expires_at": time.time() + 300,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TG_PREVIEW_PENDING_ECHO_FILE", str(pending_path))
    adapter = _make_adapter({"inline_preview_guard": {"enabled": True, "chats": ["-1009876543210"]}})
    adapter._inline_preview_guard = adapter._load_inline_preview_guard()
    message = SimpleNamespace(
        message_id=1000,
        chat=SimpleNamespace(id=-1009876543210),
        from_user=SimpleNamespace(id=123456789, username="PreviewBridge"),
        text=None,
        caption="/tg Apple не стала витриной OpenAI\n⠀\nИсточник: Bloomberg",
    )

    assert adapter._is_external_tg_preview_echo(message) is False


@pytest.mark.asyncio
async def test_send_replaces_inline_tg_preview_with_blocker_for_guarded_chat():
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="fake-token",
            extra={"inline_preview_guard": {"enabled": True, "action": "blocker", "chats": ["123"]}},
        )
    )
    adapter._bot = MagicMock()

    async def _fake_send_message(**kwargs):
        return SimpleNamespace(message_id=42)

    adapter._bot.send_message = AsyncMock(side_effect=_fake_send_message)
    post = (
        "<b>Заголовок поста</b>\n"
        "⠀\n"
        "Готовый TG-текст, который нельзя отправлять от Hermes-бота.\n\n"
        "Второй смысловой блок.\n"
        "⠀\n"
        "Источник: example"
    )

    result = await adapter.send("123", post, metadata={"thread_id": "410"})

    assert result.success is True
    sent_text = adapter._bot.send_message.await_args.kwargs["text"]
    assert "превью заблокировано" in sent_text
    assert "Заголовок поста" not in sent_text


@pytest.mark.asyncio
async def test_send_allows_same_text_in_unguarded_chat():
    adapter = TelegramAdapter(
        PlatformConfig(
            enabled=True,
            token="fake-token",
            extra={"inline_preview_guard": {"enabled": True, "chats": ["123"]}},
        )
    )
    adapter._bot = MagicMock()

    async def _fake_send_message(**kwargs):
        return SimpleNamespace(message_id=43)

    adapter._bot.send_message = AsyncMock(side_effect=_fake_send_message)
    post = (
        "<b>Заголовок поста</b>\n"
        "⠀\n"
        "Готовый TG-текст.\n\n"
        "Второй блок.\n"
        "⠀\n"
        "Источник: example"
    )

    result = await adapter.send("999", post)

    assert result.success is True
    sent_text = adapter._bot.send_message.await_args.kwargs["text"]
    assert "превью заблокировано" not in sent_text
    assert "Заголовок поста" in sent_text
