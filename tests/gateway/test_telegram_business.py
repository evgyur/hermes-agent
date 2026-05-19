"""Tests for Telegram Business delegated inbox routing."""

import importlib
import sys
import types
from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import _thread_metadata_for_source
from gateway.session import SessionSource, build_session_key


class _FakeMessageHandler:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _FakeBusinessConnectionHandler(_FakeMessageHandler):
    pass


class _FakeBusinessMessagesDeletedHandler(_FakeMessageHandler):
    pass


class _FakeInlineKeyboardButton:
    def __init__(self, text, callback_data=None, **kwargs):
        self.text = text
        self.callback_data = callback_data
        self.kwargs = kwargs


class _FakeInlineKeyboardMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


class _FakeFilter:
    def __and__(self, other):
        return self

    def __or__(self, other):
        return self

    def __invert__(self):
        return self


@pytest.fixture
def telegram_mod(monkeypatch):
    original_modules = {
        name: sys.modules.get(name)
        for name in (
            "telegram",
            "telegram.constants",
            "telegram.ext",
            "telegram.request",
        )
    }

    fake_telegram = types.ModuleType("telegram")
    fake_telegram.Update = SimpleNamespace(ALL_TYPES=("message", "business_message"))
    fake_telegram.Bot = object
    fake_telegram.Message = object
    fake_telegram.InlineKeyboardButton = _FakeInlineKeyboardButton
    fake_telegram.InlineKeyboardMarkup = _FakeInlineKeyboardMarkup

    fake_constants = types.ModuleType("telegram.constants")
    fake_constants.ParseMode = SimpleNamespace(
        MARKDOWN_V2="MarkdownV2",
        MARKDOWN="Markdown",
        HTML="HTML",
    )
    fake_constants.ChatType = SimpleNamespace(
        GROUP="group",
        SUPERGROUP="supergroup",
        CHANNEL="channel",
        PRIVATE="private",
    )

    fake_ext = types.ModuleType("telegram.ext")
    fake_ext.Application = object
    fake_ext.CommandHandler = object
    fake_ext.CallbackQueryHandler = object
    fake_ext.MessageHandler = _FakeMessageHandler
    fake_ext.BusinessConnectionHandler = _FakeBusinessConnectionHandler
    fake_ext.BusinessMessagesDeletedHandler = _FakeBusinessMessagesDeletedHandler
    fake_ext.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
    fake_ext.filters = SimpleNamespace(
        UpdateType=SimpleNamespace(BUSINESS_MESSAGE=_FakeFilter()),
        TEXT=_FakeFilter(),
        COMMAND=_FakeFilter(),
    )

    fake_request = types.ModuleType("telegram.request")
    fake_request.HTTPXRequest = object

    monkeypatch.setitem(sys.modules, "telegram", fake_telegram)
    monkeypatch.setitem(sys.modules, "telegram.constants", fake_constants)
    monkeypatch.setitem(sys.modules, "telegram.ext", fake_ext)
    monkeypatch.setitem(sys.modules, "telegram.request", fake_request)

    import gateway.platforms.telegram as telegram_module

    module = importlib.reload(telegram_module)
    try:
        yield module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        importlib.reload(telegram_module)


def _make_adapter(telegram_mod, *, business_enabled=True):
    config = PlatformConfig(
        enabled=True,
        token="fake-token",
        extra={
            "business": {
                "enabled": business_enabled,
                "trigger_words": ["Sigurd", "Сигурд", "Argus"],
                "allowed_chats": [],
            }
        },
    )
    adapter = object.__new__(telegram_mod.TelegramAdapter)
    adapter.config = config
    adapter._config = config
    adapter._platform = Platform.TELEGRAM
    adapter.platform = Platform.TELEGRAM
    adapter._bot = SimpleNamespace(id=999, username="chipshermesbot")
    adapter._mention_patterns = []
    adapter._dm_topics = {}
    adapter._dm_topics_config = []
    adapter._reply_to_mode = "first"
    return adapter


