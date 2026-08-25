"""Compression watermark contract for a precommitted gateway origin row."""

from unittest.mock import MagicMock
from types import SimpleNamespace

from agent.conversation_compression import _cap_compression_commit_watermark
from agent.context_compressor import ContextCompressor
from hermes_state import SessionDB
from run_agent import AIAgent


def test_gateway_authority_ceiling_preserves_excluded_tail():
    agent = SimpleNamespace(_compression_commit_watermark_ceiling=41)

    assert _cap_compression_commit_watermark(agent, 42) == 41


def test_gateway_authority_ceiling_never_expands_or_accepts_bool():
    assert _cap_compression_commit_watermark(
        SimpleNamespace(_compression_commit_watermark_ceiling=100),
        42,
    ) == 42
    assert _cap_compression_commit_watermark(
        SimpleNamespace(_compression_commit_watermark_ceiling=True),
        42,
    ) == 42


def test_gateway_authority_watermark_failure_aborts_before_summary_or_archive(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    db = SessionDB(db_path=tmp_path / "state.db")
    session_id = "authority-watermark-fault"
    db.create_session(session_id, source="telegram")
    authority_row = db.append_message(
        session_id,
        role="user",
        content="current exact authority",
        platform_message_id="tg-77",
    )
    original = db.get_messages_as_conversation(session_id)
    agent = AIAgent(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        model="test/model",
        quiet_mode=True,
        session_db=db,
        session_id=session_id,
        skip_context_files=True,
        skip_memory=True,
    )
    agent._cached_system_prompt = "system"
    agent._compression_commit_watermark_ceiling = authority_row - 1
    summary = MagicMock(
        return_value=[{"role": "assistant", "content": "unsafe summary"}]
    )
    agent.context_compressor.compress = summary
    archive = MagicMock(wraps=db.archive_and_compact)
    db.archive_and_compact = archive
    db.get_active_message_watermark = MagicMock(
        side_effect=OSError("forced watermark read failure")
    )

    returned, _ = agent._compress_context(
        original,
        "system",
        approx_tokens=120_000,
        force=True,
    )

    assert returned is original
    summary.assert_not_called()
    archive.assert_not_called()
    assert db.get_messages_as_conversation(session_id) == original
    db.close()


def test_context_compressor_propagates_bound_turn_holder_to_rewrite_commit():
    db = MagicMock()
    compressor = ContextCompressor(model="test/model", quiet_mode=True)
    compressor._session_db = db
    compressor._session_id = "bound-session"
    compressor.bind_turn_lease("holder-H", ttl_seconds=17.0)
    compacted = [{"role": "assistant", "content": "summary"}]

    compressor._sync_micro_compact_to_db(compacted)

    db.archive_and_compact.assert_called_once_with(
        "bound-session",
        compacted,
        turn_lease_holder="holder-H",
        turn_lease_ttl_seconds=17.0,
    )
