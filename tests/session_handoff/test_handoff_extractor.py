"""Tests for handoff_extractor and artifact_writer (T-010)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.session_handoff.artifact_writer import (
    HandoffArtifactWriter,
    ArtifactPaths,
    resolve_artifact_paths,
    _scan_for_secrets,
    _redact,
    _slugify,
)
from hermes_cli.session_handoff.handoff_extractor import HandoffExtractor, HandoffResult
from hermes_cli.session_handoff.trace_reader import SessionTraceReader


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def make_handoff_result(
    topic,
    context=None,
    decisions=None,
    files=None,
    commands=None,
    blockers=None,
    next_steps=None,
    confidence=0.75,
    staleness="fresh (< 1 day)",
):
    """Factory for HandoffResult — bypasses frozen constraint for tests."""
    context = context or f"Topic: {topic}"
    decisions = decisions or []
    files = files or []
    commands = commands or []
    blockers = blockers or []
    next_steps = next_steps or ["verify and close"]
    return HandoffResult(
        topic=topic,
        context=context,
        decisions=decisions,
        evidence_refs=["session://s1", "session://s2"],
        files=files,
        commands=commands,
        blockers=blockers,
        next_steps=next_steps,
        resume_prompt=f"# Resume: {topic}\n\nContinue from the last step.",
        confidence=confidence,
        staleness=staleness,
        session_ids=["s1", "s2"],
        messages_inspected=10,
    )


# ------------------------------------------------------------------
# HandoffExtractor tests
# ------------------------------------------------------------------

class TestHandoffExtractorEmpty:
    def test_empty_sessions_returns_empty_result(self):
        reader = SessionTraceReader()  # wont connect -- returns empty list
        ext = HandoffExtractor(reader)
        result = ext.extract([], "guardian setup")
        assert result.topic == "guardian setup"
        assert result.context == "No prior sessions found for topic: guardian setup"
        assert result.confidence == 0.0


class TestHandoffExtractorPatterns:
    """Smoke tests on pattern extraction using populated_db fixture."""

    def test_extract_with_sessions(self, populated_db):
        reader = SessionTraceReader(db_path=populated_db)
        ext = HandoffExtractor(reader)
        sessions = reader.query(source="telegram")
        result = ext.extract(sessions, "guardian setup", max_messages_per_session=10)
        assert isinstance(result, HandoffResult)
        assert result.topic == "guardian setup"
        assert result.evidence_refs
        assert result.session_ids
        assert result.messages_inspected > 0

    def test_extract_runs_on_single_session(self, populated_db):
        reader = SessionTraceReader(db_path=populated_db)
        ext = HandoffExtractor(reader)
        sessions = reader.query(source="telegram")
        result = ext.extract(sessions[:1], "payment research", max_messages_per_session=5)
        assert "payment research" in result.context
        assert len(result.session_ids) <= 3

    def test_confidence_score_float(self, populated_db):
        reader = SessionTraceReader(db_path=populated_db)
        ext = HandoffExtractor(reader)
        sessions = reader.query()
        result = ext.extract(sessions, "test topic")
        assert 0.0 <= result.confidence <= 1.0

    def test_staleness_is_string(self, populated_db):
        reader = SessionTraceReader(db_path=populated_db)
        ext = HandoffExtractor(reader)
        sessions = reader.query()
        result = ext.extract(sessions, "test topic")
        assert isinstance(result.staleness, str)
        assert len(result.staleness) > 0


# ------------------------------------------------------------------
# ArtifactPaths tests
# ------------------------------------------------------------------

class TestArtifactPathsResolve:
    def test_resolve_creates_slug(self):
        paths = resolve_artifact_paths("guardian setup", base_dir=Path("/tmp/test-handoffs"))
        assert "guardian" in paths.slug
        assert paths.md_path.suffix == ".md"
        assert paths.json_path.suffix == ".json"
        assert paths.dir == paths.md_path.parent

    def test_resolve_unicode_topic(self):
        paths = resolve_artifact_paths("Человек 2.0 setup", base_dir=Path("/tmp/test-handoffs"))
        assert paths.slug

    def test_slugify_removes_special_chars(self):
        slug = _slugify("web3 gambling research!")
        assert "/" not in slug
        assert "!" not in slug

    def test_slugify_bounds_length(self):
        long_topic = "x" * 200
        slug = _slugify(long_topic)
        assert len(slug) <= 80

    def test_slugify_lowercase(self):
        slug = _slugify("Guardian Setup")
        assert slug == slug.lower()


# ------------------------------------------------------------------
# Secret scanner tests
# ------------------------------------------------------------------

class TestSecretScanner:
    def test_detects_openai_key(self):
        found = _scan_for_secrets("my api key is sk-1234567890abcdefghij")
        assert len(found) >= 1

    def test_detects_github_token(self):
        found = _scan_for_secrets("token ghp_abcd1234efgh5678ijkl")
        assert len(found) >= 1




    def test_detects_private_key_header(self):
        # The header alone triggers the scan
        found = _scan_for_secrets("-----BEGIN RSA PRIVATE KEY-----")
        assert len(found) >= 1

    def test_detects_private_key_in_decisions(self):
        # Private key in decisions field triggers write rejection
        found = _scan_for_secrets(
            "decided to use certificate: -----BEGIN RSA PRIVATE KEY-----"
        )
        assert len(found) >= 1

    def test_pass_through_normal_text(self):
        normal = "Let us set up the Guardian monitoring for the server infrastructure"
        found = _scan_for_secrets(normal)
        assert len(found) == 0

    def test_pass_through_filenames(self):
        text = "file: /opt/hermes-agent/hermes_cli/session_handoff/trace_reader.py"
        found = _scan_for_secrets(text)
        assert len(found) == 0

    def test_redact_replaces_openai_key(self):
        text = "key is sk-1234567890abcdefghijklmnopqrstuvwxyz"
        redacted = _redact(text)
        assert "[REDACTED]" in redacted

    def test_redact_leaves_normal_text(self):
        text = "Let us set up the Guardian setup session"
        redacted = _redact(text)
        assert "Guardian setup" in redacted


# ------------------------------------------------------------------
# HandoffArtifactWriter tests
# ------------------------------------------------------------------

class TestArtifactWriterWrite:
    def test_write_creates_files(self, tmp_path):
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        result = make_handoff_result("guardian test")
        paths = writer.write(result)
        assert paths.md_path.exists()
        assert paths.json_path.exists()

    def test_write_json_is_valid(self, tmp_path):
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        result = make_handoff_result("payment research")
        paths = writer.write(result)
        data = json.loads(paths.json_path.read_text())
        assert data["schema"] == "session-handoff-artifact.v1"
        assert data["topic"] == "payment research"
        assert "resume_prompt" in data
        assert "confidence" in data

    def test_write_md_is_readable(self, tmp_path):
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        result = make_handoff_result("auth bug investigation")
        paths = writer.write(result)
        md = paths.md_path.read_text()
        assert "# Handoff:" in md
        assert "Confidence:" in md
        assert "Resume prompt" in md

    def test_write_rejects_openai_key(self, tmp_path):
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        result = make_handoff_result("api test", context="Using API key sk-1234567890abcdefghij for testing")
        with pytest.raises(ValueError, match="secret-like"):
            writer.write(result)

    def test_write_rejects_github_token(self, tmp_path):
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        result = make_handoff_result("deploy config", decisions=["set GitHub token ghp_abcd1234efgh5678ijkl"])
        with pytest.raises(ValueError, match="secret-like"):
            writer.write(result)

    def test_write_rejects_private_key(self, tmp_path):
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        result = make_handoff_result("cert setup", decisions=["-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBA"])
        with pytest.raises(ValueError, match="secret-like"):
            writer.write(result)

    def test_write_md_has_numbered_next_steps(self, tmp_path):
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        result = make_handoff_result("monitoring setup", next_steps=["check logs", "verify health"])
        paths = writer.write(result)
        md = paths.md_path.read_text()
        assert "check logs" in md
        assert "verify health" in md

    def test_write_without_next_steps(self, tmp_path):
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        result = HandoffResult(
            topic="simple task",
            context="Context summary",
            decisions=["decided to use file-based config"],
            evidence_refs=["session://s1"],
            files=["/tmp/test.conf"],
            commands=["ls /tmp"],
            blockers=[],
            next_steps=[],
            resume_prompt="Resume the task from the last decision.",
            confidence=0.8,
            staleness="recent (2.0 days old)",
            session_ids=["s1"],
            messages_inspected=5,
        )
        paths = writer.write(result)
        assert paths.md_path.exists()
        assert paths.json_path.exists()

    def test_write_deduplicates_files(self, tmp_path):
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        result = make_handoff_result("repeated files", files=["/a/b.py", "/a/b.py", "/c/d.py"])
        paths = writer.write(result)
        data = json.loads(paths.json_path.read_text())
        assert data["files"].count("/a/b.py") == 1
        assert data["files"].count("/c/d.py") == 1


# ------------------------------------------------------------------
# Integration test: extractor + writer
# ------------------------------------------------------------------

class TestExtractorWriterIntegration:
    def test_full_flow(self, populated_db, tmp_path):
        reader = SessionTraceReader(db_path=populated_db)
        ext = HandoffExtractor(reader)
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        sessions = reader.query(source="telegram")
        result = ext.extract(sessions, "guardian monitoring", max_messages_per_session=10)
        paths = writer.write(result)
        assert paths.md_path.exists()
        assert paths.json_path.exists()
        data = json.loads(paths.json_path.read_text())
        assert data["topic"] == "guardian monitoring"
        assert data["confidence"] >= 0.0
        assert data["staleness"]
        md = paths.md_path.read_text()
        assert "# Handoff:" in md

    def test_no_mem0g_write(self, populated_db, tmp_path, monkeypatch):
        """Verify no external memory system write during artifact creation."""
        written_paths = []
        original_write = Path.write_text
        def track_write(self, *args, **kwargs):
            written_paths.append(str(self))
            return original_write(self, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", track_write)

        reader = SessionTraceReader(db_path=populated_db)
        ext = HandoffExtractor(reader)
        writer = HandoffArtifactWriter(base_dir=tmp_path)
        sessions = reader.query(source="telegram")
        result = ext.extract(sessions, "test topic")
        writer.write(result)

        # All writes must be under base_dir
        for p in written_paths:
            assert p.startswith(str(tmp_path)), f"Write outside base_dir: {p}"
