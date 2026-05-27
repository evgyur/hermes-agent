"""Session Handoff extractors and artifact writers.

Package for the session-handoff layer:

``trace_reader``   — profile-aware state.db read-only access (T-008)
``query_parser``   — /recall query parser with @filter annotations (T-008)
``handoff_extractor`` — builds grounded handoff dict from top sessions (T-010)
``artifact_writer``   — writes profile-local .md/.json handoff artifacts (T-010)
"""

from __future__ import annotations

from hermes_cli.session_handoff.trace_reader import SessionTraceReader
from hermes_cli.session_handoff.query_parser import RecallQueryParser, ParsedRecallQuery
from hermes_cli.session_handoff.handoff_extractor import HandoffExtractor, HandoffResult
from hermes_cli.session_handoff.artifact_writer import HandoffArtifactWriter, ArtifactPaths

__all__ = [
    "SessionTraceReader",
    "RecallQueryParser",
    "ParsedRecallQuery",
    "HandoffExtractor",
    "HandoffResult",
    "HandoffArtifactWriter",
    "ArtifactPaths",
]
