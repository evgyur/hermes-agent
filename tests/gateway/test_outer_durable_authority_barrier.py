"""Gateway-level regressions for the durable turn-authority boundary."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import InboundContextNote
from tests.gateway.restart_test_helpers import RestartTestAdapter
from tests.gateway.test_gateway_silence_tokens import _event, _runner, _source


class _ContextProbeAdapter(RestartTestAdapter):
    def __init__(self):
        super().__init__()
        self.note_builder = MagicMock(
            return_value=InboundContextNote(
                "ephemeral probe",
                persistence="never",
            )
        )

    def build_ephemeral_context_note(self, event):
        return self.note_builder(event)


def _assert_no_downstream_effects(runner, adapter):
    hook_names = [call.args[0] for call in runner.hooks.emit.await_args_list]
    assert {
        "session:start": hook_names.count("session:start"),
        "adapter:build_ephemeral_context_note": adapter.note_builder.call_count,
        "agent:start": hook_names.count("agent:start"),
        "_run_agent": runner._run_agent.await_count,
    } == {
        "session:start": 0,
        "adapter:build_ephemeral_context_note": 0,
        "agent:start": 0,
        "_run_agent": 0,
    }


@pytest.mark.asyncio
async def test_marker_failure_prevents_all_gateway_hooks_and_agent_entry(
    monkeypatch,
    tmp_path,
):
    """A failed marker commit cannot leak work across the outer boundary."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    runner = _runner(monkeypatch, tmp_path)
    entry = runner.session_store.get_or_create_session.return_value
    entry.updated_at = entry.created_at
    adapter = _ContextProbeAdapter()
    runner.adapters = {Platform.TELEGRAM: adapter}

    # Route preflight succeeds; the authoritative marker commit fails after
    # the triggering row has been persisted by the gateway.
    runner._mark_durable_active_turn = AsyncMock(side_effect=[True, False])
    runner._run_agent = AsyncMock(
        side_effect=AssertionError("agent entry preceded durable authority")
    )

    await runner._handle_message_with_agent(
        _event(),
        _source(),
        entry.session_key,
        1,
    )

    _assert_no_downstream_effects(runner, adapter)


@pytest.mark.asyncio
async def test_session_row_failure_prevents_all_gateway_hooks_and_agent_entry(
    monkeypatch,
    tmp_path,
):
    runner = _runner(monkeypatch, tmp_path)
    entry = runner.session_store.get_or_create_session.return_value
    entry.updated_at = entry.created_at
    adapter = _ContextProbeAdapter()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._mark_durable_active_turn = AsyncMock(return_value=True)
    runner._acquire_gateway_durable_turn_authority = AsyncMock(
        side_effect=RuntimeError("durable gateway session row creation failed")
    )
    runner._run_agent = AsyncMock(
        side_effect=AssertionError("agent entry preceded durable authority")
    )

    with pytest.raises(RuntimeError, match="session row creation failed"):
        await runner._handle_message_with_agent(
            _event(),
            _source(),
            entry.session_key,
            1,
        )

    _assert_no_downstream_effects(runner, adapter)


@pytest.mark.asyncio
async def test_user_row_failure_prevents_all_gateway_hooks_and_agent_entry(
    monkeypatch,
    tmp_path,
):
    runner = _runner(monkeypatch, tmp_path)
    entry = runner.session_store.get_or_create_session.return_value
    entry.updated_at = entry.created_at
    adapter = _ContextProbeAdapter()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._mark_durable_active_turn = AsyncMock(return_value=True)
    runner._acquire_gateway_durable_turn_authority = AsyncMock(
        return_value={"db": MagicMock(), "holder": "holder", "ttl_seconds": 300.0}
    )
    runner._validate_and_seal_startup_resume = AsyncMock(return_value=True)
    runner._persist_gateway_triggering_user_row = AsyncMock(
        side_effect=RuntimeError("triggering user row write failed")
    )
    runner._run_agent = AsyncMock(
        side_effect=AssertionError("agent entry preceded durable authority")
    )

    with pytest.raises(RuntimeError, match="user row write failed"):
        await runner._handle_message_with_agent(
            _event(),
            _source(),
            entry.session_key,
            1,
        )

    _assert_no_downstream_effects(runner, adapter)


@pytest.mark.asyncio
async def test_external_turn_without_platform_message_id_fails_before_authority(
    monkeypatch,
    tmp_path,
):
    runner = _runner(monkeypatch, tmp_path)
    entry = runner.session_store.get_or_create_session.return_value
    entry.updated_at = entry.created_at
    adapter = _ContextProbeAdapter()
    runner.adapters = {Platform.TELEGRAM: adapter}
    event = _event()
    event.message_id = None
    runner._acquire_gateway_durable_turn_authority = AsyncMock(
        side_effect=AssertionError("missing origin entered durable authority")
    )
    runner._run_agent = AsyncMock(
        side_effect=AssertionError("agent entry preceded durable authority")
    )

    await runner._handle_message_with_agent(
        event,
        _source(),
        entry.session_key,
        1,
    )

    runner._acquire_gateway_durable_turn_authority.assert_not_awaited()
    _assert_no_downstream_effects(runner, adapter)


@pytest.mark.asyncio
async def test_outer_dispatch_restores_session_context_after_pre_agent_fault(
    monkeypatch,
    tmp_path,
):
    """Faults between context binding and agent entry cannot leak identity."""
    from tests.gateway.test_42039_duplicate_user_message import (
        _bootstrap,
        _event as _dispatch_event,
    )

    runner = _bootstrap(monkeypatch, tmp_path)
    tokens = [object()]
    runner._clear_session_env = MagicMock()

    async def _fault(event, *_args):
        event._gateway_session_env_tokens = tokens
        raise RuntimeError("pre-agent enrichment failed")

    runner._handle_message_with_agent = _fault

    with pytest.raises(RuntimeError, match="pre-agent enrichment failed"):
        await runner._handle_message(_dispatch_event())

    runner._clear_session_env.assert_called_once_with(tokens)


@pytest.mark.asyncio
async def test_durable_lease_release_can_retry_after_transient_db_failure(
    monkeypatch,
    tmp_path,
):
    runner = _runner(monkeypatch, tmp_path)
    event = _event()
    db = MagicMock()
    db.release_session_turn_lease.side_effect = [
        OSError("state db temporarily unavailable"),
        True,
    ]
    event._gateway_durable_turn_authority = {
        "db": db,
        "session_id": "session-exact",
        "holder": "holder-exact",
        "refresh_task": None,
        "released": False,
    }

    assert await runner._release_gateway_durable_turn_authority(event) is False
    assert event._gateway_durable_turn_authority["released"] is False
    assert await runner._release_gateway_durable_turn_authority(event) is True
    assert event._gateway_durable_turn_authority["released"] is True
    assert db.release_session_turn_lease.call_count == 2
