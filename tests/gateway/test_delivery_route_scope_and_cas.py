"""Regression coverage for route-scoped reconnect delivery claims."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway import delivery_ledger as dl
from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource, build_route_envelope, build_session_key


@contextmanager
def _scope(home):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(home))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def _failed(home, oid, transport, chat):
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id=chat,
        chat_type="dm",
        user_id="42",
        profile="runtime-a",
        transport_profile=transport,
        business_connection_id=f"business-{transport}",
        external_safe_mode=True,
    )
    with _scope(home):
        dl.record_obligation(
            obligation_id=oid,
            session_key=build_session_key(source),
            platform="telegram",
            chat_id=chat,
            thread_id=None,
            content=oid,
            route_envelope=build_route_envelope(source),
        )
        dl.mark_failed(oid, "send_path_degraded")


def _state(home, oid):
    with _scope(home), dl._connect() as conn:
        return conn.execute(
            "SELECT state, runtime_claim_token FROM delivery_obligations "
            "WHERE obligation_id=?",
            (oid,),
        ).fetchone()


def test_transport_filter_does_not_mutate_other_route_and_abandon_is_claim_cas(
    tmp_path,
):
    home = tmp_path / "runtime-a"
    home.mkdir()
    _failed(home, "via-b", "transport-b", "1001")
    _failed(home, "via-c", "transport-c", "1002")

    with _scope(home):
        rows = dl.sweep_failed_for_runtime(
            "telegram", transport_profile="transport-b"
        )
        assert [row["obligation_id"] for row in rows] == ["via-b"]
        token = rows[0]["runtime_claim_token"]
        assert dl.settle_runtime_claim("via-b", token, delivered=True)
        assert not dl.abandon_runtime_claim("via-b", token, "late quarantine")

    assert _state(home, "via-b")[0] == "delivered"
    assert _state(home, "via-c")[0] == "failed"


@pytest.mark.asyncio
async def test_transport_reconnect_scans_every_runtime_ledger(tmp_path, monkeypatch):
    from gateway import run as run_module

    homes = {
        "runtime-a": tmp_path / "runtime-a",
        "transport-b": tmp_path / "transport-b",
        "transport-c": tmp_path / "transport-c",
    }
    for home in homes.values():
        home.mkdir()
    _failed(homes["runtime-a"], "via-b", "transport-b", "1001")
    _failed(homes["runtime-a"], "via-c", "transport-c", "1002")

    monkeypatch.setattr(run_module, "_profile_runtime_scope", _scope)
    monkeypatch.setattr(
        run_module, "_multiplex_profile_homes", lambda _config: list(homes.items())
    )
    adapter = MagicMock()
    adapter._owner_profile = "transport-b"
    adapter.send = AsyncMock(
        return_value=SimpleNamespace(success=True, error="", message_id="m1")
    )
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(multiplex_profiles=True)
    runner.adapters = {}
    runner._profile_adapters = {
        "transport-b": {Platform.TELEGRAM: adapter}
    }
    runner._async_session_store = MagicMock()
    runner._async_session_store._store = None
    runner._async_session_store.clear_resume_pending_exact = AsyncMock(
        return_value=True
    )
    runner._async_session_store.clear_resume_pending = AsyncMock(
        return_value=True
    )
    runner.session_store = None

    delivered = await runner._redeliver_failed_telegram_for_transport(
        adapter, "transport-b"
    )

    assert delivered == 1
    adapter.send.assert_awaited_once()
    assert _state(homes["runtime-a"], "via-b")[0] == "delivered"
    assert _state(homes["runtime-a"], "via-c")[0] == "failed"
