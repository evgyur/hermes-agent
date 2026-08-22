import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway import run as gateway_run
from tools import async_delegation as ad
from tools import parent_task_barrier as barrier
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, SendResult
from gateway.session import SessionEntry, SessionSource, build_session_key


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ad._reset_for_tests()
    barrier.initialize_storage()
    yield
    ad._reset_for_tests()


def _seed_required_completion():
    now = time.time()
    delegation_id = "deleg-required"
    record = {
        "delegation_id": delegation_id,
        "goal": "required child",
        "context": None,
        "toolsets": None,
        "role": "leaf",
        "model": "m",
        "session_key": "agent:main:telegram:group:-100:4",
        "origin_ui_session_id": "",
        "origin_session_id": "",
        "parent_session_id": "parent-session",
        "is_batch": False,
        "dispatched_at": now,
        "status": "running",
    }
    assert ad._persist_dispatch(record)
    barrier_id = barrier.admit_required_child(
        origin_session=record["session_key"],
        parent_session_id=record["parent_session_id"],
        root_turn_id="root-turn",
        task_id=delegation_id,
    )
    event = {
        "type": "async_delegation",
        "delegation_id": delegation_id,
        "session_key": record["session_key"],
        "parent_session_id": record["parent_session_id"],
        "status": "completed",
        "completed_at": now + 1,
    }
    assert ad._persist_completion(event, {"summary": "evidence"})
    return barrier_id, event


def test_barrier_readbacks_never_enter_writer_transaction(monkeypatch):
    barrier_id, event = _seed_required_completion()

    def reject_writer_transaction():
        raise AssertionError("readback entered BEGIN IMMEDIATE writer path")

    monkeypatch.setattr(barrier, "_transaction", reject_writer_transaction)

    assert barrier.barrier_for_child(event["delegation_id"]) == barrier_id
    assert barrier.has_active_barrier(
        origin_session=event["session_key"],
        parent_session_id=event["parent_session_id"],
    )
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["barrier_id"] == barrier_id


def test_barrier_readbacks_tolerate_absent_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "not-initialized"))

    assert barrier.barrier_for_child("missing") is None
    assert barrier.has_active_barrier(origin_session="missing") is False
    assert barrier.barrier_snapshot("missing") is None


def test_barrier_read_open_error_propagates_fail_closed(monkeypatch):
    def broken_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(barrier.sqlite3, "connect", broken_connect)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        barrier.has_active_barrier(origin_session="must-defer")


@pytest.mark.asyncio
async def test_barrier_initializer_failure_prevents_gateway_intake():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._create_adapter = MagicMock()
    with patch(
        "tools.parent_task_barrier.initialize_storage",
        side_effect=sqlite3.OperationalError("disk I/O error"),
    ):
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            await runner.start()
    runner._create_adapter.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_consumes_bound_child_without_direct_reinjection():
    from gateway.run import GatewayRunner

    _barrier_id, event = _seed_required_completion()
    runner = object.__new__(GatewayRunner)

    consumed = await runner._consume_parent_barrier_child(event)

    assert consumed is True
    assert ad.completion_delivery_disposition(event["delegation_id"]) == "delivered"


@pytest.mark.asyncio
async def test_gateway_injects_only_trusted_aggregate_continuation():
    from gateway.run import GatewayRunner

    barrier_id, _event = _seed_required_completion()
    policy = barrier.finalization_policy(
        parent_session_id="parent-session", root_turn_id="root-turn"
    )
    assert policy["action"] == "withhold"
    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert isinstance(claim, barrier.TrustedParentTaskContinuation)

    runner = object.__new__(GatewayRunner)
    runner._inject_watch_notification = AsyncMock(return_value=True)

    accepted = await runner._deliver_parent_task_continuation(claim)

    assert accepted is True
    runner._inject_watch_notification.assert_awaited_once_with(
        claim["synthetic_message"], claim
    )
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "resuming"


