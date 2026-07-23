from types import SimpleNamespace

from gateway.config import Platform, PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter


TARGET_CHAT = -1003770669948
BOT_ID = 8928336881


def _message(text="hello", *, chat_id=TARGET_CHAT, reply_to_bot=False):
    reply = None
    if reply_to_bot:
        reply = SimpleNamespace(from_user=SimpleNamespace(id=BOT_ID, is_bot=True))
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type="supergroup", is_forum=True),
        from_user=SimpleNamespace(id=111, is_bot=False, username="srg"),
        sender_chat=None,
        text=text,
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=5391,
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
            "require_mention_chats": [str(TARGET_CHAT)],
            "reply_trigger_disabled_chats": [str(TARGET_CHAT)],
            "free_response_chats": [],
            "allowed_chats": [],
            "allowed_topics": [],
            "ignored_threads": [],
            "guest_mode": False,
            "exclusive_bot_mentions": True,
            "mention_patterns": [r"@Human20Bot\b"],
        },
    )
    adapter._bot = SimpleNamespace(id=BOT_ID, username="Human20Bot")
    adapter._mention_patterns = adapter._compile_mention_patterns()
    return adapter


def test_live_plugin_target_chat_is_direct_mention_only():
    adapter = _adapter()

    assert adapter._should_process_message(_message("обычное сообщение")) is False
    assert adapter._should_process_message(_message("вопрос", reply_to_bot=True)) is False
    assert adapter._should_process_message(_message("@VladisFom вопрос")) is False
    assert adapter._should_process_message(_message("@chipshermesbot вопрос")) is False
    assert adapter._should_process_message(_message("@Human20Bot вопрос")) is True


def test_live_plugin_policy_is_scoped_to_configured_chat():
    adapter = _adapter()

    assert adapter._should_process_message(_message("обычное сообщение", chat_id=-100999)) is True
