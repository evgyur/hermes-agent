"""Fail-closed startup replay routing for Telegram delivery obligations."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource, build_session_key


def _adapter(*, owner_profile: str | None):
    adapter = MagicMock()
    adapter._owner_profile = owner_profile
    adapter.send = AsyncMock(return_value=SimpleNamespace(success=True, error=""))
    return adapter


def _runner(*, primary=None, profile_adapters=None):
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: primary} if primary is not None else {}
    runner._profile_adapters = profile_adapters or {}
    store = MagicMock()
    store.clear_resume_pending = AsyncMock()
    store._store = None
    runner.session_store = None
    runner._async_session_store = store
    return runner


def _row(*, route_envelope, chat_id: str = "700000321") -> dict:
    runtime_profile = (
        str(route_envelope.get("runtime_profile") or "runtime-a")
        if isinstance(route_envelope, dict)
        else "runtime-a"
    )
    route = route_envelope if isinstance(route_envelope, dict) else {}
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=str(route.get("chat_id") or "700000321"),
        chat_type="dm",
        user_id=str(route.get("user_id") or "700000111"),
        thread_id=route.get("thread_id"),
        profile=runtime_profile,
        transport_profile=str(route.get("transport_profile") or "transport-b"),
        business_connection_id=route.get("business_connection_id"),
        external_safe_mode=bool(route.get("external_safe_mode")),
    )
    return {
        "obligation_id": "business-replay",
        "session_key": build_session_key(source),
        "platform": "telegram",
        "chat_id": chat_id,
        "thread_id": "77",
        "content": "private final",
        "needs_marker": False,
        "attempts": 1,
        "route_envelope": route_envelope,
    }


def _business_route(**updates) -> dict:
    route = {
        "version": 1,
        "platform": "telegram",
        "runtime_profile": "runtime-a",
        "transport_profile": "transport-b",
        "chat_id": "700000321",
        "thread_id": "77",
        "user_id": "700000111",
        "business_connection_id": "conn-42",
        "external_safe_mode": True,
    }
    route.update(updates)
    return route


@pytest.mark.asyncio
async def test_business_replay_uses_exact_transport_and_full_route_metadata():
    primary = _adapter(owner_profile=None)
    exact = _adapter(owner_profile="transport-b")
    runner = _runner(
        primary=primary,
        profile_adapters={"transport-b": {Platform.TELEGRAM: exact}},
    )

    with patch("gateway.delivery_ledger.mark_delivered") as mark_delivered:
        delivered = await runner._redeliver_claimed_obligations(
            [_row(route_envelope=_business_route())]
        )

    assert delivered == 1
    primary.send.assert_not_awaited()
    exact.send.assert_awaited_once()
    sent = exact.send.await_args.kwargs
    assert sent["chat_id"] == "700000321"
    assert sent["content"] == "private final"
    assert sent["metadata"]["thread_id"] == "77"
    assert sent["metadata"]["business_connection_id"] == "conn-42"
    assert sent["metadata"]["external_safe_mode"] is True
    assert sent["metadata"]["telegram_business_external_contact"] is True
    assert sent["metadata"]["profile"] == "runtime-a"
    assert sent["metadata"]["transport_profile"] == "transport-b"
    mark_delivered.assert_called_once_with("business-replay")


@pytest.mark.asyncio
async def test_default_transport_replay_uses_only_the_default_adapter():
    primary = _adapter(owner_profile=None)
    runner = _runner(primary=primary)
    route = _business_route(
        runtime_profile="default",
        transport_profile="default",
        business_connection_id=None,
        external_safe_mode=False,
    )

    with patch("gateway.delivery_ledger.mark_delivered") as mark_delivered:
        delivered = await runner._redeliver_claimed_obligations(
            [_row(route_envelope=route)]
        )

    assert delivered == 1
    sent = primary.send.await_args.kwargs
    assert sent["metadata"]["profile"] == "default"
    assert sent["metadata"]["transport_profile"] == "default"
    assert "business_connection_id" not in sent["metadata"]
    mark_delivered.assert_called_once_with("business-replay")


@pytest.mark.parametrize(
    "route_envelope",
    [
        None,
        {
            "version": 1,
            "platform": "telegram",
            "runtime_profile": "runtime-a",
            # No transport_profile: replay must not guess the default bot.
            "chat_id": "700000321",
            "thread_id": "77",
            "user_id": "700000111",
            "business_connection_id": "conn-42",
            "external_safe_mode": True,
        },
        _business_route(chat_id="999999999"),
    ],
)
@pytest.mark.asyncio
async def test_legacy_or_mismatched_route_is_quarantined_without_send(route_envelope):
    primary = _adapter(owner_profile=None)
    runner = _runner(primary=primary)

    with patch("gateway.delivery_ledger.mark_abandoned") as mark_abandoned:
        delivered = await runner._redeliver_claimed_obligations(
            [_row(route_envelope=route_envelope)]
        )

    assert delivered == 0
    primary.send.assert_not_awaited()
    mark_abandoned.assert_called_once_with(
        "business-replay", "ambiguous_route_envelope"
    )


@pytest.mark.parametrize("register_wrong_owner", [False, True])
@pytest.mark.asyncio
async def test_missing_or_mismatched_transport_never_falls_back_to_primary(
    register_wrong_owner,
):
    primary = _adapter(owner_profile=None)
    profile_adapters = {}
    wrong = None
    if register_wrong_owner:
        wrong = _adapter(owner_profile="some-other-profile")
        profile_adapters = {"transport-b": {Platform.TELEGRAM: wrong}}
    runner = _runner(primary=primary, profile_adapters=profile_adapters)

    with patch("gateway.delivery_ledger.mark_failed") as mark_failed:
        delivered = await runner._redeliver_claimed_obligations(
            [_row(route_envelope=_business_route())]
        )

    assert delivered == 0
    primary.send.assert_not_awaited()
    if wrong is not None:
        wrong.send.assert_not_awaited()
    mark_failed.assert_called_once_with(
        "business-replay", "delivery_route_unavailable"
    )


@pytest.mark.asyncio
async def test_claim_considers_a_secondary_only_connected_platform():
    secondary = _adapter(owner_profile="transport-b")
    runner = _runner(
        profile_adapters={"transport-b": {Platform.TELEGRAM: secondary}}
    )

    with (
        patch("gateway.delivery_ledger.ledger_enabled", return_value=True),
        patch("gateway.delivery_ledger.sweep_recoverable", return_value=[]) as sweep,
    ):
        assert await runner._claim_pending_obligations() == []

    assert sweep.call_args.kwargs["deliverable_platforms"] == {"telegram"}


@pytest.mark.asyncio
async def test_claim_advertises_supported_secondary_nontelegram_route():
    secondary = _adapter(owner_profile="transport-b")
    runner = _runner(
        profile_adapters={"transport-b": {Platform.SLACK: secondary}}
    )

    with (
        patch("gateway.delivery_ledger.ledger_enabled", return_value=True),
        patch("gateway.delivery_ledger.sweep_recoverable", return_value=[]) as sweep,
    ):
        assert await runner._claim_pending_obligations() == []

    assert sweep.call_args.kwargs["deliverable_platforms"] == {"slack"}
    assert ("slack", "transport-b") in sweep.call_args.kwargs[
        "deliverable_targets"
    ]
