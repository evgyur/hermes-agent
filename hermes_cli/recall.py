"""RecallCommand — /recall implementation for session handoff layer.

Produces ranked compact session cards from Hermes state.db via SessionTraceReader.
No mem0g writes, no raw transcript dumps, no external side effects.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Use the already-implemented trace reader and query parser from T-008.
from hermes_cli.session_handoff.query_parser import RecallQueryParser
from hermes_cli.session_handoff.trace_reader import SessionTraceReader

# ----------------------------------------------------------------------
# Output dataclass
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionCard:
    """One compact session card returned by RecallCommand.format()."""

    session_id: str
    title: str
    source: str
    model: str
    started_at: str  # ISO-format string
    ended_at: Optional[str]
    end_reason: Optional[str]
    message_count: int
    tool_call_count: int
    handoff_state: Optional[str]
    handoff_platform: Optional[str]
    handoff_error: Optional[str]
    # Why this session matched the query
    why_matched: str
    # Last observable state summary
    last_state: str
    # Tools used in this session
    tools: List[str]
    # Evidence refs
    evidence_refs: List[str]
    # Match quality signal
    rank_score: float = 0.0


# ----------------------------------------------------------------------
# Formatter
# ----------------------------------------------------------------------


class RecallFormatter:
    """Formats SessionCard objects as bounded, human-readable text.

    Strict limits:
    - title: 60 chars
    - why_matched: 120 chars
    - last_state: 200 chars
    - per-card total: ~600 chars max
    """

    MAX_TITLE = 60
    MAX_WHY = 120
    MAX_LAST_STATE = 200
    MAX_TOOLS = 5
    MAX_CARDS = 10

    @classmethod
    def format_card(cls, card: SessionCard, index: int) -> str:
        lines = [
            f"{index}. [{card.source}] {cls._truncate(card.title, cls.MAX_TITLE)}",
            f"   id={card.session_id} | {card.started_at[:10]} | {card.model}",
            f"   why: {cls._truncate(card.why_matched, cls.MAX_WHY)}",
            f"   state: {cls._truncate(card.last_state, cls.MAX_LAST_STATE)}",
        ]
        if card.tools:
            tool_str = ", ".join(card.tools[: cls.MAX_TOOLS])
            if len(card.tools) > cls.MAX_TOOLS:
                tool_str += f" +{len(card.tools) - cls.MAX_TOOLS} more"
            lines.append(f"   tools: {tool_str}")
        if card.handoff_error:
            lines.append(f"   ⚠ handoff error: {card.handoff_error[:60]}")
        return "\n".join(lines)

    @classmethod
    def format_results(
        cls,
        cards: List[SessionCard],
        query: str,
        elapsed_ms: float,
        errors: Optional[List[str]] = None,
    ) -> str:
        if not cards:
            parts = ["🔍 /recall results for: " + query, "", "No sessions matched."]
            if errors:
                parts.extend(["", "Warnings:"] + [f"  • {e}" for e in errors])
            parts.extend(
                [
                    "",
                    "Tips:",
                    "  /recall <topic>           — basic search",
                    "  /recall <topic> @today    — today only",
                    "  /recall <topic> @7d       — last 7 days",
                    "  /recall <topic> @source:telegram  — Telegram only",
                    "  /recall <topic> @tool:terminal     — sessions using terminal",
                ]
            )
            return "\n".join(parts)

        header = f"🔍 /recall — {len(cards)} session(s) for: {query}"
        if elapsed_ms < 1000:
            header += f" ({elapsed_ms:.0f}ms)"
        else:
            header += f" ({elapsed_ms/1000:.1f}s)"
        lines = [header, "", "=" * 60]
        for i, card in enumerate(cards[: cls.MAX_CARDS], 1):
            lines.append(cls.format_card(card, i))
            lines.append("")
        if len(cards) > cls.MAX_CARDS:
            lines.append(f"(showing first {cls.MAX_CARDS} of {len(cards)} matches)")
        return "\n".join(lines)

    @staticmethod
    def _truncate(s: str, max_len: int) -> str:
        if not s:
            return "—"
        s = s.strip()
        if len(s) <= max_len:
            return s
        return s[: max_len - 1].rstrip() + "…"


# ----------------------------------------------------------------------
# RecallCommand
# ----------------------------------------------------------------------


class RecallCommand:
    """Implements /recall <query>.

    Uses SessionTraceReader (T-008) for state.db access and RecallQueryParser
    (T-008) for query parsing. Produces ranked compact SessionCards with
    bounded output, no raw transcripts.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._reader = SessionTraceReader(db_path) if db_path else SessionTraceReader()
        self._parser = RecallQueryParser()

    def execute(self, raw_query: str) -> str:
        """Run /recall and return formatted output (CLI text)."""
        t0 = time.monotonic()
        errors: List[str] = []

        # Parse
        parsed = self._parser.parse(raw_query)
        validation_errors = self._parser.validate(parsed)
        if validation_errors:
            errors.extend(validation_errors)
            # Don't fail on unknown filters — degrade gracefully

        # Trace args
        trace_args = self._parser.to_trace_args(parsed)
        # Compact slash output should stay fast and bounded. The trace reader
        # itself caps at 50, but `/recall` only needs the top cards.
        trace_args["limit"] = 10
        display_query = raw_query.strip() or "(all sessions)"

        # Execute search
        sessions = self._reader.query(**trace_args)

        # Build cards
        cards: List[SessionCard] = []
        for rank, session in enumerate(sessions, 1):
            card = self._build_card(session, parsed.free_text or "", rank)
            cards.append(card)

        elapsed_ms = (time.monotonic() - t0) * 1000
        return RecallFormatter.format_results(cards, display_query, elapsed_ms, errors)

    def _build_card(
        self, session: Dict[str, Any], free_text: str, rank: int
    ) -> SessionCard:
        sid = session.get("id", "")
        ended_at = session.get("ended_at")
        end_reason = session.get("end_reason")
        msg_count = session.get("message_count", 0)
        tool_count = session.get("tool_call_count", 0)

        # Title: use title field or synthesize
        title = session.get("title") or ""
        if not title:
            title = self._synthesize_title(session, free_text)

        # Why matched: use FTS snippet or infer
        why = self._infer_why_matched(session, free_text)

        # Last state
        last_state = self._summarize_last_state(session)

        # Tools
        tools = self._reader.get_tool_names(sid)

        # Evidence refs
        refs = [f"session://{sid}"]
        if session.get("started_at"):
            refs.append(f"session://{sid}?at={session['started_at'][:10]}")

        # End state signal
        if not ended_at:
            state_signal = "active"
        elif end_reason:
            state_signal = f"ended:{end_reason}"
        else:
            state_signal = "completed"

        return SessionCard(
            session_id=sid,
            title=title,
            source=session.get("source", "—"),
            model=session.get("model") or "—",
            started_at=session.get("started_at") or "—",
            ended_at=ended_at,
            end_reason=end_reason,
            message_count=msg_count,
            tool_call_count=tool_count,
            handoff_state=session.get("handoff_state"),
            handoff_platform=session.get("handoff_platform"),
            handoff_error=session.get("handoff_error"),
            why_matched=why,
            last_state=last_state,
            tools=tools,
            evidence_refs=refs,
            rank_score=1.0 / rank if rank > 0 else 0.0,
        )

    def _synthesize_title(self, session: Dict[str, Any], free_text: str) -> str:
        """Synthesize a title when sessions.title is empty."""
        parts = [session.get("source", "").capitalize()]
        model = session.get("model", "")
        if model:
            parts.append(model.split("-")[0].capitalize())
        if free_text:
            parts.append(f'"{free_text[:20]}"')
        return " ".join(parts) or "Untitled session"

    def _infer_why_matched(self, session: Dict[str, Any], free_text: str) -> str:
        """Infer why this session matched the query."""
        signals: List[str] = []
        if free_text:
            signals.append(f'query term: "{free_text[:30]}"')
        source = session.get("source")
        if source:
            signals.append(f"source={source}")
        model = session.get("model", "")
        if model:
            signals.append(f"model={model}")
        tool_count = session.get("tool_call_count", 0)
        if tool_count > 0:
            signals.append(f"{tool_count} tool call(s)")
        msg_count = session.get("message_count", 0)
        if msg_count > 0:
            signals.append(f"{msg_count} message(s)")
        return "; ".join(signals) if signals else "matched"

    def _summarize_last_state(self, session: Dict[str, Any]) -> str:
        """Summarize the last observable state of the session."""
        ended_at = session.get("ended_at")
        end_reason = session.get("end_reason")
        handoff_state = session.get("handoff_state")
        handoff_error = session.get("handoff_error")

        if handoff_error:
            return f"handoff error: {handoff_error[:80]}"
        if handoff_state:
            return f"handoff state={handoff_state}"
        if not ended_at:
            return "session is active"
        if end_reason:
            return f"ended ({end_reason})"
        return "completed normally"

    def format_session_detail(self, session_id: str) -> str:
        """Format detailed view of a single session (for verbose/debug use)."""
        session = self._reader.get_session(session_id)
        if not session:
            return f"Session {session_id} not found."

        messages = self._reader.get_messages(session_id, limit=5)
        tools = self._reader.get_tool_names(session_id)

        lines = [
            f"=== Session: {session_id} ===",
            f"title:    {session.get('title') or '(none)'}",
            f"source:   {session.get('source')}",
            f"model:    {session.get('model') or '?'}",
            f"started:  {session.get('started_at', '?')}",
            f"ended:    {session.get('ended_at') or 'still active'}",
            f"reason:   {session.get('end_reason') or '?'}",
            f"messages: {session.get('message_count', 0)}",
            f"tool_calls: {session.get('tool_call_count', 0)}",
            f"handoff:  {session.get('handoff_state') or 'none'}",
        ]
        if tools:
            lines.append(f"tools:    {', '.join(tools[:10])}")
        if messages:
            lines.append("last messages:")
            for m in messages[-5:]:
                role = m.get("role", "?")
                content = (m.get("content") or "")[:100]
                tool = m.get("tool_name") or ""
                lines.append(f"  [{role}] {tool} {content[:60]}")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# CLI entry point (called from HermesCLI._handle_recall_command)
# ----------------------------------------------------------------------


def run_recall(raw_query: str) -> str:
    """Top-level recall entry point for CLI/gateway."""
    cmd = RecallCommand()
    return cmd.execute(raw_query)


def format_session(session_id: str) -> str:
    """Format a single session in detail (debug/verbose mode)."""
    cmd = RecallCommand()
    return cmd.format_session_detail(session_id)
