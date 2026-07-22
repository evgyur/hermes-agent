"""Regression coverage for the live Telegram platform plugin.

The core adapter has its own business-mirror guard, but production loads
plugins/platforms/telegram/adapter.py. Keep this test pinned to the plugin file
so future core/plugin drift cannot reintroduce duplicate Telegram turns.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import Platform, PlatformConfig


_PLUGIN_PATH = Path(__file__).resolve().parents[2] / "plugins/platforms/telegram/adapter.py"


def _load_plugin_adapter_class():
    module_name = "test_active_telegram_plugin_adapter"
    spec = importlib.util.spec_from_file_location(module_name, _PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.TelegramAdapter


def _adapter():
    adapter_class = _load_plugin_adapter_class()
    adapter = object.__new__(adapter_class)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(
        enabled=True,
        token="***",
        extra={"require_mention": False},
    )
    adapter._bot = SimpleNamespace(id=8533179145, username="chipshermesbot")
    return adapter


def _dm(*, chat_id, from_user_id, business_connection_id=None):
    return SimpleNamespace(
        text="same owner-authored message",
        caption=None,
        chat=SimpleNamespace(id=chat_id, type="private"),
        from_user=SimpleNamespace(id=from_user_id, is_bot=False),
        business_connection_id=business_connection_id,
        message_thread_id=None,
    )


def test_live_plugin_drops_business_mirror_of_its_own_bot_dialog():
    adapter = _adapter()
    mirror = _dm(
        chat_id=8533179145,
        from_user_id=617744661,
        business_connection_id="business-mirror",
    )

    assert adapter._should_process_message(mirror) is False


def test_live_plugin_drops_bot_dialog_mirror_when_business_id_is_missing():
    adapter = _adapter()
    mirror = _dm(
        chat_id=8533179145,
        from_user_id=617744661,
        business_connection_id=None,
    )

    assert adapter._should_process_message(mirror) is False


def test_live_plugin_preserves_normal_owner_dm():
    adapter = _adapter()
    normal_dm = _dm(chat_id=617744661, from_user_id=617744661)

    assert adapter._should_process_message(normal_dm) is True


def test_live_plugin_preserves_third_party_business_dm():
    adapter = _adapter()
    third_party_dm = _dm(
        chat_id=95948382,
        from_user_id=95948382,
        business_connection_id="business-third-party",
    )

    assert adapter._should_process_message(third_party_dm) is True

@pytest.mark.asyncio
async def test_live_plugin_retains_business_route_before_rejecting_unauthorized_text(
    tmp_path, monkeypatch
):
    adapter = _adapter()
    adapter.config.extra["allow_from"] = ["617744661"]
    store = tmp_path / "telegram_business_connections.json"
    monkeypatch.setattr(
        adapter,
        "_business_connection_store_path",
        lambda: store,
        raising=False,
    )
    adapter._build_message_event = MagicMock()
    adapter._enqueue_text_event = MagicMock()
    message = _dm(
        chat_id=700000042,
        from_user_id=700000042,
        business_connection_id="business-external",
    )
    update = SimpleNamespace(update_id=7001, effective_message=message, message=None)

    await adapter._handle_text_message(update, SimpleNamespace())

    assert json.loads(store.read_text(encoding="utf-8")) == {
        "700000042": "business-external"
    }
    adapter._build_message_event.assert_not_called()
    adapter._enqueue_text_event.assert_not_called()
