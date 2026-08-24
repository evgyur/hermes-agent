from __future__ import annotations

import hashlib
import json
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
    yield
    ad._reset_for_tests()


def _dispatch(gate, *, delegation_id="deleg-native", runner=None):
    def default_runner():
        gate.wait(timeout=5)
        return {
            "results": [
                {"task_index": 0, "status": "completed", "summary": "one"},
                {"task_index": 1, "status": "completed", "summary": "two"},
            ]
        }

    return ad.dispatch_async_delegation_batch(
        goals=["complete first child", "complete second child"],
        context="durable context",
        toolsets=None,
        role="leaf",
        model="test-model",
        session_key="telegram:origin",
        parent_session_id="parent-session",
        root_turn_id="root-turn",
        runner=runner or default_runner,
        delegation_id=delegation_id,
        child_session_ids=["child-0", "child-1"],
        child_capability_names=[["read_file"], ["terminal"]],
        task_specs=[
            {"goal": "complete first child", "context": "a", "role": "leaf"},
            {"goal": "complete second child", "context": "b", "role": "leaf"},
        ],
        output_schemas=[None, {"type": "object"}],
        output_schema_fingerprints=[
            "",
            hashlib.sha256(b'{"type":"object"}').hexdigest(),
        ],
        restart_policy="gateway_owned_v1",
    )


def _wait_state(delegation_id, expected, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with ad._transaction() as conn:
            row = conn.execute(
                "SELECT state FROM async_delegations WHERE delegation_id=?",
                (delegation_id,),
            ).fetchone()
        if row and row[0] == expected:
            return
        time.sleep(0.02)
    raise AssertionError(f"{delegation_id} did not reach {expected}")


def test_contract_is_insert_only_and_child_results_precede_parent_completion():
    first = threading.Event()
    finish = threading.Event()

    def runner():
        assert ad.commit_child_terminal(
            0, {"task_index": 0, "status": "completed", "summary": "durable"}
        )
        first.set()
        finish.wait(timeout=5)
        return {
            "results": [
                {"task_index": 0, "status": "completed", "summary": "durable"},
                {"task_index": 1, "status": "completed", "summary": "second"},
            ]
        }

    gate = threading.Event()
    result = _dispatch(gate, delegation_id="deleg-contract", runner=runner)
    assert result["status"] == "dispatched"
    assert first.wait(timeout=3)
    with ad._transaction() as conn:
        parent = conn.execute(
            "SELECT state, contract_version, task_fingerprint, child_count "
            "FROM async_delegations WHERE delegation_id='deleg-contract'"
        ).fetchone()
        child = conn.execute(
            "SELECT state, result_json FROM async_delegation_children "
            "WHERE delegation_id='deleg-contract' AND child_index=0"
        ).fetchone()
    assert parent[0] == "running"
    assert parent[1] == 2 and len(parent[2]) == 64 and parent[3] == 2
    assert child[0] == "completed"
    assert json.loads(child[1])["summary"] == "durable"
    duplicate = _dispatch(gate, delegation_id="deleg-contract")
    assert duplicate["status"] == "rejected"
    finish.set()
    _wait_state("deleg-contract", "completed")


def test_restart_generation_preserves_terminal_and_fences_stale_worker():
    gate = threading.Event()
    result = _dispatch(gate, delegation_id="deleg-generation")
    assert ad.commit_child_terminal(
        0,
        {"task_index": 0, "status": "completed", "summary": "keep"},
        delegation_id=result["delegation_id"],
        execution_generation=0,
    )
    assert ad.defer_restartable_interruption(result["delegation_id"], "gateway_drain")
    with ad._transaction() as conn:
        nonce = conn.execute(
            "SELECT restart_nonce FROM async_delegations WHERE delegation_id=?",
            (result["delegation_id"],),
        ).fetchone()[0]
    claim = ad.claim_restartable_delegation(
        result["delegation_id"],
        owner_pid=1234,
        owner_started_at=10,
        expected_session_key="telegram:origin",
        restart_nonce=nonce,
    )
    assert claim and claim["execution_generation"] == 1
    assert claim["children"][0]["result"]["summary"] == "keep"
    assert claim["children"][1]["state"] == "restarting"
    assert not ad.commit_child_terminal(
        1,
        {"task_index": 1, "status": "completed", "summary": "stale"},
        delegation_id=result["delegation_id"],
        execution_generation=0,
    )
    assert not ad._mark_runner_returned(result["delegation_id"], 0)
    gate.set()


def test_ambiguous_child_is_terminalized_without_forgetting_completed_sibling(monkeypatch):
    from tools import delegate_tool as dt

    task = {
        "goal": "2 parallel subagents: already finished; ambiguous effect",
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
    claim = {
        "delegation_id": "deleg-mixed",
        "task": task,
        "task_fingerprint": hashlib.sha256(
            json.dumps(task, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
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
            return [{
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
            }]

    committed = []
    captured = {}
    monkeypatch.setattr(
        ad,
        "commit_child_terminal",
        lambda index, result, **kwargs: committed.append((index, result, kwargs)) or True,
    )

    def fake_delegate_task(**kwargs):
        captured.update(dt._HOST_RESTART_CONTEXT.get())
        return json.dumps({"status": "dispatched", "delegation_id": "deleg-mixed"})

    monkeypatch.setattr(dt, "delegate_task", fake_delegate_task)
    parent = SimpleNamespace(_session_db=SimpleNamespace(_db=SessionDB()))
    assert dt.resume_async_delegation(claim, parent) == "dispatched"
    assert committed[0][1]["status"] == "blocked_unknown_effect"
    assert {item["task_index"] for item in captured["completed_results"]} == {0, 1}


def test_effect_proofs_are_conservative():
    from tools.delegate_tool import _restart_history_has_unknown_side_effect

    def history(content, *, tool_name="terminal", disposition=None):
        result = {"role": "tool", "tool_call_id": "call-1", "content": content}
        if disposition is not None:
            result["effect_disposition"] = disposition
        return [
            {"role": "assistant", "tool_calls": [{"id": "call-1", "function": {"name": tool_name, "arguments": "{}"}}]},
            result,
        ]

    assert _restart_history_has_unknown_side_effect(history(""))
    assert _restart_history_has_unknown_side_effect(
        history('{"exit_code":0}', disposition="unknown")
    )
    assert not _restart_history_has_unknown_side_effect(history('{"exit_code":0}'))
    assert _restart_history_has_unknown_side_effect(
        history('{"success":true}', tool_name="write_file")
    )
    assert not _restart_history_has_unknown_side_effect(
        history('{"verified":true,"bytes_written":12}', tool_name="write_file")
    )


def test_batch_runner_exception_terminalizes_every_child_and_parent():
    def runner():
        raise RuntimeError("synthetic runner crash")

    gate = threading.Event()
    result = _dispatch(gate, delegation_id="deleg-exception", runner=runner)
    assert result["status"] == "dispatched"
    _wait_state(result["delegation_id"], "error")
    with ad._transaction() as conn:
        children = conn.execute(
            "SELECT state, replay_decision FROM async_delegation_children "
            "WHERE delegation_id=? ORDER BY child_index",
            (result["delegation_id"],),
        ).fetchall()
    assert children == [("failed", "runner_exception"), ("failed", "runner_exception")]
