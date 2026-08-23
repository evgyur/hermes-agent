"""Tests for ``BasePlatformAdapter.register_post_delivery_callback`` chaining.

When two features want to run after the final response lands on the same
session (e.g. background-review release + temporary-progress cleanup), the
registration API chains them rather than clobbering. Per-callback
exceptions are swallowed so one bad callback can't sabotage the others.
Stale-generation registrations are rejected.

The chained wrapper is ``async`` so it transparently supports sync or async
callbacks — the outer invoker in ``_handle_message`` awaits awaitable
callbacks, and a sync wrapper would silently drop coroutine results from
async callbacks chained behind it.
"""
import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult


class _MinAdapter(BasePlatformAdapter):
    async def connect(self, *, is_reconnect: bool = False) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        return SendResult(success=True, message_id="1")

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


@pytest.fixture
def adapter():
    return _MinAdapter(PlatformConfig(enabled=True), Platform.TELEGRAM)


def _invoke(cb):
    """Invoke a popped callback, awaiting if it returns a coroutine.

    Single-registration callbacks are returned as the raw user callable
    (sync). Chained callbacks (two or more registrations on the same
    session) are wrapped in an async helper. Tests use this helper so
    they don't have to care which case they're exercising.
    """
    result = cb()
    if inspect.isawaitable(result):
        asyncio.run(result)


class TestPostDeliveryCallbackChaining:
    def test_single_callback_fires(self, adapter):
        fired = []
        adapter.register_post_delivery_callback("s", lambda: fired.append("A"))
        cb = adapter.pop_post_delivery_callback("s")
        _invoke(cb)
        assert fired == ["A"]

    def test_two_callbacks_chain_in_order(self, adapter):
        fired = []
        adapter.register_post_delivery_callback("s", lambda: fired.append("A"))
        adapter.register_post_delivery_callback("s", lambda: fired.append("B"))
        cb = adapter.pop_post_delivery_callback("s")
        _invoke(cb)
        assert fired == ["A", "B"]

    def test_three_callbacks_chain_in_order(self, adapter):
        """Chain composes over an already-chained callback."""
        fired = []
        for label in ("A", "B", "C"):
            adapter.register_post_delivery_callback(
                "s", lambda x=label: fired.append(x)
            )
        cb = adapter.pop_post_delivery_callback("s")
        _invoke(cb)
        assert fired == ["A", "B", "C"]


class TestPostDeliveryCallbackAsyncChaining:
    """When an async callback is chained, the wrapper must await it.

    Regression test for a bug where the sync ``_chained`` wrapper called
    async callbacks without awaiting, silently dropping the returned
    coroutine. This broke ``/goal`` continuations (Discord etc.) where
    the continuation injection is an async ``_deliver()`` coroutine.
    """

    def test_async_callback_in_chain_is_awaited(self, adapter):
        fired = []

        async def async_cb():
            await asyncio.sleep(0)
            fired.append("async")

        adapter.register_post_delivery_callback("s", lambda: fired.append("sync"))
        adapter.register_post_delivery_callback("s", async_cb)
        cb = adapter.pop_post_delivery_callback("s")
        _invoke(cb)
        assert fired == ["sync", "async"]


@pytest.mark.asyncio
async def test_startup_ack_observes_swallowed_background_failure(adapter):
    """A normal Task return is not handler success when the adapter swallowed it."""
    event = __import__(
        "gateway.platforms.base", fromlist=["MessageEvent"]
    ).MessageEvent(
        text="restore me",
        source=__import__(
            "gateway.session", fromlist=["SessionSource"]
        ).SessionSource(
            platform=Platform.TELEGRAM,
            user_id="u",
            chat_id="c",
            chat_type="dm",
        ),
        message_id="startup-failure",
    )
    event._hermes_startup_dispatch_ack = asyncio.Event()
    adapter.config.typing_indicator = False
    adapter._run_processing_hook = AsyncMock()
    adapter._stop_typing_refresh = AsyncMock()

    async def fail_before_dispatch(_event):
        raise RuntimeError("predispatch failed")

    adapter.set_message_handler(fail_before_dispatch)
    assert adapter._start_session_processing(event, "s") is True
    await asyncio.wait_for(event._hermes_startup_dispatch_ack.wait(), timeout=2)
    await asyncio.wait_for(event._hermes_adapter_processing_task, timeout=2)

    assert event._hermes_background_handler_failed is True
    assert not getattr(event, "_hermes_background_processing_completed", False)
    assert event._hermes_background_processing_outcome == "failed"


@pytest.mark.asyncio
async def test_startup_ack_records_authoritative_successful_completion(adapter):
    event = __import__(
        "gateway.platforms.base", fromlist=["MessageEvent"]
    ).MessageEvent(
        text="restore me",
        source=__import__(
            "gateway.session", fromlist=["SessionSource"]
        ).SessionSource(
            platform=Platform.TELEGRAM,
            user_id="u",
            chat_id="c",
            chat_type="dm",
        ),
        message_id="startup-success",
    )
    event._hermes_startup_dispatch_ack = asyncio.Event()
    adapter.config.typing_indicator = False
    adapter._run_processing_hook = AsyncMock()
    adapter._stop_typing_refresh = AsyncMock()
    adapter.set_message_handler(AsyncMock(return_value="done"))

    assert adapter._start_session_processing(event, "s") is True
    await asyncio.wait_for(event._hermes_startup_dispatch_ack.wait(), timeout=2)
    await asyncio.wait_for(event._hermes_adapter_processing_task, timeout=2)

    assert event._hermes_background_processing_completed is True
    assert event._hermes_background_processing_outcome == "completed"

