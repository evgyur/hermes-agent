"""State-store contracts used by the gateway authority barrier."""

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
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

        inserted = db.append_or_reuse_gateway_user_authority(
            "authority-session",
            content="ship it",
            platform_message_id="tg-42",
            turn_lease_holder=holder,
        )
        assert inserted.row_id > 0
        assert inserted.inserted is True
        reused = db.append_or_reuse_gateway_user_authority(
            "authority-session",
            content="ship it",
            platform_message_id="tg-42",
            turn_lease_holder=holder,
        )
        assert reused.row_id == inserted.row_id
        assert reused.inserted is False
        row_id = inserted.row_id

        db.archive_and_compact(
            "authority-session",
            [{"role": "assistant", "content": "older history summary"}],
            watermark=row_id - 1,
            turn_lease_holder=holder,
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


@pytest.mark.asyncio
async def test_gateway_authority_seals_direct_reply_semantics_before_marker(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    session_id = "reply-authority"
    holder = "pid=1:reply-authority"
    db.create_session(session_id, source="telegram")
    assert db.acquire_session_turn_lease(session_id, holder, wait_seconds=0.1)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        user_id="42",
    )
    event = MessageEvent(
        text="Fix",
        source=source,
        message_id="tg-fix",
        reply_to_message_id="tg-report",
        reply_to_text="Guardian report: candidate transport is stale.",
        reply_to_is_own_message=True,
    )
    runner = GatewayRunner.__new__(GatewayRunner)

    result = await runner._persist_gateway_triggering_user_row(
        event,
        type("Entry", (), {"session_id": session_id})(),
        {"db": db, "holder": holder, "ttl_seconds": 300.0},
        platform_message_id="tg-fix",
        display_kind=None,
    )

    assert result.inserted is True
    row = db.get_messages_as_conversation(session_id)[-1]
    assert row["display_metadata"]["gateway_raw_semantic_v1"] == {
        "version": 1,
        "message_type": "text",
        "reply": {
            "message_id": "tg-report",
            "is_own": True,
            "quote": "Guardian report: candidate transport is stale.",
        },
        "media": [],
    }
    db.close()


@pytest.mark.asyncio
async def test_gateway_authority_bounds_long_rich_reply_to_consumed_semantic_prefix(
    tmp_path,
):
    db = SessionDB(db_path=tmp_path / "state.db")
    session_id = "long-rich-reply-authority"
    holder = "pid=1:long-rich-reply-authority"
    db.create_session(session_id, source="telegram")
    assert db.acquire_session_turn_lease(session_id, holder, wait_seconds=0.1)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        user_id="42",
    )
    rich_text = "🧭" * 3000
    event = MessageEvent(
        text="Use this",
        source=source,
        message_id="tg-rich-reply",
        reply_to_message_id="tg-rich",
        reply_to_text=rich_text,
        reply_to_is_own_message=True,
    )
    runner = GatewayRunner.__new__(GatewayRunner)

    result = await runner._persist_gateway_triggering_user_row(
        event,
        type("Entry", (), {"session_id": session_id})(),
        {"db": db, "holder": holder, "ttl_seconds": 300.0},
        platform_message_id="tg-rich-reply",
        display_kind=None,
    )

    assert result.inserted is True
    history = db.get_messages_as_conversation(session_id)
    envelope = history[-1]["display_metadata"]["gateway_raw_semantic_v1"]
    assert envelope["reply"]["quote"] == rich_text[:500]
    assert len(envelope["reply"]["quote"].encode("utf-8")) == 2000

    cold_event = MessageEvent(
        text="Use this", source=source, message_id="tg-rich-reply"
    )
    assert runner._restore_startup_raw_semantic_envelope(
        cold_event,
        history,
        source_message_id="tg-rich-reply",
    )
    assert cold_event.reply_to_text == rich_text[:500]
    db.close()


