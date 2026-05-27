"""Session Trace Reader — profile-aware Hermes state.db reader.

Exposes session/messages data with strict limits for /recall and /handoff.
No raw transcript dumps, no writes, no mem0g touch.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes_constants import get_hermes_home

# Fields returned per session — no raw transcript.
_SESSION_SUMMARY_FIELDS = (
    "id",
    "source",
    "user_id",
    "model",
    "title",
    "started_at",
    "ended_at",
    "end_reason",
    "message_count",
    "tool_call_count",
    "handoff_state",
    "handoff_platform",
    "handoff_error",
)

# Fields returned per message — no raw content, no reasoning fields.
_MESSAGE_SUMMARY_FIELDS = (
    "id",
    "session_id",
    "role",
    "content",  # content preview only (caller truncates)
    "tool_name",
    "timestamp",
    "finish_reason",
)

_MAX_SESSIONS = 50
_MAX_MESSAGES_PER_SESSION = 100


class SessionTraceReader:
    """Read-only session trace access.

    All methods are thread-safe. Profile-aware: reads from the active
    Hermes profile's state.db via ``get_hermes_home()``.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path: Path = db_path or (get_hermes_home() / "state.db")
        self._lock = threading.Lock()

    # -------------------------------------------------------------------------
    # Query primitives
    # -------------------------------------------------------------------------

    def query(
        self,
        *,
        free_text: Optional[str] = None,
        source: Optional[str] = None,
        tool: Optional[str] = None,
        model: Optional[str] = None,
        status: Optional[str] = None,
        days_back: Optional[int] = None,
        limit: int = _MAX_SESSIONS,
    ) -> List[Dict[str, Any]]:
        """Search sessions and return summary records.

        Args:
            free_text: FTS5 full-text query across message content/tool_name.
            source: Exact match on sessions.source (e.g. "telegram", "cli").
            tool: Substring match on messages.tool_name.
            model: Substring match on sessions.model.
            status: Match on sessions.end_reason / handoff_state.
                Supported values: "active" (no ended_at), "completed", "error".
            days_back: If set, restrict to sessions started within N days.
            limit: Maximum sessions to return (capped at 50).

        Returns:
            List of session summary dicts ordered by started_at DESC.
            Each dict has all _SESSION_SUMMARY_FIELDS plus a "matched_content"
            snippet when free_text search matched.
        """
        limit = min(max(1, limit), _MAX_SESSIONS)

        sql, params = self._build_query_sql(
            free_text=free_text,
            source=source,
            tool=tool,
            model=model,
            status=status,
            days_back=days_back,
            limit=limit,
        )

        with self._lock:
            try:
                conn = sqlite3.connect(
                    str(self._db_path),
                    check_same_thread=False,
                    timeout=5.0,
                )
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
                conn.close()
            except sqlite3.Error:
                return []

        results = []
        for row in rows:
            d = dict(row)
            d.pop("_matched_snippet", None)
            # Format timestamps
            for ts_field in ("started_at", "ended_at"):
                if ts_field in d and d[ts_field]:
                    try:
                        d[ts_field] = datetime.fromtimestamp(
                            d[ts_field], tz=timezone.utc
                        ).isoformat()
                    except (OSError, ValueError):
                        pass
            # Truncate content if present (preview only)
            if "content" in d and d["content"]:
                d["content"] = str(d["content"])[:200]
            results.append(d)

        return results

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return a single session summary or None."""
        with self._lock:
            try:
                conn = sqlite3.connect(
                    str(self._db_path),
                    check_same_thread=False,
                    timeout=5.0,
                )
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    f"SELECT {','.join(_SESSION_SUMMARY_FIELDS)} FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                conn.close()
            except sqlite3.Error:
                return None

        if not row:
            return None
        d = dict(row)
        for ts_field in ("started_at", "ended_at"):
            if ts_field in d and d[ts_field]:
                try:
                    d[ts_field] = datetime.fromtimestamp(
                        d[ts_field], tz=timezone.utc
                    ).isoformat()
                except (OSError, ValueError):
                    pass
        return d

    def get_messages(
        self, session_id: str, limit: int = _MAX_MESSAGES_PER_SESSION
    ) -> List[Dict[str, Any]]:
        """Return message summaries for a session (oldest-first).

        Truncates content at 200 chars.
        """
        limit = min(max(1, limit), _MAX_MESSAGES_PER_SESSION)
        with self._lock:
            try:
                conn = sqlite3.connect(
                    str(self._db_path),
                    check_same_thread=False,
                    timeout=5.0,
                )
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"""SELECT {','.join(_MESSAGE_SUMMARY_FIELDS)}
                        FROM messages
                        WHERE session_id = ?
                        ORDER BY timestamp ASC
                        LIMIT ?""",
                    (session_id, limit),
                ).fetchall()
                conn.close()
            except sqlite3.Error:
                return []

        results = []
        for row in rows:
            d = dict(row)
            if d.get("content"):
                d["content"] = str(d["content"])[:200]
            if d.get("timestamp"):
                try:
                    d["timestamp"] = datetime.fromtimestamp(
                        d["timestamp"], tz=timezone.utc
                    ).isoformat()
                except (OSError, ValueError):
                    pass
            results.append(d)
        return results

    def get_tool_names(self, session_id: str) -> List[str]:
        """Return distinct tool_name values used in a session."""
        with self._lock:
            try:
                conn = sqlite3.connect(
                    str(self._db_path),
                    check_same_thread=False,
                    timeout=5.0,
                )
                rows = conn.execute(
                    "SELECT DISTINCT tool_name FROM messages "
                    "WHERE session_id = ? AND tool_name IS NOT NULL AND tool_name != ''",
                    (session_id,),
                ).fetchall()
                conn.close()
            except sqlite3.Error:
                return []
        return [r[0] for r in rows if r[0]]

    # -------------------------------------------------------------------------
    # SQL builder (kept private)
    # -------------------------------------------------------------------------

    def _build_query_sql(
        self,
        *,
        free_text: Optional[str],
        source: Optional[str],
        tool: Optional[str],
        model: Optional[str],
        status: Optional[str],
        days_back: Optional[int],
        limit: int,
    ) -> Tuple[str, list]:
        """Build and return (sql, params) for the query."""
        fields = ", ".join(_SESSION_SUMMARY_FIELDS)
        has_fts = bool(free_text and free_text.strip())
        has_tool = bool(tool and tool.strip())

        params: list = []

        if has_fts:
            fields += ", snippet(messages_fts, 0, '>>>', '<<<', '...', 40) AS _matched_snippet"

        # Sanitize FTS term early so it's available for both branches
        sanitized = _sanitize_fts5(free_text.strip()) if has_fts else ""

        # Fast path for free-text-only searches: ask FTS for matching message
        # rowids first, then filter sessions by those ids. The previous
        # sessions×messages join + correlated EXISTS was painfully slow on a
        # real state.db with large active Telegram sessions.
        if has_fts and not has_tool:
            tables = "sessions s"
            select_fields = "s." + ", s.".join(_SESSION_SUMMARY_FIELDS)
            group_cols = ""
            where_parts = [
                "s.id IN ("
                "SELECT DISTINCT m.session_id "
                "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
                "WHERE messages_fts MATCH ?"
                ")"
            ]
            params.append(sanitized)
        # Join messages for tool filter, and for the rarer combined tool+FTS case.
        elif has_fts or has_tool:
            tables = "sessions s JOIN messages m ON m.session_id = s.id"
            group_cols = "s." + ", s.".join(_SESSION_SUMMARY_FIELDS)
            select_fields = "s." + ", s.".join(_SESSION_SUMMARY_FIELDS)
            if has_fts:
                # snippet() with a correlated FTS subquery: SQLite resolves
                # messages_fts via rowid=m.id without placing the FTS virtual
                # table on the right side of a JOIN.
                select_fields += (
                    ", (SELECT snippet(messages_fts, 0, '>>>', '<<<', '...', 40) "
                    "FROM messages_fts WHERE messages_fts.rowid = m.id "
                    "AND messages_fts MATCH ?) AS _matched_snippet"
                )
                params.append(sanitized)
            where_parts: list = []
            if has_tool:
                assert tool and tool.strip()
                where_parts.append("m.tool_name LIKE ?")
                params.append(f"%{tool.strip()}%")
        else:
            tables = "sessions s"
            select_fields = fields
            group_cols = fields
            where_parts = []

        where_clauses = list(where_parts)

        if has_fts and sanitized and has_tool:
            # Combined tool+FTS branch still joins messages as `m`; the
            # free-text-only fast path already added an FTS subquery above.
            where_clauses.append(
                "EXISTS (SELECT 1 FROM messages_fts "
                "WHERE messages_fts.rowid = m.id AND messages_fts MATCH ?)"
            )
            params.append(sanitized)

        if source and source.strip():
            where_clauses.append("s.source = ?")
            params.append(source.strip())

        if model and model.strip():
            where_clauses.append("s.model LIKE ?")
            params.append(f"%{model.strip()}%")

        if status and status.strip():
            raw_status = status.strip().lower()
            if raw_status == "active":
                where_clauses.append("s.ended_at IS NULL")
            elif raw_status == "completed":
                where_clauses.append("s.ended_at IS NOT NULL AND (s.handoff_state IS NULL OR s.handoff_state = 'completed')")
            elif raw_status == "error":
                where_clauses.append(
                    "s.ended_at IS NOT NULL AND "
                    "(s.handoff_state = 'error' OR s.end_reason = 'error' OR s.handoff_error IS NOT NULL)"
                )

        if days_back is not None and days_back >= 0:
            if days_back == 0:
                # Today only: started_at >= start of today (UTC)
                today_start = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                cutoff_ts = today_start.timestamp()
            else:
                cutoff_ts = datetime.now(timezone.utc).timestamp() - days_back * 86400
            where_clauses.append("s.started_at >= ?")
            params.append(cutoff_ts)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        if has_tool:
            group_sql = f"GROUP BY {group_cols}"
        else:
            group_sql = ""

        order_sql = "ORDER BY s.started_at DESC"
        limit_sql = f"LIMIT {limit}"

        sql = f"SELECT {select_fields} FROM {tables} {where_sql} {group_sql} {order_sql} {limit_sql}"
        return sql, params


def _sanitize_fts5(query: str) -> str:
    """Basic FTS5 query sanitization: strip dangerous operators."""
    if not query:
        return ""
    # Remove double-quote traps and control chars; allow basic FTS5 syntax.
    q = query.replace('"', ' ').replace("'", " ").replace("\x00", "")
    return q.strip()