@pytest.mark.asyncio
async def test_failed_aggregate_injection_releases_durable_claim():
    from gateway.run import GatewayRunner

    barrier_id, _event = _seed_required_completion()
    barrier.finalization_policy(
        parent_session_id="parent-session", root_turn_id="root-turn"
    )
    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert claim is not None

    runner = object.__new__(GatewayRunner)
    runner._inject_watch_notification = AsyncMock(return_value=False)
    assert await runner._deliver_parent_task_continuation(claim) is False

    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "ready"
    assert snapshot["barrier"]["continuation_status"] == "pending"


@pytest.mark.asyncio
async def test_ordinary_user_event_is_parked_behind_active_barrier():
    from gateway.run import GatewayRunner

    barrier.admit_required_child(
        origin_session="agent:main:telegram:dm:1",
        parent_session_id="parent",
        root_turn_id="root",
        task_id="child",
    )
    runner = object.__new__(GatewayRunner)
    queued = []
    runner._queue_or_replace_pending_event = (
        lambda session_key, event: queued.append((session_key, event))
    )
    event = SimpleNamespace(text="steer me after the child")

    parked = await runner._park_user_event_for_parent_barrier(
        event=event,
        session_key="agent:main:telegram:dm:1",
        parent_session_id="parent",
    )
    assert parked is True
    assert queued == [("agent:main:telegram:dm:1", event)]

    barrier.cancel_session_barriers(origin_session="agent:main:telegram:dm:1")
    assert not await runner._park_user_event_for_parent_barrier(
        event=event,
        session_key="agent:main:telegram:dm:1",
        parent_session_id="parent",
    )


