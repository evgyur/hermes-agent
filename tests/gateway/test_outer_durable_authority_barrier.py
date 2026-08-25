"""Gateway-level regressions for the durable turn-authority boundary."""

import asyncio
import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import InboundContextNote
from gateway.run import GatewayRunner
from agent.context_compressor import ContextCompressor
from hermes_state import (
    AsyncSessionDB,
    GatewayUserAuthorityWrite,
    SessionDB,
    SessionTurnLeaseLostError,
)
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


@pytest.mark.asyncio
async def test_retry_adopts_reused_authority_tail_before_short_approval_preprocessing(
    monkeypatch,
    tmp_path,
):
    """A marker-write retry must not present the current request twice."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    runner = _runner(monkeypatch, tmp_path)
    entry = runner.session_store.get_or_create_session.return_value
    entry.updated_at = entry.created_at
    history = [
        {
            "_row_id": 101,
            "role": "user",
            "content": "old watcher context",
            "_db_persisted": True,
        },
        {
            "_row_id": 202,
            "role": "assistant",
            "content": "Latest candidate commitment: deploy candidate 31e after approval.",
            "_db_persisted": True,
        },
        {
            "_row_id": 303,
            "role": "user",
            "content": "Го",
            "platform_message_id": "msg-42",
            "_db_persisted": True,
        },
    ]
    runner.session_store.load_transcript.return_value = history
    runner._mark_durable_active_turn = AsyncMock(return_value=True)
    authority = {
        "db": MagicMock(),
        "holder": "holder-exact",
        "ttl_seconds": 300.0,
    }
    runner._acquire_gateway_durable_turn_authority = AsyncMock(
        return_value=authority
    )
    runner._validate_and_seal_startup_resume = AsyncMock(return_value=True)
    runner._persist_gateway_triggering_user_row = AsyncMock(
        return_value=GatewayUserAuthorityWrite(row_id=303, inserted=False)
    )
    runner._enrich_gateway_triggering_user_row = AsyncMock(return_value=303)
    runner._run_agent = AsyncMock(
        return_value={
            "failed": False,
            "final_response": "accepted",
            "messages": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    event = _event()
    event.text = "Го"
    await runner._handle_message_with_agent(
        event,
        _source(),
        entry.session_key,
        1,
    )

    call = runner._run_agent.await_args.kwargs
    assert [row.get("_row_id") for row in call["history"]] == [101, 202]
    assert "Latest candidate commitment" in str(call["message"])
    assert str(call["message"]).count("Го") == 1
    assert runner._run_agent.await_count == 1
    assert call["precommitted_user_row_id"] == 303


@pytest.mark.asyncio
async def test_lease_loss_during_real_hygiene_fences_stale_holder_and_tail(
    monkeypatch,
    tmp_path,
):
    """A paused pre-agent hygiene owner cannot outlive an H2 takeover."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(db_path=tmp_path / "state.db")
    session_id = "real-hygiene-holder-race"
    db.create_session(session_id, source="telegram")
    for index in range(6):
        db.append_message(
            session_id,
            role="user" if index % 2 == 0 else "assistant",
            content=(f"history-{index}-" + ("x" * 500)),
        )

    runner = _runner(monkeypatch, tmp_path)
    entry = runner.session_store.get_or_create_session.return_value
    entry.session_id = session_id
    entry.updated_at = entry.created_at + timedelta(microseconds=1)
    history = db.get_messages_as_conversation(session_id, include_row_ids=True)
    runner.session_store.load_transcript.return_value = history
    runner._session_db = AsyncSessionDB(db)
    runner._mark_durable_active_turn = AsyncMock(return_value=True)
    runner._resolve_session_agent_runtime = MagicMock(
        return_value=("test/model", {"api_key": "fake"})
    )
    runner._run_agent = AsyncMock(
        side_effect=AssertionError("model ran after pre-agent lease loss")
    )
    adapter = _ContextProbeAdapter()
    runner.adapters = {Platform.TELEGRAM: adapter}

    import gateway.run as gateway_run

    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length_async",
        AsyncMock(return_value=100),
    )
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "compression": {
                "enabled": True,
                "hygiene_timeout_seconds": 30,
            }
        },
    )

    worker_started = threading.Event()
    release_worker = threading.Event()
    worker_done = threading.Event()
    observed = {}

    class PausedHygieneAgent:
        def __init__(self, **kwargs):
            self.model = kwargs.get("model")
            self.platform = kwargs.get("platform")
            self.session_id = kwargs["session_id"]
            self._session_db = kwargs["session_db"]
            self._cached_system_prompt = None
            self.context_compressor = ContextCompressor(
                model="test/model",
                quiet_mode=True,
            )
            self.compression_in_place = False
            self._last_compaction_in_place = False
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()

        def _compress_context(self, messages, *_args, **_kwargs):
            observed["holder"] = self.context_compressor._turn_lease_holder
            observed["ttl"] = self.context_compressor._turn_lease_ttl_seconds
            worker_started.set()
            assert release_worker.wait(timeout=5)
            try:
                self._session_db.archive_and_compact(
                    self.session_id,
                    [{"role": "assistant", "content": "stale H summary"}],
                    turn_lease_holder=observed["holder"],
                    turn_lease_ttl_seconds=observed["ttl"],
                )
            except Exception as exc:
                observed["archive_error"] = exc
            finally:
                worker_done.set()
            return messages, None

    monkeypatch.setitem(
        __import__("sys").modules,
        "run_agent",
        SimpleNamespace(AIAgent=PausedHygieneAgent),
    )

    event = _event()
    event.text = "Го"
    handler = asyncio.create_task(
        runner._handle_message_with_agent(
            event,
            _source(),
            entry.session_key,
            1,
        )
    )
    assert await asyncio.to_thread(worker_started.wait, 5)
    authority = event._gateway_durable_turn_authority
    holder_h = authority["holder"]
    assert observed["holder"] == holder_h

    db.release_session_turn_lease(session_id, holder_h)
    holder_h2 = "pid=2:gateway-turn=successor-H2:platform=telegram"
    assert db.acquire_session_turn_lease(
        session_id,
        holder_h2,
        ttl_seconds=300,
        wait_seconds=0.1,
    )
    sentinel_id = db.append_message(
        session_id,
        role="assistant",
        content="H2 sentinel \u0000 byte-exact tail",
        api_content="H2 api sentinel",
        platform_message_id="h2-sentinel",
        turn_lease_holder=holder_h2,
    )
    sentinel_before = tuple(
        db._conn.execute(
            "SELECT content, api_content, platform_message_id, active, compacted "
            "FROM messages WHERE id = ?",
            (sentinel_id,),
        ).fetchone()
    )

    GatewayRunner._stop_gateway_durable_turn_authority_owner(
        authority,
        "forced lease loss after H2 takeover",
    )
    with pytest.raises(asyncio.CancelledError):
        await handler
    release_worker.set()
    assert await asyncio.to_thread(worker_done.wait, 5)

    sentinel_after = tuple(
        db._conn.execute(
            "SELECT content, api_content, platform_message_id, active, compacted "
            "FROM messages WHERE id = ?",
            (sentinel_id,),
        ).fetchone()
    )
    assert isinstance(observed.get("archive_error"), SessionTurnLeaseLostError)
    assert sentinel_after == sentinel_before
    assert "stale H summary" not in [
        row[0]
        for row in db._conn.execute(
            "SELECT content FROM messages WHERE session_id = ? AND active = 1",
            (session_id,),
        ).fetchall()
    ]
    adapter.note_builder.assert_not_called()
    runner._run_agent.assert_not_awaited()
    assert not runner.hooks.emit.await_args_list
    db.release_session_turn_lease(session_id, holder_h2)
    db.close()


