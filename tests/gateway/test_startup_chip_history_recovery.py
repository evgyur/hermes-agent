"""Startup telegram-chip history reconciliation tests.

Phase 3 keeps the runtime path read-only and stubbed: no real telegram-chip
credentials, SSH, or Telegram sessions are required in CI.
"""

from __future__ import annotations

import logging

from gateway.chip_history_recovery import (
    DEFAULT_CHIP_USER_ID,
    TELEGRAM_CHIP_RECENT_MESSAGES_CONTRACT,
    TelegramChipHistoryConfig,
    TelegramChipMessage,
    classify_chip_history_message,
    parse_telegram_chip_recent_messages,
    reconcile_chip_history_against_ledger,
    redacted_reconciliation_example,
)
from hermes_state import SessionDB


class StubTelegramChipClient:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def fetch_recent_messages(self, *, chat_id, thread_id=None, limit=20):
        self.calls.append({"chat_id": chat_id, "thread_id": thread_id, "limit": limit})
        return self.records


class FailingTelegramChipClient:
    def fetch_recent_messages(self, **_kwargs):
        raise RuntimeError("telegram-chip offline")


def _db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _message(message_id, *, sender_id=DEFAULT_CHIP_USER_ID, chat_id="617744661", thread_id=None, text=None):
    return TelegramChipMessage(
        message_id=message_id,
        sender_id=sender_id,
        sender="@ChipCR" if sender_id == DEFAULT_CHIP_USER_ID else "other",
        chat_id=chat_id,
        thread_id=thread_id,
        snippet=text or f"msg {message_id}",
    )


def test_telegram_chip_history_config_is_off_by_default_and_env_gated():
    assert TelegramChipHistoryConfig.from_mapping({}, env={}).enabled is False

    cfg = TelegramChipHistoryConfig.from_mapping(
        {},
        env={
            "HERMES_TELEGRAM_CHIP_HISTORY_RECOVERY": "1",
            "TELEGRAM_CHIP_API_URL": "http://127.0.0.1:8080/",
            "TELEGRAM_CHIP_HISTORY_LIMIT": "9",
        },
    )

    assert cfg.enabled is True
    assert cfg.base_url == "http://127.0.0.1:8080"
    assert cfg.limit == 9
    assert "GET /chats/{chat_id}/messages" in TELEGRAM_CHIP_RECENT_MESSAGES_CONTRACT


def test_parser_accepts_json_and_redacts_snippets():
    payload = {
        "success": True,
        "data": [
            {
                "id": 1001,
                "chat_id": "617744661",
                "sender_id": DEFAULT_CHIP_USER_ID,
                "date": "2026-06-17T15:00:00Z",
                "message": "rotate key sk-testSECRET1234567890 now",
            }
        ],
    }

    records = parse_telegram_chip_recent_messages(payload)

    assert records[0].message_id == "1001"
    assert records[0].chat_id == "617744661"
    assert records[0].sender_id == DEFAULT_CHIP_USER_ID
    assert "testSECRET" not in (records[0].snippet or "")


def test_parser_accepts_transcript_string_shape():
    payload = (
        "ID: 1002 | Date: 2026-06-17 | From: @ChipCR | "
        "Message: проверь зависший goal\n"
    )

    records = parse_telegram_chip_recent_messages(
        {"success": True, "data": payload},
        fallback_chat_id="617744661",
    )

    assert len(records) == 1
    assert records[0].message_id == "1002"
    assert records[0].sender == "@ChipCR"
    assert records[0].chat_id == "617744661"
    assert "goal" in (records[0].snippet or "")