@pytest.mark.asyncio
async def test_platform_exception_abandons_only_trusted_parent_delivery(monkeypatch):
    import gateway.delivery_ledger as ledger
    from gateway.platforms.base import _abort_parent_task_delivery

    calls = []
    monkeypatch.setattr(
        ledger,
        "mark_abandoned",
        lambda obligation_id, error="": calls.append(
            ("abandoned", obligation_id, error)
        ),
    )
    monkeypatch.setattr(
        barrier,
        "release_accepted_continuation",
        lambda barrier_id, claim: calls.append(("released", barrier_id, claim))
        or True,
    )
    delivery = barrier.TrustedParentTaskDelivery(
        "final",
        barrier_id="barrier",
        continuation_claim="claim",
        result={"final_response": "final"},
    )

    assert await _abort_parent_task_delivery(
        delivery, "oid", error="platform exception"
    )
    assert calls == [
        ("abandoned", "oid", "platform exception"),
        ("released", "barrier", "claim"),
    ]
    assert not await _abort_parent_task_delivery(
        "user-authored lookalike", "oid-2", error="ignored"
    )
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_platform_ack_settles_trusted_parent_delivery():
    from gateway.delivery_ledger import (
        mark_attempting,
        mark_delivered,
        record_obligation,
    )
    from gateway.platforms.base import (
        _prepare_parent_task_delivery,
        _settle_parent_task_delivery,
    )

    barrier_id = barrier.admit_required_child(
        origin_session="origin",
        parent_session_id="parent",
        root_turn_id="root",
        task_id="child",
    )
    barrier.finalization_policy(parent_session_id="parent", root_turn_id="root")
    conn = sqlite3.connect(barrier._db_path())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS async_delegations(
               delegation_id TEXT PRIMARY KEY, result_json TEXT
           )"""
    )
    conn.execute(
        "INSERT OR REPLACE INTO async_delegations VALUES ('child', '{\"summary\":\"done\"}')"
    )
    conn.commit()
    conn.close()
    barrier.record_child_terminal(task_id="child", state="completed", result={})
    claim = barrier.claim_next_ready_continuation(owner="gateway")
    assert claim is not None
    assert barrier.accept_continuation(
        barrier_id,
        claim["continuation_claim"],
        accepted_turn_id="turn",
        owner_pid=1,
    )
    delivery = barrier.TrustedParentTaskDelivery(
        "final",
        barrier_id=barrier_id,
        continuation_claim=claim["continuation_claim"],
        result={"final_response": "final"},
    )
    empty_delivery = barrier.TrustedParentTaskDelivery(
        "",
        barrier_id=barrier_id,
        continuation_claim=claim["continuation_claim"],
        result={},
    )
    assert not await _settle_parent_task_delivery(
        empty_delivery, delivered=True, obligation_id=""
    )
    unbound_snapshot = barrier.barrier_snapshot(barrier_id)
    assert unbound_snapshot is not None
    assert unbound_snapshot["barrier"]["state"] == "continuing"
    record_obligation(
        obligation_id="oid",
        session_key="origin",
        platform="telegram",
        chat_id="1",
        thread_id=None,
        content="final",
    )
    mark_attempting("oid")
    assert await _prepare_parent_task_delivery(delivery, "oid")
    mark_delivered("oid")
    assert await _settle_parent_task_delivery(
        delivery, delivered=True, obligation_id="oid"
    )
    snapshot = barrier.barrier_snapshot(barrier_id)
    assert snapshot is not None
    assert snapshot["barrier"]["state"] == "closed"


def test_streaming_is_buffered_before_required_child_admission():
    from gateway.run import (
        _parent_task_stream_allowed,
        _parent_task_turn_buffers_streaming,
    )

    capable = SimpleNamespace(valid_tool_names={"delegate_task", "terminal"})
    incapable = SimpleNamespace(valid_tool_names={"terminal"})
    assert _parent_task_turn_buffers_streaming(capable)
    assert not _parent_task_turn_buffers_streaming(incapable)
    assert _parent_task_turn_buffers_streaming(incapable, {"barrier_id": "b"})

    agent = SimpleNamespace(_parent_task_barrier_stream_suppressed=False)
    assert _parent_task_stream_allowed(agent, run_still_current=True)
    agent._parent_task_barrier_stream_suppressed = True
    assert not _parent_task_stream_allowed(agent, run_still_current=True)
    assert not _parent_task_stream_allowed(agent, run_still_current=False)


def test_goal_outcome_defers_standing_goal_judge_for_provisional_turn():
    from gateway.run import _goal_turn_outcomes

    outcomes = _goal_turn_outcomes(
        {
            "final_response": "provisional",
            "suppress_delivery": True,
            "defer_goal_evaluation": True,
        }
    )

    assert outcomes[0]["delivery_suppressed"] is True
    assert outcomes[0]["defer_goal_evaluation"] is True


def test_gateway_runtime_preserves_parent_barrier_delivery_controls():
    from gateway.run import _merge_gateway_agent_delivery_controls

    provisional = {
        "final_response": "provisional with /tmp/PLAN.md",
        "suppress_delivery": True,
        "delivery_suppressed": True,
        "defer_goal_evaluation": True,
        "parent_task_barrier_id": "barrier-1",
        "turn_exit_reason": "text_response(finish_reason=stop)",
        "completed": False,
    }

    payload = _merge_gateway_agent_delivery_controls(
        {"final_response": provisional["final_response"]}, provisional
    )

    assert payload["suppress_delivery"] is True
    assert payload["delivery_suppressed"] is True
    assert payload["defer_goal_evaluation"] is True
    assert payload["parent_task_barrier_id"] == "barrier-1"
    assert payload["turn_exit_reason"] == "text_response(finish_reason=stop)"
    assert payload["completed"] is False


def test_gateway_runtime_does_not_invent_delivery_controls_for_normal_turn():
    from gateway.run import _merge_gateway_agent_delivery_controls

    payload = _merge_gateway_agent_delivery_controls(
        {"final_response": "ordinary final"},
        {"final_response": "ordinary final", "completed": True},
    )

    assert payload["final_response"] == "ordinary final"
    assert payload["completed"] is True
    assert payload["delivery_control"]["disposition"] == "SEND"
    assert "suppress_delivery" not in payload
    assert "delivery_suppressed" not in payload
    assert "response_already_delivered" not in payload
    assert "defer_goal_evaluation" not in payload


def test_ordinary_normalization_preserves_queued_delivery_fallback():
    payload = gateway_run._merge_gateway_agent_delivery_controls(
        {"final_response": "first final"},
        {
            "final_response": "first final",
            "turn_exit_reason": "completed",
            "completed": True,
        },
    )

    outcome = gateway_run._goal_turn_outcome(
        payload, response_already_delivered=True
    )

    assert outcome is not None
    assert outcome["response_already_delivered"] is True


def _image_generate_result(disposition: str, attachment: Path) -> dict:
    return {
        "delivery_control": {
            "disposition": disposition,
            "barrier_id": "nested-barrier" if disposition == "DEFER" else "",
            "defer_goal_evaluation": disposition == "DEFER",
            "outcome_id": "outcome-image",
        },
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "image-call",
                        "function": {"name": "image_generate", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "image-call",
                "content": json.dumps(
                    {"success": True, "image": str(attachment)}
                ),
            },
        ],
    }


@pytest.mark.parametrize(
    ("disposition", "final_response"),
    [("DEFER", "provisional final"), ("DEFER", ""), ("ALREADY_DELIVERED", "done")],
)
def test_delivery_gate_precedes_automatic_attachment_enrichment(
    tmp_path, disposition, final_response
):
    attachment = tmp_path / "provisional.png"
    attachment.write_bytes(b"not-deliverable-yet")

    response, control = gateway_run._prepare_gateway_delivery_text(
        _image_generate_result(disposition, attachment), final_response
    )

    assert control.disposition.value == disposition
    assert response == final_response
    assert "MEDIA:" not in response


def test_send_enriches_terminal_artifact_exactly_once_and_leaves_plain_text_unchanged(
    tmp_path,
):
    attachment = tmp_path / "terminal.png"
    attachment.write_bytes(b"terminal-artifact")
    result = _image_generate_result("SEND", attachment)

    enriched, control = gateway_run._prepare_gateway_delivery_text(
        result, "terminal final"
    )
    replayed, _ = gateway_run._prepare_gateway_delivery_text(result, enriched)
    plain, _ = gateway_run._prepare_gateway_delivery_text(
        {"final_response": "ordinary", "messages": []}, "ordinary"
    )

    assert control.disposition.value == "SEND"
    assert enriched.count(f"MEDIA:{attachment}") == 1
    assert replayed == enriched
    assert plain == "ordinary"


def test_contradictory_typed_and_legacy_delivery_controls_fail_closed(caplog):
    from agent.turn_result import normalize_delivery_control

    control = normalize_delivery_control(
        {
            "delivery_control": {
                "disposition": "SEND",
                "barrier_id": "",
                "defer_goal_evaluation": False,
                "outcome_id": "outcome-1",
            },
            "suppress_delivery": True,
        },
        logger=__import__("logging").getLogger("delivery-control-test"),
    )

    assert control.disposition.value == "DEFER"
    assert control.defer_goal_evaluation is True
    assert "contradictory typed/legacy delivery controls" in caplog.text


@pytest.mark.parametrize(
    "legacy_fields",
    [
        {"defer_goal_evaluation": True},
        {"delivery_suppressed": True},
    ],
)
def test_any_legacy_defer_signal_conflicting_with_typed_send_fails_closed(
    caplog, legacy_fields
):
    from agent.turn_result import normalize_delivery_control

    control = normalize_delivery_control(
        {
            "delivery_control": {
                "disposition": "SEND",
                "barrier_id": "",
                "defer_goal_evaluation": False,
                "outcome_id": "",
            },
            **legacy_fields,
        },
        logger=__import__("logging").getLogger("delivery-control-test"),
    )

    assert control.disposition.value == "DEFER"
    assert control.defer_goal_evaluation is True
    assert "contradictory typed/legacy delivery controls" in caplog.text


def test_matching_legacy_identity_is_preserved_when_typed_control_omits_it():
    from agent.turn_result import normalize_delivery_control

    control = normalize_delivery_control(
        {
            "delivery_control": {
                "disposition": "DEFER",
                "barrier_id": "",
                "defer_goal_evaluation": True,
                "outcome_id": "",
            },
            "suppress_delivery": True,
            "parent_task_barrier_id": "barrier-legacy",
            "outcome_id": "outcome-legacy",
        }
    )

    assert control.disposition.value == "DEFER"
    assert control.barrier_id == "barrier-legacy"
    assert control.outcome_id == "outcome-legacy"


class _CaptureParentBarrierAdapter(BasePlatformAdapter):
    """Exercise the real post-handler delivery pipeline without network I/O."""

    def __init__(self):
        super().__init__(
            PlatformConfig(enabled=True, token="fake-token"), Platform.TELEGRAM
        )
        self.sent = []
        self.documents = []

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.sent.append(
            {
                "chat_id": chat_id,
                "content": content,
                "reply_to": reply_to,
                "metadata": metadata,
            }
        )
        return SendResult(success=True, message_id="unexpected-send")

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        return None

    async def send_document(
        self,
        chat_id,
        file_path,
        caption=None,
        file_name=None,
        reply_to=None,
        metadata=None,
        human_delay=0.0,
        **kwargs,
    ) -> SendResult:
        self.documents.append(
            {
                "chat_id": chat_id,
                "file_path": file_path,
                "sha256": hashlib.sha256(Path(file_path).read_bytes()).hexdigest(),
            }
        )
        return SendResult(success=True, message_id=f"doc-{len(self.documents)}")

    async def _keep_typing(
        self, chat_id: str, interval: float = 2, metadata=None, stop_event=None
    ) -> None:
        if stop_event is not None:
            await stop_event.wait()

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "legacy-defer-media",
        "legacy-defer-local",
        "legacy-defer-empty-metadata",
        "typed-already",
        "typed-send",
    ],
)
async def test_delivery_disposition_controls_outer_platform_enqueue(
    monkeypatch, tmp_path, case
):
    """Only SEND may enqueue; withheld text and local files never escape."""
    import gateway.run as gateway_run

    attachment = tmp_path / "PLAN.md"
    attachment.write_text("provisional plan", encoding="utf-8")
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        user_id="12345",
    )
    event = MessageEvent(text="continue", source=source, message_id="msg-42")
    session_key = build_session_key(source)

    adapter = _CaptureParentBarrierAdapter()
    runner = gateway_run.GatewayRunner(GatewayConfig())
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._is_user_authorized = lambda source: True
    runner._set_session_env = lambda context: []
    runner._handle_active_session_busy_message = AsyncMock(return_value=False)
    runner._session_db = MagicMock()
    runner._recover_telegram_topic_thread_id = lambda source: None
    runner._cache_session_source = lambda session_key, source: None
    runner._is_session_run_current = lambda session_key, generation: True
    runner._reply_anchor_for_event = lambda event: None
    runner._get_guild_id = lambda event: None
    runner._should_send_voice_reply = lambda *_a, **_kw: False
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()

    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key=session_key,
        session_id="sess-parent-barrier",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="group",
    )
    runner.session_store.load_transcript.return_value = []
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()

    final_response = {
        "legacy-defer-media": f"provisional plan\n\nMEDIA:{attachment}",
        "legacy-defer-local": f"provisional plan at {attachment}",
        "legacy-defer-empty-metadata": "",
        "typed-already": f"provisional plan\n\nMEDIA:{attachment}",
        "typed-send": "ordinary final",
    }[case]
    legacy_defer = {
        "suppress_delivery": True,
        "delivery_suppressed": True,
        "defer_goal_evaluation": True,
        "parent_task_barrier_id": "barrier-1",
    }
    delivery_fields = {
        "legacy-defer-media": legacy_defer,
        "legacy-defer-local": legacy_defer,
        "legacy-defer-empty-metadata": legacy_defer,
        "typed-already": {
            "delivery_control": {
                "disposition": "ALREADY_DELIVERED",
                "barrier_id": "",
                "defer_goal_evaluation": False,
                "outcome_id": "outcome-1",
            }
        },
        "typed-send": {
            "delivery_control": {
                "disposition": "SEND",
                "barrier_id": "",
                "defer_goal_evaluation": False,
                "outcome_id": "outcome-2",
            }
        },
    }[case]
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": final_response,
            "messages": (
                [
                    {"role": "user", "content": "continue"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "img-1",
                                "function": {
                                    "name": "image_generate",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "img-1",
                        "content": (
                            '{"success": true, "image": "'
                            + str(attachment)
                            + '"}'
                        ),
                    },
                ]
                if case == "legacy-defer-empty-metadata"
                else [
                    {"role": "user", "content": "continue"},
                    {"role": "assistant", "content": final_response},
                ]
            ),
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
            "api_calls": 1,
            "failed": False,
            "completed": case == "typed-send",
            **delivery_fields,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setenv(gateway_run._home_target_env_var("telegram"), "-1001")
    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"}
    )
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100_000,
    )

    adapter.set_message_handler(runner._handle_message)

    await adapter._process_message_background(event, session_key)

    if case == "typed-send":
        assert [item["content"] for item in adapter.sent] == ["ordinary final"]
    else:
        assert adapter.sent == []
        assert adapter.documents == []


@pytest.mark.asyncio
async def test_terminal_parent_callback_delivers_text_and_artifact_once_on_replay(
    tmp_path,
):
    artifact = tmp_path / "review.txt"
    artifact.write_text("reviewed artifact", encoding="utf-8")
    expected_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()

    barrier_id = barrier.admit_required_child(
        origin_session="agent:main:telegram:group:-1001",
        parent_session_id="parent-session",
        root_turn_id="root-turn",
        task_id="child",
    )
    barrier.finalization_policy(
        parent_session_id="parent-session", root_turn_id="root-turn"
    )
    barrier.record_child_terminal(
        task_id="child", state="completed", result={"summary": "reviewed"}
    )
    conn = sqlite3.connect(barrier._db_path())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS async_delegations(
               delegation_id TEXT PRIMARY KEY, result_json TEXT
           )"""
    )
    conn.execute(
        "INSERT OR REPLACE INTO async_delegations VALUES (?, ?)",
        ("child", '{"summary":"reviewed"}'),
    )
    conn.commit()
    conn.close()
    claim = barrier.claim_next_ready_continuation(owner="gateway-test")
    assert claim is not None
    assert barrier.accept_continuation(
        barrier_id,
        claim["continuation_claim"],
        accepted_turn_id="turn-1",
        owner_pid=1,
    )

    delivery = barrier.TrustedParentTaskDelivery(
        f"Reviewed final.\n\nMEDIA:{artifact}",
        barrier_id=barrier_id,
        continuation_claim=claim["continuation_claim"],
        result={
            "final_response": f"Reviewed final.\n\nMEDIA:{artifact}",
            "delivery_control": {
                "disposition": "SEND",
                "barrier_id": barrier_id,
                "defer_goal_evaluation": False,
                "outcome_id": "outcome-reviewed",
            },
        },
    )

    adapter = _CaptureParentBarrierAdapter()

    async def _handler(_event):
        return delivery

    adapter.set_message_handler(_handler)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        user_id="12345",
    )
    event = MessageEvent(text="callback", source=source, message_id="callback-1")
    session_key = build_session_key(source)

    await adapter._process_message_background(event, session_key)
    await adapter._process_message_background(event, session_key)

    assert [item["content"] for item in adapter.sent] == ["Reviewed final."]
    assert [item["sha256"] for item in adapter.documents] == [expected_hash]
