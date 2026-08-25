"""State-store contracts used by the gateway authority barrier."""

import pytest

from hermes_state import SessionDB


def test_gateway_authority_user_row_reuses_only_exact_unfinished_tail(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("authority-session", source="telegram")
        holder = "pid=1:test-gateway-authority"
        assert db.acquire_session_turn_lease(
            "authority-session",
            holder,
            wait_seconds=0.1,
        )

        row_id = db.append_or_reuse_gateway_user_authority(
            "authority-session",
            content="ship it",
            platform_message_id="tg-42",
            turn_lease_holder=holder,
        )
        assert row_id > 0
        assert db.append_or_reuse_gateway_user_authority(
            "authority-session",
            content="ship it",
            platform_message_id="tg-42",
            turn_lease_holder=holder,
        ) == row_id

        db.archive_and_compact(
            "authority-session",
            [{"role": "assistant", "content": "older history summary"}],
            watermark=row_id - 1,
        )
        enriched_row_id = db.enrich_gateway_user_authority(
            "authority-session",
            row_id,
            content="ship it with the transcribed attachment",
            platform_message_id="tg-42",
            turn_lease_holder=holder,
        )
        assert enriched_row_id > 0
        assert enriched_row_id != row_id
        row_id = enriched_row_id
        durable = db.get_messages_as_conversation("authority-session")
        assert durable[-1]["content"] == "ship it with the transcribed attachment"

        db.append_message(
            "authority-session",
            "assistant",
            "done",
            turn_lease_holder=holder,
        )
        with pytest.raises(RuntimeError, match="already has downstream rows"):
            db.enrich_gateway_user_authority(
                "authority-session",
                row_id,
                content="late rewrite",
                platform_message_id="tg-42",
                turn_lease_holder=holder,
            )
        with pytest.raises(RuntimeError, match="already has durable downstream rows"):
            db.append_or_reuse_gateway_user_authority(
                "authority-session",
                content="ship it with the transcribed attachment",
                platform_message_id="tg-42",
                turn_lease_holder=holder,
            )
    finally:
        db.close()