def _business_message(telegram_mod, *, text="Sigurd, hello", user_id=123, is_bot=False):
    return SimpleNamespace(
        text=text,
        caption=None,
        chat=SimpleNamespace(
            id=456,
            type=telegram_mod.ChatType.PRIVATE,
            title=None,
            full_name="External User",
        ),
        from_user=SimpleNamespace(id=user_id, full_name="External User", is_bot=is_bot),
        message_thread_id=None,
        reply_to_message=None,
        message_id=42,
        date=None,
        business_connection_id="bc-123",
    )


def test_business_source_metadata_survives_without_thread_id():
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="456",
        chat_type="dm",
        user_id="123",
        business_connection_id="bc-123",
        external_safe_mode=True,
    )

    metadata = _thread_metadata_for_source(source)

    assert metadata == {
        "business_connection_id": "bc-123",
        "external_safe_mode": True,
        "telegram_business_external_contact": True,
    }


def test_business_session_key_isolates_actor_and_safe_mode():
    trusted = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="456",
        chat_type="dm",
        user_id="1",
        business_connection_id="bc-123",
        external_safe_mode=False,
    )
    external = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="456",
        chat_type="dm",
        user_id="2",
        business_connection_id="bc-123",
        external_safe_mode=True,
    )

    assert build_session_key(trusted) == "agent:main:telegram:business:456:1:trusted"
    assert build_session_key(external) == "agent:main:telegram:business:456:2:external"


def test_business_source_serializes_business_fields():
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="456",
        business_connection_id="bc-123",
        external_safe_mode=True,
    )

    restored = SessionSource.from_dict(source.to_dict())

    assert restored.business_connection_id == "bc-123"
    assert restored.external_safe_mode is True


@pytest.mark.asyncio
async def test_business_message_accepts_trigger_and_marks_external_safe(monkeypatch, telegram_mod):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "")
    adapter = _make_adapter(telegram_mod)
    captured = []

    async def handle(event):
        captured.append(event)

    adapter.handle_message = handle

    await adapter._handle_business_message(
        SimpleNamespace(update_id=7, business_message=_business_message(telegram_mod)),
        SimpleNamespace(),
    )

    assert len(captured) == 1
    event = captured[0]
    assert event.text == "hello"
    assert event.source.business_connection_id == "bc-123"
    assert event.source.external_safe_mode is True


@pytest.mark.asyncio
async def test_business_message_ignores_missing_trigger(monkeypatch, telegram_mod):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "")
    adapter = _make_adapter(telegram_mod)
    captured = []
    adapter.handle_message = lambda event: captured.append(event)

    await adapter._handle_business_message(
        SimpleNamespace(
            update_id=7,
            business_message=_business_message(telegram_mod, text="ordinary customer text"),
        ),
        SimpleNamespace(),
    )

    assert captured == []


def test_business_trust_does_not_treat_wildcard_as_operator(monkeypatch, telegram_mod):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "*")
    adapter = _make_adapter(telegram_mod)

    assert adapter._is_business_trusted_actor(_business_message(telegram_mod, user_id=123)) is False


def test_business_kwargs_from_metadata(telegram_mod):
    adapter = _make_adapter(telegram_mod)

    assert adapter._business_kwargs({"business_connection_id": "bc-123"}) == {
        "business_connection_id": "bc-123"
    }


def test_top_level_telegram_business_config_is_bridged(tmp_path, monkeypatch):
    import gateway.config as config_mod

    (tmp_path / "config.yaml").write_text(
        """
telegram:
  business:
    enabled: true
    trigger_words:
      - Sigurd
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_mod, "get_hermes_home", lambda: tmp_path)

    cfg = config_mod.load_gateway_config()

    business = cfg.platforms[Platform.TELEGRAM].extra["business"]
    assert business["enabled"] is True
    assert business["trigger_words"] == ["Sigurd"]