@pytest.mark.asyncio
async def test_lease_loss_cancels_pre_agent_handler_before_hooks_or_model():
    entered_hygiene = asyncio.Event()
    release_hygiene = asyncio.Event()
    downstream = MagicMock()

    async def _pre_agent_handler():
        entered_hygiene.set()
        await release_hygiene.wait()
        downstream("hook-or-model")

    handler = asyncio.create_task(_pre_agent_handler())
    await entered_hygiene.wait()
    authority = {
        "handler_task": handler,
        "agent": None,
        "lost": False,
    }

    GatewayRunner._stop_gateway_durable_turn_authority_owner(
        authority,
        "Session turn lease lost; stopping to protect the transcript.",
    )

    with pytest.raises(asyncio.CancelledError):
        await handler
    assert authority["lost"] is True
    downstream.assert_not_called()


@pytest.mark.asyncio
async def test_lease_loss_preserves_hard_agent_interrupt_after_assignment():
    agent = MagicMock()
    handler = asyncio.create_task(asyncio.Event().wait())
    authority = {
        "handler_task": handler,
        "agent": agent,
        "lost": False,
    }

    GatewayRunner._stop_gateway_durable_turn_authority_owner(
        authority,
        "lease lost after assignment",
    )

    agent.interrupt.assert_called_once_with(
        "lease lost after assignment",
        hard_cancel=True,
    )
    assert not handler.cancelled()
    handler.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handler
