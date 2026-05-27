"""HandoffSummaryCommand — write continuation-ready session handoff artifacts.

This is the artifact-producing `/handoff <topic>` path for the session handoff
layer. It is deliberately local-only: reads Hermes state.db, writes profile-local
artifacts under ~/.hermes/handoffs, and never promotes anything to memory or
external systems.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from hermes_cli.session_handoff.artifact_writer import ArtifactPaths, HandoffArtifactWriter
from hermes_cli.session_handoff.handoff_extractor import HandoffExtractor, HandoffResult
from hermes_cli.session_handoff.query_parser import RecallQueryParser
from hermes_cli.session_handoff.trace_reader import SessionTraceReader


@dataclass(frozen=True, slots=True)
class HandoffSummaryOutcome:
    """Result of generating a handoff artifact pair."""

    result: HandoffResult
    paths: ArtifactPaths
    elapsed_ms: float


class HandoffSummaryFormatter:
    """Formats artifact generation results for CLI output."""

    @staticmethod
    def format_outcome(outcome: HandoffSummaryOutcome) -> str:
        result = outcome.result
        paths = outcome.paths
        elapsed = outcome.elapsed_ms
        elapsed_text = f"{elapsed:.0f}ms" if elapsed < 1000 else f"{elapsed / 1000:.1f}s"

        lines = [
            f"↻ /handoff — wrote handoff for: {result.topic} ({elapsed_text})",
            "",
            f"confidence: {result.confidence:.0%} | staleness: {result.staleness}",
            f"markdown: {paths.md_path}",
            f"json: {paths.json_path}",
        ]
        if result.evidence_refs:
            lines.append(f"evidence: {', '.join(result.evidence_refs[:5])}")
        if result.blockers:
            lines.append("blockers:")
            lines.extend(f"  • {b}" for b in result.blockers[:5])
        if result.next_steps:
            lines.append("next:")
            lines.extend(f"  {i}. {step}" for i, step in enumerate(result.next_steps[:5], 1))
        lines.extend([
            "",
            "resume prompt:",
            result.resume_prompt,
        ])
        return "\n".join(lines)


class HandoffSummaryCommand:
    """Build and write a continuation-ready handoff for a topic."""

    def __init__(self, db_path: Optional[str | Path] = None, base_dir: Optional[Path] = None) -> None:
        self._reader = SessionTraceReader(str(db_path)) if db_path else SessionTraceReader()
        self._parser = RecallQueryParser()
        self._extractor = HandoffExtractor(self._reader)
        self._writer = HandoffArtifactWriter(base_dir=base_dir)

    def run(self, raw_topic: str) -> HandoffSummaryOutcome:
        topic = raw_topic.strip()
        if not topic:
            raise ValueError("Usage: /handoff <topic>")

        t0 = time.monotonic()
        parsed = self._parser.parse(topic)
        trace_args = self._parser.to_trace_args(parsed)
        # Handoffs inspect message snippets, so keep the session candidate set
        # deliberately small. Extraction still uses only the top 3 sessions.
        trace_args["limit"] = 10
        sessions = self._reader.query(**trace_args)
        result = self._extractor.extract(sessions, topic, max_messages_per_session=10)
        paths = self._writer.write(result)
        elapsed_ms = (time.monotonic() - t0) * 1000
        return HandoffSummaryOutcome(result=result, paths=paths, elapsed_ms=elapsed_ms)

    def execute(self, raw_topic: str) -> str:
        return HandoffSummaryFormatter.format_outcome(self.run(raw_topic))


def run_handoff_summary(topic: str, db_path: Optional[str | Path] = None, base_dir: Optional[Path] = None) -> str:
    """Convenience entry point used by CLI tests and simple integrations."""

    return HandoffSummaryCommand(db_path=db_path, base_dir=base_dir).execute(topic)
