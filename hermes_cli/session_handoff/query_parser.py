"""RecallQueryParser — parses /recall query strings with @filter annotations.

Supported filters:
    @today           sessions started today
    @7d              sessions from the last 7 days
    @30d             sessions from the last 30 days
    @source:telegram filter by sessions.source (exact match)
    @tool:terminal   filter by messages.tool_name (substring match)
    @model:gpt-5.5   filter by sessions.model (substring match)
    @status:active   filter by sessions.end_reason / handoff_state
                       values: active, completed, error

Filters are stripped from the free-text query sent to FTS5.
Unknown filters are preserved in the raw query (returned as unknown_filters)
so callers can surface an appropriate error.

Usage:
    parsed = RecallQueryParser.parse("guardian @today @source:telegram")
    reader = SessionTraceReader()
    results = reader.query(
        free_text=parsed.free_text,
        source=parsed.source,
        tool=parsed.tool,
        model=parsed.model,
        status=parsed.status,
        days_back=parsed.days_back,
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# ----------------------------------------------------------------------
# Dataclass returned by parse()
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedRecallQuery:
    """Result of parsing a /recall query string."""

    # FTS5 free-text query after removing all filter tokens.
    free_text: str

    # Applied filters (for caller to use).
    source: Optional[str] = None  # e.g. "telegram"
    tool: Optional[str] = None  # e.g. "terminal"
    model: Optional[str] = None  # e.g. "gpt-5.5"
    status: Optional[str] = None  # e.g. "active"
    days_back: Optional[int] = None  # e.g. 7

    # True if @today was in the query (regardless of other date filters).
    is_today: bool = False

    # Any filter tokens that could not be parsed (for error reporting).
    unknown_filters: Tuple[str, ...] = field(default_factory=tuple)

    # Original query before parsing (for debug/audit).
    raw_query: str = ""


# ----------------------------------------------------------------------
# Regex patterns (compiled once at module load)
# ----------------------------------------------------------------------
# Date shortcuts
_DATE_SHORTCUT_RE = re.compile(r"^@(\d+)d$", re.IGNORECASE)
_DATE_TODAY_RE = re.compile(r"^@today$", re.IGNORECASE)

# All keyed filters look like @key:value. The set of known keys is
# validated after regex matching (to accept any key=value shape and report
# unknown ones). This regex is intentionally permissive.
_KEYED_FILTER_RE = re.compile(r"^@(\w+):(\S+)$", re.IGNORECASE)

# Standalone @word not matching any known pattern
_UNKNOWN_FILTER_RE = re.compile(r"^@(\w+)$", re.IGNORECASE)

# Whitespace strip for final free-text
_WS_RE = re.compile(r"\s+")
# Valid status values
_VALID_STATUSES = frozenset({"active", "completed", "error"})


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------


class RecallQueryParser:
    """Parser for /recall query strings with @filter annotations."""

    @staticmethod
    def parse(query: str) -> ParsedRecallQuery:
        """Parse a raw query string into structured filter + free-text.

        Args:
            query: The full query string, e.g.
                "guardian @today @source:telegram @tool:terminal"

        Returns:
            ParsedRecallQuery with applied filters and stripped free_text.
        """
        raw_query = query
        if not query:
            return ParsedRecallQuery(raw_query="", free_text="")

        parts = query.strip().split()
        free_text_parts: List[str] = []
        source: Optional[str] = None
        tool: Optional[str] = None
        model: Optional[str] = None
        status: Optional[str] = None
        days_back: Optional[int] = None
        is_today = False
        unknown_filters: List[str] = []

        for part in parts:
            # Date shortcuts: @today
            if _DATE_TODAY_RE.match(part):
                is_today = True
                days_back = 0  # Signal: today only (caller maps to <1 day)
                continue

            # Date shortcuts: @7d, @30d
            m = _DATE_SHORTCUT_RE.match(part)
            if m:
                try:
                    days_back = int(m.group(1))
                except ValueError:
                    pass
                else:
                    is_today = False  # explicit range overrides @today
                continue

            # Keyed filters: @source:telegram, @tool:terminal, etc.
            m = _KEYED_FILTER_RE.match(part)
            if m:
                key = m.group(1).lower()
                val = m.group(2).strip()
                if key in ("source", "tool", "model", "status"):
                    if key == "source":
                        source = val
                    elif key == "tool":
                        tool = val
                    elif key == "model":
                        model = val
                    elif key == "status":
                        if val not in _VALID_STATUSES:
                            unknown_filters.append(part)
                        else:
                            status = val
                else:
                    # Unknown keyed filter
                    unknown_filters.append(part)
                continue

            # Unknown filter: @anything_else -> pass through as unknown
            if _UNKNOWN_FILTER_RE.match(part):
                unknown_filters.append(part)
                continue

            # Regular text: free-text for FTS5
            free_text_parts.append(part)

        # Assemble free_text
        free_text = _WS_RE.sub(" ", " ".join(free_text_parts)).strip()

        return ParsedRecallQuery(
            free_text=free_text,
            source=source or None,
            tool=tool or None,
            model=model or None,
            status=status or None,
            days_back=days_back,
            is_today=is_today,
            unknown_filters=tuple(unknown_filters),
            raw_query=raw_query,
        )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @staticmethod
    def validate(parsed: ParsedRecallQuery) -> List[str]:
        """Return a list of validation error strings (empty = valid)."""
        errors: List[str] = []

        if parsed.unknown_filters:
            for f in parsed.unknown_filters:
                errors.append(f"Unknown filter: {f}. Supported: @today, @7d, @30d, @source:X, @tool:X, @model:X, @status:X")

        if parsed.days_back is not None and parsed.days_back < 0:
            errors.append(f"days_back must be non-negative, got {parsed.days_back}")

        if parsed.status is not None and parsed.status not in _VALID_STATUSES:
            errors.append(f"Invalid status '{parsed.status}'. Valid: {', '.join(sorted(_VALID_STATUSES))}")

        return errors

    @staticmethod
    def to_trace_args(parsed: ParsedRecallQuery) -> dict:
        """Map a ParsedRecallQuery to SessionTraceReader.query() kwargs."""
        # Map @today to a days_back of 0 (trace_reader maps to today-only query)
        days_back = parsed.days_back
        # Normalize @today: if is_today but no explicit days_back, use 0
        if parsed.is_today and days_back is None:
            days_back = 0

        return dict(
            free_text=parsed.free_text or None,
            source=parsed.source,
            tool=parsed.tool,
            model=parsed.model,
            status=parsed.status,
            days_back=days_back,
        )