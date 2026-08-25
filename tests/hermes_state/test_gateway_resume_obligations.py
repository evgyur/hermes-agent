from hermes_state import SessionDB


def _admit(db, *, expected_generation, task, origin):
    return db.admit_gateway_resume_obligation(
        session_key="telegram:chat:1",
        resume_task_id=task,
        expected_generation=expected_generation,
        origin_json=origin,
        origin_sha256=f"sha:{origin}",
        reason="interrupted",
        marked_at=10.0 + expected_generation,
    )


def test_stale_store_cannot_clear_or_overwrite_new_generation(tmp_path):
    path = tmp_path / "state.db"
    stale = SessionDB(path)
    current = SessionDB(path)
    try:
        first = _admit(stale, expected_generation=0, task="task-1", origin="one")
        assert first["generation"] == 1
        assert stale.cancel_gateway_resume_obligation(
            session_key="telegram:chat:1",
            resume_task_id="task-1",
            expected_generation=1,
            reason="superseded",
        )
        second = _admit(current, expected_generation=1, task="task-2", origin="two")
        assert second["generation"] == 2

        assert _admit(stale, expected_generation=1, task="stale", origin="stale") is None
        assert not stale.claim_gateway_resume_obligation(
            session_key="telegram:chat:1", resume_task_id="task-1",
            expected_generation=1, claim_owner="old", claim_token="old-token",
        )
        assert not stale.clear_gateway_resume_obligation(
            session_key="telegram:chat:1", resume_task_id="task-1",
            expected_generation=1, claim_token="old-token",
        )
        assert not stale.cancel_gateway_resume_obligation(
            session_key="telegram:chat:1", resume_task_id="task-1",
            expected_generation=1, reason="stale",
        )

        row = current.get_gateway_resume_obligation("telegram:chat:1")
        assert (row["resume_task_id"], row["generation"], row["origin_json"]) == (
            "task-2", 2, "two"
        )
    finally:
        stale.close()
        current.close()


def test_resume_claim_and_clear_require_exact_generation_and_token(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        assert _admit(db, expected_generation=0, task="task", origin="origin")
        assert db.claim_gateway_resume_obligation(
            session_key="telegram:chat:1", resume_task_id="task",
            expected_generation=1, claim_owner="worker", claim_token="token",
        )
        assert not db.cancel_gateway_resume_obligation(
            session_key="telegram:chat:1", resume_task_id="task",
            expected_generation=1, reason="cannot-cancel-live-claim",
        )
        assert not db.clear_gateway_resume_obligation(
            session_key="telegram:chat:1", resume_task_id="task",
            expected_generation=1, claim_token="wrong",
        )
        assert db.clear_gateway_resume_obligation(
            session_key="telegram:chat:1", resume_task_id="task",
            expected_generation=1, claim_token="token",
        )
        row = db.get_gateway_resume_obligation("telegram:chat:1")
        assert row["state"] == "TERMINAL"
        assert row["generation"] == 1
    finally:
        db.close()