def test_reconcile_classifies_missed_received_drained_and_completed(tmp_path):
    db = _db(tmp_path)
    try:
        received_id = db.record_gateway_message_received(
            platform="telegram",
            chat_id="617744661",
            thread_id=None,
            message_id="m-received",
            user_id=DEFAULT_CHIP_USER_ID,
            snippet="received but not dispatched",
        )
        drained_id = db.record_gateway_message_received(
            platform="telegram",
            chat_id="617744661",
            thread_id=None,
            message_id="m-drained",
            user_id=DEFAULT_CHIP_USER_ID,
            snippet="side effect maybe",
        )
        completed_id = db.record_gateway_message_received(
            platform="telegram",
            chat_id="617744661",
            thread_id=None,
            message_id="m-completed",
            user_id=DEFAULT_CHIP_USER_ID,
            snippet="already done",
        )
        db.update_gateway_message_ledger(received_id, status="received")
        db.update_gateway_message_ledger(drained_id, status="in_progress")
        db.update_gateway_message_ledger(drained_id, status="drained")
        db.update_gateway_message_ledger(completed_id, status="completed")

        client = StubTelegramChipClient(
            [
                _message("m-missed", text="missed by bot"),
                _message("m-received", text="safe candidate"),
                _message("m-drained", text="alert only"),
                _message("m-completed", text="ignore"),
                _message("m-other-user", sender_id="999", text="not Chip"),
                _message("m-other-thread", thread_id="777", text="wrong topic"),
            ]
        )

        result = reconcile_chip_history_against_ledger(
            db,
            client,
            chat_id="617744661",
            thread_id=None,
            limit=20,
        )

        assert result.enabled is True
        assert result.records_checked == 4
        by_id = {c.message.message_id: c for c in result.classifications}
        assert by_id["m-missed"].status == "missed_by_gateway"
        assert by_id["m-missed"].action == "alert_only"
        assert by_id["m-received"].status == "safe_auto_requeue_candidate"
        assert by_id["m-received"].action == "auto_requeue_candidate"
        assert by_id["m-drained"].status == "alert_only"
        assert by_id["m-drained"].action == "alert_only"
        assert by_id["m-completed"].status == "completed_ignore"
        assert by_id["m-completed"].action == "ignore"
        assert "m-other-user" not in by_id
        assert "m-other-thread" not in by_id

        example = redacted_reconciliation_example(result)
        assert example["missed_by_gateway"]["message_id"] == "m-missed"
        assert example["safe_auto_requeue_candidate"]["ledger_status"] == "received"
    finally:
        db.close()


def test_reconcile_keeps_forum_topics_scoped(tmp_path):
    db = _db(tmp_path)
    try:
        db.record_gateway_message_received(
            platform="telegram",
            chat_id="-100123",
            thread_id="42",
            message_id="topic-msg",
            user_id=DEFAULT_CHIP_USER_ID,
        )
        client = StubTelegramChipClient(
            [
                _message("topic-msg", chat_id="-100123", thread_id="42"),
                _message("wrong-topic", chat_id="-100123", thread_id="43"),
                _message("no-topic", chat_id="-100123", thread_id=None),
            ]
        )

        result = reconcile_chip_history_against_ledger(
            db,
            client,
            chat_id="-100123",
            thread_id="42",
        )

        assert [c.message.message_id for c in result.classifications] == ["topic-msg"]
        assert result.classifications[0].status == "safe_auto_requeue_candidate"
    finally:
        db.close()


def test_reconcile_degrades_to_ledger_only_when_telegram_chip_fails(tmp_path, caplog):
    db = _db(tmp_path)
    try:
        with caplog.at_level(logging.WARNING):
            result = reconcile_chip_history_against_ledger(
                db,
                FailingTelegramChipClient(),
                chat_id="617744661",
            )

        assert result.enabled is True
        assert result.degraded is True
        assert result.classifications == ()
        assert "ledger-only recovery" in (result.warning or "")
        assert "telegram-chip history unavailable" in caplog.text
    finally:
        db.close()


def test_classifier_never_auto_replays_unknown_or_side_effectful_rows():
    message = _message("m-side")

    unknown = classify_chip_history_message(message, {"id": 1, "status": "custom"})
    side_effectful = classify_chip_history_message(
        message,
        {"id": 2, "status": "in_progress", "dispatch_started_at": 123.0},
    )

    assert unknown.action == "alert_only"
    assert side_effectful.action == "alert_only"