@pytest.mark.asyncio
async def test_gateway_authority_preserves_exact_whitespace_reply_prefix(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    session_id = "whitespace-rich-reply-authority"
    holder = "pid=1:whitespace-rich-reply-authority"
    db.create_session(session_id, source="telegram")
    assert db.acquire_session_turn_lease(session_id, holder, wait_seconds=0.1)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        user_id="42",
    )
    rich_text = (" \n" * 260) + "outside-the-live-prefix"
    event = MessageEvent(
        text="Use this",
        source=source,
        message_id="tg-whitespace-reply",
        reply_to_message_id="tg-rich",
        reply_to_text=rich_text,
    )
    runner = GatewayRunner.__new__(GatewayRunner)

    result = await runner._persist_gateway_triggering_user_row(
        event,
        type("Entry", (), {"session_id": session_id})(),
        {"db": db, "holder": holder, "ttl_seconds": 300.0},
        platform_message_id="tg-whitespace-reply",
        display_kind=None,
    )

    assert result.inserted is True
    history = db.get_messages_as_conversation(session_id)
    envelope = history[-1]["display_metadata"]["gateway_raw_semantic_v1"]
    assert envelope["reply"]["quote"] == rich_text[:500]
    cold_event = MessageEvent(
        text="Use this", source=source, message_id="tg-whitespace-reply"
    )
    assert runner._restore_startup_raw_semantic_envelope(
        cold_event,
        history,
        source_message_id="tg-whitespace-reply",
    )
    assert cold_event.reply_to_text == rich_text[:500]
    db.close()


@pytest.mark.asyncio
async def test_gateway_authority_seals_recoverable_media_refs_without_bytes(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    session_id = "media-authority"
    holder = "pid=1:media-authority"
    db.create_session(session_id, source="telegram")
    assert db.acquire_session_turn_lease(session_id, holder, wait_seconds=0.1)
    voice = tmp_path / "voice.ogg"
    photo = tmp_path / "photo.jpg"
    voice.write_bytes(b"voice-placeholder")
    photo.write_bytes(b"photo-placeholder")
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        user_id="42",
    )
    event = MessageEvent(
        text="",
        source=source,
        message_id="tg-media",
        message_type=MessageType.VOICE,
        media_urls=[str(voice), str(photo)],
        media_types=["audio/ogg", "image/jpeg"],
    )
    runner = GatewayRunner.__new__(GatewayRunner)

    await runner._persist_gateway_triggering_user_row(
        event,
        type("Entry", (), {"session_id": session_id})(),
        {"db": db, "holder": holder, "ttl_seconds": 300.0},
        platform_message_id="tg-media",
        display_kind=None,
    )

    envelope = db.get_messages_as_conversation(session_id)[-1][
        "display_metadata"
    ]["gateway_raw_semantic_v1"]
    assert envelope == {
        "version": 1,
        "message_type": "voice",
        "reply": None,
        "media": [
            {"ref": str(voice), "type": "audio/ogg"},
            {"ref": str(photo), "type": "image/jpeg"},
        ],
    }
    assert "voice-placeholder" not in repr(envelope)
    assert "photo-placeholder" not in repr(envelope)
    db.close()


@pytest.mark.asyncio
async def test_topic_root_empty_reply_stays_routing_only_across_cold_restore(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    session_id = "topic-root-routing-only"
    holder = "pid=1:topic-root-routing-only"
    db.create_session(session_id, source="telegram")
    assert db.acquire_session_turn_lease(session_id, holder, wait_seconds=0.1)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        thread_id="24901",
        user_id="42",
    )
    event = MessageEvent(
        text="Fix",
        source=source,
        message_id="tg-topic-root",
        reply_to_message_id="24901",
        reply_to_text="",
        reply_to_is_own_message=True,
    )
    runner = GatewayRunner.__new__(GatewayRunner)

    await runner._persist_gateway_triggering_user_row(
        event,
        type("Entry", (), {"session_id": session_id})(),
        {"db": db, "holder": holder, "ttl_seconds": 300.0},
        platform_message_id="tg-topic-root",
        display_kind=None,
    )
    history = db.get_messages_as_conversation(session_id)
    envelope = history[-1]["display_metadata"]["gateway_raw_semantic_v1"]
    assert envelope["reply"] is None

    cold_event = MessageEvent(text="Fix", source=source, message_id="tg-topic-root")
    assert runner._restore_startup_raw_semantic_envelope(
        cold_event,
        history,
        source_message_id="tg-topic-root",
    )
    assert cold_event.reply_to_message_id is None
    assert cold_event.reply_to_text is None
    assert cold_event.reply_to_is_own_message is False
    db.close()
