from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from tools import async_delegation as ad
from tools.process_registry import process_registry


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    from tools import parent_task_barrier as ptb

    monkeypatch.setattr(ad, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(ptb, "get_hermes_home", lambda: tmp_path)
    ad._reset_for_tests()
    while not process_registry.completion_queue.empty():
        process_registry.completion_queue.get_nowait()
    yield tmp_path
    ad._reset_for_tests()
    while not process_registry.completion_queue.empty():
        process_registry.completion_queue.get_nowait()


def _dispatch_blocked_batch(
    gate: threading.Event,
    *,
    delegation_id: str = "deleg_contract_v2",
    **overrides,
):
    def runner():
        gate.wait(timeout=5)
        return {
            "results": [
                {"task_index": 0, "status": "completed", "summary": "one"},
                {"task_index": 1, "status": "completed", "summary": "two"},
            ]
        }

    kwargs = dict(
        goals=["complete first child", "complete second child"],
        context="durable context",
        toolsets=None,
        role="leaf",
        model="test-model",
        session_key="telegram:origin",
        origin_ui_session_id="ui-parent",
        origin_session_id="origin-session",
        parent_session_id="parent-session",
        root_turn_id="root-turn",
        existing_parent_barrier_id="",
        runner=runner,
        max_async_children=3,
        delegation_id=delegation_id,
        child_session_ids=["child-0", "child-1"],
        child_capability_names=[["read_file"], ["read_file", "web_search"]],
        task_specs=[
            {"goal": "complete first child", "context": "a", "role": "leaf"},
            {"goal": "complete second child", "context": "b", "role": "leaf"},
        ],
        output_schemas=[None, {"type": "object"}],
        output_schema_fingerprints=[
            "",
            hashlib.sha256(b'{"type":"object"}').hexdigest(),
        ],
    )
    kwargs.update(overrides)
    return ad.dispatch_async_delegation_batch(**kwargs)


def _wait_for_state(delegation_id: str, state: str, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with ad._transaction() as conn:
            row = conn.execute(
                "SELECT state FROM async_delegations WHERE delegation_id=?",
                (delegation_id,),
            ).fetchone()
        if row and row[0] == state:
            return
        time.sleep(0.02)
    raise AssertionError(f"delegation {delegation_id} did not reach {state}")


def test_required_delegation_is_restartable_by_default_and_contract_is_insert_only():
    gate = threading.Event()
    result = _dispatch_blocked_batch(gate)
    assert result["status"] == "dispatched"

    with ad._transaction() as conn:
        parent = conn.execute(
            """SELECT restart_policy, restart_budget, contract_version,
                      task_fingerprint, execution_generation, child_count,
                      output_schema_fingerprints_json
               FROM async_delegations WHERE delegation_id=?""",
            (result["delegation_id"],),
        ).fetchone()
        children = conn.execute(
            """SELECT child_index, child_session_id, capability_fingerprint,
                      output_schema_fingerprint, state
               FROM async_delegation_children WHERE delegation_id=?
               ORDER BY child_index""",
            (result["delegation_id"],),
        ).fetchall()

    assert parent[:3] == ("gateway_owned_v1", 3, 2)
    assert len(parent[3]) == 64
    assert parent[4:6] == (0, 2)
    assert json.loads(parent[6])[1] == hashlib.sha256(b'{"type":"object"}').hexdigest()
    assert [row[1] for row in children] == ["child-0", "child-1"]
    assert all(len(row[2]) == 64 for row in children)
    assert children[1][3] == hashlib.sha256(b'{"type":"object"}').hexdigest()
    assert [row[4] for row in children] == ["running", "running"]

    duplicate = _dispatch_blocked_batch(gate)
    assert duplicate["status"] == "rejected"

    gate.set()
    _wait_for_state(result["delegation_id"], "completed")


def test_child_result_is_durable_before_parent_aggregate_completion():
    first_committed = threading.Event()
    finish = threading.Event()

    def runner():
        assert ad.commit_child_terminal(
            0,
            {"task_index": 0, "status": "completed", "summary": "durable first"},
        )
        first_committed.set()
        finish.wait(timeout=5)
        return {
            "results": [
                {"task_index": 0, "status": "completed", "summary": "durable first"},
                {"task_index": 1, "status": "completed", "summary": "second"},
            ]
        }

    gate = threading.Event()
    result = _dispatch_blocked_batch(gate, runner=runner, delegation_id="deleg_child_commit")
    assert result["status"] == "dispatched"
    assert first_committed.wait(timeout=3)

    with ad._transaction() as conn:
        parent_state = conn.execute(
            "SELECT state FROM async_delegations WHERE delegation_id=?",
            (result["delegation_id"],),
        ).fetchone()[0]
        child = conn.execute(
            """SELECT state, result_json FROM async_delegation_children
               WHERE delegation_id=? AND child_index=0""",
            (result["delegation_id"],),
        ).fetchone()
    assert parent_state == "running"
    assert child[0] == "completed"
    assert json.loads(child[1])["summary"] == "durable first"

    finish.set()
    _wait_for_state(result["delegation_id"], "completed")


def test_reclaim_preserves_terminal_child_and_stale_generation_cannot_commit():
    gate = threading.Event()
    result = _dispatch_blocked_batch(gate, delegation_id="deleg_generation")
    delegation_id = result["delegation_id"]
    assert ad.commit_child_terminal(
        0,
        {"task_index": 0, "status": "completed", "summary": "keep me"},
        delegation_id=delegation_id,
        execution_generation=0,
    )
    assert ad.defer_restartable_interruption(delegation_id, "gateway_drain")
    with ad._transaction() as conn:
        nonce = conn.execute(
            "SELECT restart_nonce FROM async_delegations WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()[0]
    claim = ad.claim_restartable_delegation(
        delegation_id,
        owner_pid=1234,
        owner_started_at=10,
        expected_session_key="telegram:origin",
        restart_nonce=nonce,
    )
    assert claim is not None
    assert claim["execution_generation"] == 1
    assert claim["children"][0]["state"] == "completed"
    assert claim["children"][0]["result"]["summary"] == "keep me"
    assert claim["children"][1]["state"] == "restarting"
    assert not ad.commit_child_terminal(
        1,
        {"task_index": 1, "status": "completed", "summary": "stale"},
        delegation_id=delegation_id,
        execution_generation=0,
    )
    gate.set()


def test_per_row_restart_budget_terminalizes_exactly_once():
    gate = threading.Event()
    result = _dispatch_blocked_batch(gate, delegation_id="deleg_budget")
    delegation_id = result["delegation_id"]
    with ad._transaction() as conn:
        conn.execute(
            "UPDATE async_delegations SET restart_budget=1 WHERE delegation_id=?",
            (delegation_id,),
        )
    assert ad.defer_restartable_interruption(delegation_id, "gateway_drain")
    with ad._transaction() as conn:
        nonce = conn.execute(
            "SELECT restart_nonce FROM async_delegations WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()[0]
    assert ad.claim_restartable_delegation(
        delegation_id,
        owner_pid=222,
        owner_started_at=20,
        expected_session_key="telegram:origin",
        restart_nonce=nonce,
    ) is not None
    assert ad.release_restart_claim(delegation_id, "dead_owner") is False
    assert ad.finalize_exhausted_restarts() == 1
    assert ad.finalize_exhausted_restarts() == 0
    gate.set()


def test_resume_marks_only_ambiguous_child_unknown_and_reuses_terminal(monkeypatch):
    from tools import delegate_tool as dt

    task = {
        "goal": "2 parallel subagents",
        "goals": ["already finished", "ambiguous effect"],
        "tasks": [
            {"goal": "already finished", "role": "leaf"},
            {"goal": "ambiguous effect", "role": "leaf"},
        ],
        "output_schemas": [None, None],
        "context": None,
        "toolsets": None,
        "role": "leaf",
        "model": "m",
        "is_batch": True,
    }
    task_fingerprint = hashlib.sha256(
        json.dumps(task, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    claim = {
        "delegation_id": "deleg_mixed",
        "task": task,
        "task_fingerprint": task_fingerprint,
        "contract_version": 2,
        "execution_generation": 1,
        "child_session_ids": ["child-0", "child-1"],
        "child_capability_names": [["read_file"], ["terminal"]],
        "children": [
            {
                "child_index": 0,
                "state": "completed",
                "result": {"task_index": 0, "status": "completed", "summary": "done"},
                "output_schema_fingerprint": "",
            },
            {
                "child_index": 1,
                "state": "restarting",
                "result": {},
                "output_schema_fingerprint": "",
            },
        ],
    }

    class SessionDB:
        def get_messages_as_conversation(self, session_id):
            assert session_id == "child-1"
            return [
                {"role": "assistant", "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}]},
            ]

    parent = SimpleNamespace(_session_db=SimpleNamespace(_db=SessionDB()))
    committed = []
    captured = {}

    def fake_commit(index, result, **kwargs):
        committed.append((index, result, kwargs))
        return True

    def fake_delegate_task(**kwargs):
        captured.update(dt._HOST_RESTART_CONTEXT.get())
        return json.dumps({"status": "dispatched", "delegation_id": "deleg_mixed"})

    monkeypatch.setattr(ad, "commit_child_terminal", fake_commit)
    monkeypatch.setattr(dt, "delegate_task", fake_delegate_task)
    assert dt.resume_async_delegation(claim, parent) == "dispatched"
    assert committed[0][0] == 1
    assert committed[0][1]["status"] == "blocked_unknown_effect"
    assert {item["task_index"] for item in captured["completed_results"]} == {0, 1}
    assert captured["transcripts"] == {}


def test_resume_fails_closed_on_output_schema_fingerprint_mismatch(monkeypatch):
    from tools import delegate_tool as dt

    schema = {"type": "object"}
    task = {
        "goals": ["schema-bound child"],
        "tasks": [{"goal": "schema-bound child", "role": "leaf"}],
        "output_schemas": [schema],
        "role": "leaf",
    }
    claim = {
        "delegation_id": "deleg_schema_mismatch",
        "task": task,
        "task_fingerprint": hashlib.sha256(
            json.dumps(task, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "contract_version": 2,
        "child_session_ids": ["child-0"],
        "child_capability_names": [["read_file"]],
        "children": [{
            "child_index": 0,
            "state": "restarting",
            "result": {},
            "output_schema_fingerprint": "tampered",
        }],
    }
    assert dt.resume_async_delegation(claim, SimpleNamespace()) == "failed"


def test_side_effect_recovery_requires_a_durable_known_success():
    from tools.delegate_tool import _restart_history_has_unknown_side_effect

    def history(content, *, disposition=None, tool_name="terminal"):
        result = {"role": "tool", "tool_call_id": "call-1", "content": content}
        if disposition is not None:
            result["effect_disposition"] = disposition
        return [
            {
                "role": "assistant",
                "tool_calls": [{
                    "id": "call-1",
                    "function": {"name": tool_name, "arguments": "{}"},
                }],
            },
            result,
        ]

    assert _restart_history_has_unknown_side_effect(
        history('{"output":"late","exit_code":0}', disposition="unknown")
    )
    assert _restart_history_has_unknown_side_effect(history(""))
    assert _restart_history_has_unknown_side_effect(
        history('{"error":"remote timeout"}')
    )
    assert not _restart_history_has_unknown_side_effect(
        history('{"output":"ok","exit_code":0}')
    )
    assert not _restart_history_has_unknown_side_effect(
        history("blocked before execution", disposition="none")
    )
    assert _restart_history_has_unknown_side_effect(
        history('{"success":true}', tool_name="write_file")
    )
    assert not _restart_history_has_unknown_side_effect(
        history('{"verified":true,"bytes_written":12}', tool_name="write_file")
    )


def test_stale_generation_cannot_mark_current_runner_returned():
    gate = threading.Event()
    result = _dispatch_blocked_batch(
        gate, delegation_id="deleg_stale_generation"
    )
    delegation_id = result["delegation_id"]
    assert result["status"] == "dispatched"
    assert ad.defer_restartable_interruption(delegation_id, "gateway_drain")
    with ad._transaction() as conn:
        origin, nonce = conn.execute(
            "SELECT origin_session, restart_nonce FROM async_delegations WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()
    claim = ad.claim_restartable_delegation(
        delegation_id,
        owner_pid=4321,
        owner_started_at=8765,
        expected_session_key=origin,
        restart_nonce=nonce,
    )
    assert claim and claim["execution_generation"] == 1
    with ad._transaction() as conn:
        conn.execute(
            "UPDATE async_delegations SET state='running', runner_returned=0 WHERE delegation_id=?",
            (delegation_id,),
        )
    assert not ad._mark_runner_returned(delegation_id, 0)
    with ad._transaction() as conn:
        row = conn.execute(
            "SELECT runner_returned FROM async_delegations WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()
    assert row == (0,)
    gate.set()


def test_mixed_unknown_effect_batch_terminalizes_parent_as_error():
    def runner():
        return {
            "results": [
                {"task_index": 0, "status": "completed", "summary": "safe"},
                {
                    "task_index": 1,
                    "status": "blocked_unknown_effect",
                    "summary": None,
                    "error": "unknown effect",
                },
            ]
        }

    result = ad.dispatch_async_delegation_batch(
        goals=["safe child", "unknown child"],
        context=None,
        toolsets=None,
        role="leaf",
        model="m",
        session_key="origin",
        parent_session_id="parent",
        root_turn_id="",
        runner=runner,
        max_async_children=3,
        delegation_id="deleg_mixed_terminal",
        child_session_ids=["mixed-0", "mixed-1"],
        child_capability_names=[["read_file"], ["terminal"]],
        task_specs=[{"goal": "safe child"}, {"goal": "unknown child"}],
        output_schemas=[None, None],
        output_schema_fingerprints=["", ""],
        restart_policy="gateway_owned_v1",
    )
    assert result["status"] == "dispatched"
    _wait_for_state(result["delegation_id"], "error")
    with ad._transaction() as conn:
        row = conn.execute(
            "SELECT state, result_json FROM async_delegations WHERE delegation_id=?",
            (result["delegation_id"],),
        ).fetchone()
    assert row[0] == "error"
    payload = json.loads(row[1])
    assert {item["status"] for item in payload["results"]} == {
        "completed", "blocked_unknown_effect"
    }
