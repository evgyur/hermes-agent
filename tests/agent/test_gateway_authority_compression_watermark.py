"""Compression watermark contract for a precommitted gateway origin row."""

from types import SimpleNamespace

from agent.conversation_compression import _cap_compression_commit_watermark


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
