"""Tests for Telegram declarative auto-skill routes."""

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType
from gateway.platforms.telegram import TelegramAdapter


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
                "chats": [-1003437858232, "617744661"],
                "match": {"urls": True, "media": ["photo", "video"]},
            }
        ]
    })

    assert adapter._auto_skill_routes == [
        {
            "skill": "tg",
            "chats": {"-1003437858232", "617744661"},
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
