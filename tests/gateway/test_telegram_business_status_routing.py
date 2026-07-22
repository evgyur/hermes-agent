"""Regression tests for Telegram Business status/progress routing.

Final replies are sent by ``BasePlatformAdapter`` with source-derived metadata.
Gateway-owned synthetic sends (long-running notifications, progress, status
callbacks) use ``GatewayRunner._thread_metadata_for_source`` instead.  Those
paths must preserve the Telegram Business connection even when a private chat
has no thread id, or the user sees a long silent turn followed only by the
final reply.
"""

from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _runner() -> GatewayRunner:
    return object.__new__(GatewayRunner)


def test_business_root_dm_status_preserves_connection_without_thread() -> None:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="700000002",
        chat_type="dm",
        message_id="123",
        business_connection_id="biz-123",
    )

    metadata = _runner()._thread_metadata_for_source(source)

    assert metadata == {"business_connection_id": "biz-123"}


def test_external_business_status_preserves_fail_closed_markers() -> None:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="700000002",
        chat_type="dm",
        business_connection_id="biz-external",
        external_safe_mode=True,
    )

    metadata = _runner()._thread_metadata_for_source(source)

    assert metadata == {
        "business_connection_id": "biz-external",
        "external_safe_mode": True,
        "telegram_business_external_contact": True,
    }


def test_business_dm_topic_status_combines_connection_and_thread_route() -> None:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="700000002",
        chat_type="dm",
        thread_id="22182",
        message_id="123",
        business_connection_id="biz-123",
    )

    metadata = _runner()._thread_metadata_for_source(source)

    assert metadata == {
        "business_connection_id": "biz-123",
        "thread_id": "22182",
        "telegram_dm_topic_reply_fallback": True,
        "direct_messages_topic_id": "22182",
        "telegram_reply_to_message_id": "123",
    }
