from __future__ import annotations

import json
from types import SimpleNamespace


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}


def test_resumed_child_capabilities_are_exact_and_refresh_stable() -> None:
    from model_tools import get_tool_definitions
    from toolsets import TOOLSETS
    from tools.delegate_tool import _apply_child_capability_ceiling

    child = SimpleNamespace(
        tools=[_schema("read_file"), _schema("terminal"), _schema("web_search")],
        valid_tool_names={"read_file", "terminal", "web_search"},
        enabled_toolsets=["hermes-cli"],
        disabled_toolsets=[],
        _context_engine_tool_names={"web_search"},
    )

    _apply_child_capability_ceiling(child, ["read_file", "terminal"])
    synthetic = child.enabled_toolsets[0]
    try:
        assert child.valid_tool_names == {"read_file", "terminal"}
        assert {item["function"]["name"] for item in child.tools} == {
            "read_file",
            "terminal",
        }
        assert child._context_engine_tool_names == set()

        refreshed = get_tool_definitions(
            enabled_toolsets=child.enabled_toolsets,
            disabled_toolsets=child.disabled_toolsets,
            quiet_mode=True,
        )
        assert {item["function"]["name"] for item in refreshed} == {
            "read_file",
            "terminal",
        }
    finally:
        TOOLSETS.pop(synthetic, None)


def test_resume_fails_closed_without_persisted_capability_snapshot() -> None:
    from tools.delegate_tool import resume_async_delegation

    claim = {
        "delegation_id": "deleg_missing_caps",
        "task": {"goals": ["continue"]},
        "child_session_ids": ["child-1"],
        "child_capability_names": [],
    }
    assert resume_async_delegation(claim, SimpleNamespace()) == "failed"


def test_corrupt_restart_json_is_bounded_and_never_strands(
    tmp_path, monkeypatch
) -> None:
    from tools import async_delegation as ad

    monkeypatch.setattr(ad, "get_hermes_home", lambda: tmp_path)
    delegation_id = "deleg_corrupt_contract"
    with ad._transaction() as conn:
        conn.execute(
            """INSERT INTO async_delegations
               (delegation_id, origin_session, parent_session_id, state,
                dispatched_at, updated_at, delivery_state, task_json,
                heartbeat_at, restart_policy, restart_nonce,
                child_session_ids_json, child_capability_names_json)
               VALUES (?, 'origin', 'parent', 'restart_pending', 1, 1,
                       'pending', '{', 1, 'gateway_owned_v1', 'nonce-1',
                       '[', 'not-json')""",
            (delegation_id,),
        )

    nonce = "nonce-1"
    for attempt in (1, 2, 3):
        claim = ad.claim_restartable_delegation(
            delegation_id,
            owner_pid=100 + attempt,
            owner_started_at=attempt,
            expected_session_key="origin",
            restart_nonce=nonce,
        )
        assert claim is not None
        assert claim["task"] == {}
        assert claim["child_session_ids"] == []
        assert claim["child_capability_names"] == []
        assert ad.release_restart_claim(delegation_id, "dead_owner") is (
            attempt < 3
        )
        with ad._transaction() as conn:
            state, nonce = conn.execute(
                "SELECT state, restart_nonce FROM async_delegations "
                "WHERE delegation_id=?",
                (delegation_id,),
            ).fetchone()
        assert state == "restart_pending"

    assert ad.finalize_exhausted_restarts() == 1
    with ad._transaction() as conn:
        state, event_json = conn.execute(
            "SELECT state, event_json FROM async_delegations WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()
    assert state == "error"
    assert json.loads(event_json)["task"] == {}
