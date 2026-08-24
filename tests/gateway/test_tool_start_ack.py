"""Gateway delivery half of the deterministic pre-tool acknowledgment."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.start_ack import StartAckReceipt
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import SendResult
from gateway.run import (
    GatewayRunner,
    MultiplexConfigError,
    TurnRunner,
    _resolve_start_ack_policy,
    _resolve_start_ack_text,
    _validate_start_ack_runtime_support,
)
from gateway.session import SessionSource
from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig
from gateway.turn_context import TurnContext
from plugins.platforms.telegram.adapter import TelegramAdapter
from gateway.profile_routing import ProfileRoute
from tools.async_delegation import TrustedRestartEvent
from tools.parent_task_barrier import TrustedParentTaskContinuation


@pytest.mark.asyncio
async def test_start_ack_preserves_topic_reply_metadata_and_tracks_cleanup():
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="ack-42"))
    )
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-100123",
        chat_type="group",
        thread_id="1858",
    )
    gateway_runner = object.__new__(GatewayRunner)
    metadata = gateway_runner._thread_metadata_for_source(
        source, reply_to_message_id="47289"
    )
    assert metadata == {"thread_id": "1858"}
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "↳ Принял. Начинаю проверку."
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._status_thread_metadata = metadata
    ctx._loop_for_step = asyncio.get_running_loop()
    ctx.event_message_id = "47289"
    ctx._cleanup_progress = True
    runner = TurnRunner(SimpleNamespace(), ctx)

    delivered = await asyncio.to_thread(runner.start_ack_callback)

    assert isinstance(delivered, StartAckReceipt)
    assert delivered.text == ctx.start_ack_text
    assert delivered.message_id == "ack-42"
    adapter.send.assert_awaited_once_with(
        source.chat_id,
        "↳ Принял. Начинаю проверку.",
        reply_to="47289",
        metadata={
            **metadata,
            "_interim_send": True,
        },
    )
    assert ctx._cleanup_msg_ids == ["ack-42"]


@pytest.mark.asyncio
async def test_start_ack_fails_open_when_delivery_fails():
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=False, error="offline"))
    )
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "↳ Принял. Начинаю проверку."
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    runner = TurnRunner(SimpleNamespace(), ctx)

    assert await asyncio.to_thread(runner.start_ack_callback) is False
    assert ctx._cleanup_msg_ids == []


@pytest.mark.asyncio
async def test_start_ack_timeout_cancels_blocked_send_without_late_delivery():
    started = asyncio.Event()
    cancelled = asyncio.Event()
    delivered = []

    async def blocked_send(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        delivered.append(True)

    adapter = SimpleNamespace(send=blocked_send)
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "configured"
    ctx.start_ack_timeout_s = 0.02
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    runner = TurnRunner(SimpleNamespace(), ctx)

    assert await asyncio.to_thread(runner.start_ack_callback) is False
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    await asyncio.sleep(0)
    assert started.is_set()
    assert delivered == []


@pytest.mark.parametrize(
    "platform,kwargs",
    [
        (Platform.WEBHOOK, {}),
        (Platform.TELEGRAM, {"startup_resume": True}),
        (
            Platform.TELEGRAM,
            {"persist_user_display_kind": "internal_notification"},
        ),
    ],
)
def test_start_ack_is_suppressed_for_non_user_turn_origins(platform, kwargs):
    config = {
        "display": {
            "platforms": {
                "telegram": {"start_ack_text": "configured"},
                "webhook": {"start_ack_text": "configured"},
            }
        }
    }

    assert _resolve_start_ack_text(config, platform.value, platform, **kwargs) == ""


def test_start_ack_is_enabled_for_an_ordinary_configured_telegram_turn():
    config = {
        "display": {
            "platforms": {"telegram": {"start_ack_text": "  configured  "}}
        }
    }

    assert (
        _resolve_start_ack_text(config, "telegram", Platform.TELEGRAM)
        == "configured"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"trusted_restart_wake": {}},
        {"trusted_restart_wake": object()},
        {"trusted_parent_task_continuation": {}},
        {"trusted_parent_task_continuation": object()},
    ],
)
def test_untrusted_origin_markers_cannot_suppress_start_ack(kwargs):
    config = {
        "display": {
            "platforms": {"telegram": {"start_ack_text": "configured"}}
        }
    }

    assert (
        _resolve_start_ack_text(config, "telegram", Platform.TELEGRAM, **kwargs)
        == "configured"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"trusted_restart_wake": TrustedRestartEvent()},
        {
            "trusted_parent_task_continuation": TrustedParentTaskContinuation()
        },
    ],
)
def test_typed_but_unclaimed_markers_do_not_suppress_start_ack(kwargs):
    config = {
        "display": {
            "platforms": {"telegram": {"start_ack_text": "configured"}}
        }
    }

    assert (
        _resolve_start_ack_text(config, "telegram", Platform.TELEGRAM, **kwargs)
        == "configured"
    )


def test_required_policy_cannot_silently_downgrade_with_empty_text():
    config = {
        "display": {
            "platforms": {
                "telegram": {"start_ack_mode": "required", "start_ack_text": ""}
            }
        }
    }

    with pytest.raises(ValueError, match="must be non-empty"):
        _resolve_start_ack_policy(config, "telegram", Platform.TELEGRAM)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply_mode,expected_reply",
    [("first", 47289), ("off", None)],
)
async def test_start_ack_reaches_real_telegram_forum_wire(
    reply_mode, expected_reply
):
    adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="test-token", reply_to_mode=reply_mode)
    )
    adapter._bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=99))
    )
    adapter._rich_messages_enabled = False
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-100123",
        chat_type="group",
        thread_id="1858",
    )
    gateway_runner = object.__new__(GatewayRunner)
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "configured"
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._status_thread_metadata = gateway_runner._thread_metadata_for_source(
        source, reply_to_message_id="47289"
    )
    ctx.event_message_id = "47289"
    ctx._loop_for_step = asyncio.get_running_loop()

    assert await asyncio.to_thread(TurnRunner(SimpleNamespace(), ctx).start_ack_callback)
    wire = adapter._bot.send_message.await_args.kwargs
    assert wire["chat_id"] == -100123
    assert wire["message_thread_id"] == 1858
    assert wire["reply_to_message_id"] == expected_reply


@pytest.mark.asyncio
async def test_start_ack_accepts_exact_real_stream_consumer_receipt_without_duplicate():
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="stream-1")),
        edit_message=AsyncMock(return_value=SendResult(success=True, message_id="stream-1")),
        MAX_MESSAGE_LENGTH=4096,
    )
    consumer = GatewayStreamConsumer(
        adapter,
        "chat-1",
        StreamConsumerConfig(edit_interval=0.01, buffer_threshold=1),
    )
    consumer.on_delta("Checking now.")
    task = asyncio.create_task(consumer.run())

    source = SessionSource(platform=Platform.TELEGRAM, chat_id="chat-1", chat_type="dm")
    agent = SimpleNamespace(_pending_start_ack_visible_text="Checking now.")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "fallback"
    ctx.interim_assistant_messages_enabled = True
    ctx.interim_assistant_messages_mode = "raw"
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    ctx.agent_holder[0] = agent
    ctx.stream_consumer_holder[0] = consumer

    assert await asyncio.to_thread(TurnRunner(SimpleNamespace(), ctx).start_ack_callback)
    assert adapter.send.await_count + adapter.edit_message.await_count == 1
    assert consumer.has_delivered_text("Checking now.") is True

    consumer.finish()
    await task


@pytest.mark.asyncio
async def test_partial_stream_receipt_does_not_satisfy_exact_start_ack_text():
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="m1")),
        edit_message=AsyncMock(return_value=SendResult(success=True, message_id="m1")),
        MAX_MESSAGE_LENGTH=4096,
    )
    consumer = GatewayStreamConsumer(
        adapter,
        "chat-1",
        StreamConsumerConfig(edit_interval=0.01, buffer_threshold=1),
    )
    consumer.on_delta("Partial")
    task = asyncio.create_task(consumer.run())

    source = SessionSource(platform=Platform.TELEGRAM, chat_id="chat-1", chat_type="dm")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "fallback"
    ctx.interim_assistant_messages_enabled = True
    ctx.interim_assistant_messages_mode = "raw"
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    ctx.agent_holder[0] = SimpleNamespace(
        _pending_start_ack_visible_text="Complete commentary"
    )
    ctx.stream_consumer_holder[0] = consumer

    assert await asyncio.to_thread(TurnRunner(SimpleNamespace(), ctx).start_ack_callback)
    assert adapter.send.await_count + adapter.edit_message.await_count == 2

    consumer.finish()
    await task


@pytest.mark.asyncio
async def test_failed_real_stream_delivery_does_not_satisfy_required_receipt():
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=False, error="offline")),
        edit_message=AsyncMock(return_value=SendResult(success=False, error="offline")),
        MAX_MESSAGE_LENGTH=4096,
    )
    consumer = GatewayStreamConsumer(
        adapter,
        "chat-1",
        StreamConsumerConfig(edit_interval=0.01, buffer_threshold=1),
    )
    consumer.on_delta("Checking now.")
    task = asyncio.create_task(consumer.run())

    source = SessionSource(platform=Platform.TELEGRAM, chat_id="chat-1", chat_type="dm")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "fallback"
    ctx.interim_assistant_messages_enabled = True
    ctx.interim_assistant_messages_mode = "raw"
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    ctx.agent_holder[0] = SimpleNamespace(
        _pending_start_ack_visible_text="Checking now."
    )
    ctx.stream_consumer_holder[0] = consumer

    assert await asyncio.to_thread(TurnRunner(SimpleNamespace(), ctx).start_ack_callback) is False
    assert consumer.has_delivered_text("Checking now.") is False

    consumer.finish()
    await task


@pytest.mark.asyncio
async def test_unresolved_stream_flush_does_not_race_a_second_ack_send():
    consumer = SimpleNamespace(
        flush_pending_sync=lambda timeout: False,
        has_delivered_text=lambda text: False,
    )
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="duplicate"))
    )
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "fallback"
    ctx.interim_assistant_messages_enabled = True
    ctx.interim_assistant_messages_mode = "raw"
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    ctx.agent_holder[0] = SimpleNamespace(
        _pending_start_ack_visible_text="Unresolved commentary"
    )
    ctx.stream_consumer_holder[0] = consumer

    assert await asyncio.to_thread(TurnRunner(SimpleNamespace(), ctx).start_ack_callback) is False
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_stream_send_cannot_authorize_or_duplicate_ack():
    visible_deliveries = []

    async def ambiguous_send(*args, **kwargs):
        content = args[1] if len(args) > 1 else kwargs.get("content", "")
        visible_deliveries.append(content)
        raise RuntimeError("response lost after remote acceptance")

    adapter = SimpleNamespace(
        send=ambiguous_send,
        edit_message=AsyncMock(return_value=SendResult(success=True)),
        MAX_MESSAGE_LENGTH=4096,
    )
    consumer = GatewayStreamConsumer(
        adapter,
        "chat-1",
        StreamConsumerConfig(edit_interval=0.01, buffer_threshold=1),
    )
    text = "Starting the requested work."
    consumer.on_commentary(text)
    task = asyncio.create_task(consumer.run())

    source = SessionSource(platform=Platform.TELEGRAM, chat_id="chat-1", chat_type="dm")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "fallback"
    ctx.interim_assistant_messages_enabled = True
    ctx.interim_assistant_messages_mode = "raw"
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    ctx.agent_holder[0] = SimpleNamespace(_pending_start_ack_visible_text=text)
    ctx.stream_consumer_holder[0] = consumer

    assert await asyncio.to_thread(TurnRunner(SimpleNamespace(), ctx).start_ack_callback) is False
    consumer.finish()
    await task
    assert visible_deliveries == [text]


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["NO_REPLY", "[SILENT]"])
async def test_silence_control_marker_uses_configured_ack(marker):
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="ack-1"))
    )
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "configured fallback"
    ctx.interim_assistant_messages_enabled = True
    ctx.interim_assistant_messages_mode = "raw"
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    ctx.agent_holder[0] = SimpleNamespace(_pending_start_ack_visible_text=marker)

    assert await asyncio.to_thread(TurnRunner(SimpleNamespace(), ctx).start_ack_callback)
    assert adapter.send.await_args.args[1] == "configured fallback"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "generic"])
async def test_hidden_or_redacted_commentary_uses_configured_fallback(mode):
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="fallback-1"))
    )
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm")
    ctx = TurnContext(source=source, _run_still_current=lambda: True)
    ctx.start_ack_text = "operator-approved fallback"
    ctx.interim_assistant_messages_enabled = mode != "off"
    ctx.interim_assistant_messages_mode = mode
    ctx._status_adapter = adapter
    ctx._status_chat_id = source.chat_id
    ctx._loop_for_step = asyncio.get_running_loop()
    ctx.agent_holder[0] = SimpleNamespace(
        _pending_start_ack_visible_text="raw model narration"
    )

    assert await asyncio.to_thread(TurnRunner(SimpleNamespace(), ctx).start_ack_callback)
    assert adapter.send.await_args.args[1] == "operator-approved fallback"


@pytest.mark.asyncio
async def test_required_ack_rejects_proxy_before_remote_request(monkeypatch):
    runner = object.__new__(GatewayRunner)
    runner._run_agent_via_proxy = AsyncMock()
    monkeypatch.setattr(runner, "_get_proxy_url", lambda: "https://proxy.invalid")
    monkeypatch.setattr(
        "gateway.run._load_gateway_config",
        lambda: {
            "display": {
                "platforms": {
                    "telegram": {
                        "start_ack_mode": "required",
                        "start_ack_text": "ack",
                    }
                }
            }
        },
    )

    with pytest.raises(RuntimeError, match="unsupported with proxy mode"):
        await runner._run_agent(
            "go",
            "",
            [],
            SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm"),
            "session-1",
        )

    runner._run_agent_via_proxy.assert_not_awaited()


@pytest.mark.asyncio
async def test_best_effort_ack_keeps_proxy_mode_available(monkeypatch):
    runner = object.__new__(GatewayRunner)
    runner._run_agent_via_proxy = AsyncMock(return_value={"completed": True})
    monkeypatch.setattr(runner, "_get_proxy_url", lambda: "https://proxy.invalid")
    monkeypatch.setattr(
        "gateway.run._load_gateway_config",
        lambda: {
            "display": {
                "platforms": {
                    "telegram": {
                        "start_ack_mode": "best_effort",
                        "start_ack_text": "ack",
                    }
                }
            }
        },
    )

    result = await runner._run_agent(
        "go",
        "",
        [],
        SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm"),
        "session-1",
    )

    assert result == {"completed": True}
    runner._run_agent_via_proxy.assert_awaited_once()


@pytest.mark.asyncio
async def test_secondary_profile_ack_policy_is_validated_before_adapter_connect(
    monkeypatch, tmp_path
):
    profile_cfg = GatewayConfig(
        multiplex_profiles=True,
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="secondary-token")
        },
    )
    invalid_runtime = {
        "display": {
            "platforms": {
                "telegram": {"start_ack_mode": "required", "start_ack_text": ""}
            }
        }
    }
    runner = object.__new__(GatewayRunner)
    runner.config = profile_cfg
    runner._profile_adapters = {}
    monkeypatch.setattr(
        "gateway.run._load_gateway_runtime_config", lambda: invalid_runtime
    )
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: profile_cfg)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    connect = AsyncMock()
    runner._connect_adapter_with_timeout = connect

    with pytest.raises(MultiplexConfigError, match="Profile 'hermesdev'.*start_ack"):
        await runner._start_one_profile_adapters(
            "hermesdev", tmp_path / "hermesdev", {}
        )

    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_secondary_profile_unsupported_ack_runtime_fails_before_connect(
    monkeypatch, tmp_path
):
    profile_cfg = GatewayConfig(
        multiplex_profiles=True,
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="secondary-token")
        },
    )
    strict_runtime_config = {
        "display": {
            "platforms": {
                "telegram": {
                    "start_ack_mode": "required",
                    "start_ack_text": "ack",
                }
            }
        }
    }
    runner = object.__new__(GatewayRunner)
    runner.config = profile_cfg
    runner._profile_adapters = {}
    monkeypatch.setattr(
        "gateway.run._load_gateway_runtime_config",
        lambda: strict_runtime_config,
    )
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: profile_cfg)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    monkeypatch.setattr(
        "gateway.run._resolve_runtime_agent_kwargs",
        lambda: {"api_mode": "codex_app_server", "provider": "openai-codex"},
    )
    connect = AsyncMock()
    runner._connect_adapter_with_timeout = connect

    with pytest.raises(MultiplexConfigError, match="hermesdev.*codex_app_server"):
        await runner._start_one_profile_adapters(
            "hermesdev", tmp_path / "hermesdev", {}
        )

    connect.assert_not_awaited()


@pytest.mark.parametrize(
    "runtime,proxy_url,match",
    [
        ({"api_mode": "chat_completions", "provider": "openai"}, "https://proxy.invalid", "proxy"),
        ({"api_mode": "codex_app_server", "provider": "openai-codex"}, "", "codex_app_server"),
        ({"api_mode": "codex_responses", "provider": "xai"}, "", "provider-executed"),
    ],
)
def test_required_ack_rejects_unsupported_runtime_before_startup(
    runtime, proxy_url, match
):
    display = {
        "display": {
            "platforms": {
                "telegram": {
                    "start_ack_mode": "required",
                    "start_ack_text": "ack",
                }
            }
        }
    }
    gateway_config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="token")}
    )

    with pytest.raises(ValueError, match=match):
        _validate_start_ack_runtime_support(
            display,
            gateway_config,
            runtime=runtime,
            proxy_url=proxy_url,
        )


def test_best_effort_ack_allows_unsupported_strict_runtime_planes():
    display = {
        "display": {
            "platforms": {
                "telegram": {
                    "start_ack_mode": "best_effort",
                    "start_ack_text": "ack",
                }
            }
        }
    }
    gateway_config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="token")}
    )

    _validate_start_ack_runtime_support(
        display,
        gateway_config,
        runtime={"api_mode": "codex_app_server", "provider": "openai-codex"},
        proxy_url="https://proxy.invalid",
    )


def test_custom_xai_base_url_uses_same_strict_runtime_classification():
    display = {
        "display": {
            "platforms": {
                "telegram": {
                    "start_ack_mode": "required",
                    "start_ack_text": "ack",
                }
            }
        }
    }
    gateway_config = GatewayConfig(platforms={})

    with pytest.raises(ValueError, match="provider-executed"):
        _validate_start_ack_runtime_support(
            display,
            gateway_config,
            runtime={
                "api_mode": "codex_responses",
                "provider": "custom-xai-route",
                "base_url": "https://api.x.ai/v1",
            },
            proxy_url="",
            effective_platforms={"telegram"},
        )


def test_shared_route_secondary_is_preflighted_without_own_adapter(
    monkeypatch, tmp_path
):
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        multiplex_profiles=True,
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="primary")
        },
        profile_routes=[
            ProfileRoute(
                name="shared",
                platform="telegram",
                profile="hermesdev",
                chat_id="-1001",
            )
        ],
    )
    strict = {
        "display": {
            "platforms": {
                "telegram": {
                    "start_ack_mode": "required",
                    "start_ack_text": "ack",
                }
            }
        }
    }
    secondary_cfg = GatewayConfig(platforms={})
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        "gateway.run._multiplex_profile_homes",
        lambda config: [("default", tmp_path / "default"), ("hermesdev", tmp_path / "hermesdev")],
    )
    monkeypatch.setattr("gateway.run._load_gateway_runtime_config", lambda: strict)
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: secondary_cfg)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    monkeypatch.setattr(
        "gateway.run._resolve_runtime_agent_kwargs",
        lambda: {"api_mode": "codex_app_server", "provider": "openai-codex"},
    )

    with pytest.raises(MultiplexConfigError, match="hermesdev.*codex_app_server"):
        runner._preflight_multiplex_start_ack_profiles()
