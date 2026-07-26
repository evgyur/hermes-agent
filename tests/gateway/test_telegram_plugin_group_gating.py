from types import SimpleNamespace

from gateway.config import Platform, PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter


TARGET_CHAT = -1003770669948
BOT_ID = 8928336881


def _message(
    text="hello",
    *,
    chat_id=TARGET_CHAT,
    thread_id=5391,
    reply_to_bot=False,
    reply_to_other_bot=False,
):
    reply = None
    if reply_to_bot:
        reply = SimpleNamespace(from_user=SimpleNamespace(id=BOT_ID, is_bot=True))
    elif reply_to_other_bot:
        reply = SimpleNamespace(from_user=SimpleNamespace(id=8533179145, is_bot=True))
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type="supergroup", is_forum=True),
        from_user=SimpleNamespace(id=111, is_bot=False, username="srg"),
        sender_chat=None,
        text=text,
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=thread_id,
        reply_to_message=reply,
    )


def _adapter(
    *,
    require_mention_chats=None,
    require_mention_topics=None,
    reply_trigger_disabled_chats=None,
    free_response_chats=None,
):
    module = load_plugin_adapter("telegram")
    adapter = object.__new__(module.TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(
        enabled=True,
        token="***",
        extra={
            "require_mention": False,
            "require_mention_chats": [str(TARGET_CHAT)] if require_mention_chats is None else require_mention_chats,
            "require_mention_topics": [] if require_mention_topics is None else require_mention_topics,
            "reply_trigger_disabled_chats": (
                [str(TARGET_CHAT)]
                if reply_trigger_disabled_chats is None
                else reply_trigger_disabled_chats
            ),
            "free_response_chats": [] if free_response_chats is None else free_response_chats,
            "allowed_chats": [],
            "allowed_topics": [],
            "ignored_threads": [],
            "guest_mode": False,
            "exclusive_bot_mentions": True,
            "ignore_other_bot_replies_chats": [str(TARGET_CHAT)],
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


def test_live_plugin_ignores_reply_to_sigurd_in_target_chat():
    adapter = _adapter(
        require_mention_chats=[],
        reply_trigger_disabled_chats=[],
        free_response_chats=[str(TARGET_CHAT)],
    )

    assert adapter._should_process_message(
        _message("продолжай", thread_id=14804, reply_to_other_bot=True)
    ) is False
    assert adapter._should_process_message(
        _message("@Human20Bot подключись", thread_id=14804, reply_to_other_bot=True)
    ) is True


def test_live_plugin_topic_gate_overrides_free_response_chat():
    adapter = _adapter(
        require_mention_chats=[],
        require_mention_topics=[f"{TARGET_CHAT}:14804"],
        reply_trigger_disabled_chats=[],
        free_response_chats=[str(TARGET_CHAT)],
    )

    assert adapter._should_process_message(_message("обычное сообщение", thread_id=14804)) is False
    assert adapter._should_process_message(_message("ответ", thread_id=14804, reply_to_bot=True)) is True
    assert adapter._should_process_message(_message("@Human20Bot вопрос", thread_id=14804)) is True
    assert adapter._should_process_message(_message("обычное сообщение", thread_id=5413)) is True
