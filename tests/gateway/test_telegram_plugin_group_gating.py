from types import SimpleNamespace

from gateway.config import Platform, PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter


TARGET_CHAT = -200
BOT_ID = 999


def _message(text="hello", *, thread_id=777, reply_to_bot=False, reply_to_other_bot=False):
    reply = None
    if reply_to_bot:
        reply = SimpleNamespace(from_user=SimpleNamespace(id=BOT_ID, is_bot=True))
    elif reply_to_other_bot:
        reply = SimpleNamespace(from_user=SimpleNamespace(id=555, is_bot=True))
    return SimpleNamespace(
        chat=SimpleNamespace(id=TARGET_CHAT, type="supergroup", is_forum=True),
        from_user=SimpleNamespace(id=111, is_bot=False, username="alice"),
        sender_chat=None,
        text=text,
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=thread_id,
        reply_to_message=reply,
    )


def _adapter():
    module = load_plugin_adapter("telegram")
    adapter = object.__new__(module.TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(
        enabled=True,
        token="***",
        extra={
            "require_mention": False,
            "require_mention_chats": [],
            "require_mention_topics": [f"{TARGET_CHAT}:777"],
            "free_response_chats": [str(TARGET_CHAT)],
            "allowed_chats": [],
            "allowed_topics": [],
            "ignored_threads": [],
            "guest_mode": False,
            "exclusive_bot_mentions": True,
            "ignore_other_bot_replies_chats": [str(TARGET_CHAT)],
            "mention_patterns": [r"@testbot\b"],
        },
    )
    adapter._bot = SimpleNamespace(id=BOT_ID, username="testbot")
    adapter._mention_patterns = adapter._compile_mention_patterns()
    return adapter


def test_plugin_topic_gate_overrides_free_response_chat():
    adapter = _adapter()

    assert adapter._should_process_message(_message("plain", thread_id=777)) is False
    assert adapter._should_process_message(_message("reply", thread_id=777, reply_to_bot=True)) is True
    assert adapter._should_process_message(_message("@testbot question", thread_id=777)) is True
    assert adapter._should_process_message(_message("plain", thread_id=778)) is True


def test_plugin_free_response_chat_ignores_other_bot_replies():
    adapter = _adapter()

    assert adapter._should_process_message(
        _message("reply to other", thread_id=778, reply_to_other_bot=True)
    ) is False
    assert adapter._should_process_message(
        _message("reply to self", thread_id=778, reply_to_bot=True)
    ) is True
    assert adapter._should_process_message(
        _message("@testbot join", thread_id=778, reply_to_other_bot=True)
    ) is True
