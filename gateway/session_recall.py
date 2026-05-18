"""Ephemeral cross-session recall prompt for fresh gateway sessions.

This module intentionally does not call an LLM and does not persist anything.
It reads the local SessionDB, filters to the same private user, and returns a
small system-prompt block that helps the next agent turn avoid losing recent
context after a gateway session reset/new session.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Optional


_HIDDEN_SESSION_SOURCES = ("tool",)
_MAX_PROMPT_CHARS = 2_800
_MAX_SNIPPET_CHARS = 220

_STOPWORDS = {
    # English
    "about", "after", "again", "also", "because", "before", "could", "from",
    "have", "into", "just", "make", "need", "please", "should", "that", "this",
    "what", "when", "where", "with", "would", "your",
    # Russian common glue words
    "будет", "были", "было", "быть", "вот", "давай", "делать", "если", "есть",
    "зачем", "или", "как", "когда", "который", "меня", "можно", "надо", "нужно",
    "почему", "после", "потом", "просто", "сделай", "тебе", "того", "тоже", "тут",
    "чего", "чем", "через", "чтоб", "чтобы", "это", "этот",
}

_COMMITMENT_RE = re.compile(
    r"(?i)("
    r"\b(?:todo|next step|follow[- ]?up|pending|blocked|blocker|promise[sd]?|commitment|need to|will)\b"
    r"|обещ\w*|договор\w*|следующ\w* шаг|открыт\w* обещ|"
    r"сдела(?:ю|ем|ть)|почин(?:ю|им|ить)|додела\w*|верн(?:усь|емся)|"
    r"остал(?:ось|ся)|блокер\w*|нужно будет|надо будет"
    r")"
)

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_./-]{4,}")
_TAG_RE = re.compile(r">>>|<<<")
_WS_RE = re.compile(r"\s+")


def cross_session_recall_enabled(config: Optional[dict[str, Any]]) -> bool:
    """Return whether gateway cross-session recall preflight is enabled.

    Default is enabled. Operators can disable with either:
      cross_session_recall: false
      gateway.cross_session_recall.enabled: false
    """
    if not isinstance(config, dict):
        return True
    raw: Any = config.get("cross_session_recall")
    gateway_cfg = config.get("gateway")
    if isinstance(gateway_cfg, dict) and "cross_session_recall" in gateway_cfg:
        raw = gateway_cfg.get("cross_session_recall")
    if isinstance(raw, dict):
        raw = raw.get("enabled", True)
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def build_cross_session_recall_prompt(
    *,
    db: Any,
    source: Any,
    current_session_id: str,
    user_message: str,
    max_relevant_sessions: int = 3,
    max_recent_sessions: int = 2,
    max_commitments: int = 5,
) -> Optional[str]:
    """Build a private system-context block with same-user prior-session hints.

    Privacy boundary: only private one-on-one gateway sessions with a stable
    user_id are eligible. Shared groups/channels/threads are deliberately
    skipped so private recall cannot leak into a public/shared context.
    """
    if db is None or not _eligible_private_source(source):
        return None

    platform = _platform_value(getattr(source, "platform", None))
    user_id = str(getattr(source, "user_id", "") or "")
    current_session_id = str(current_session_id or "")

    relevant = _find_relevant_sessions(
        db=db,
        platform=platform,
        user_id=user_id,
        current_session_id=current_session_id,
        user_message=user_message,
        limit=max_relevant_sessions,
    )
    recent = _find_recent_sessions(
        db=db,
        platform=platform,
        user_id=user_id,
        current_session_id=current_session_id,
        exclude_ids={item["session_id"] for item in relevant},
        limit=max_recent_sessions,
    )
    commitments = _find_candidate_commitments(
        db=db,
        session_ids=[item["session_id"] for item in relevant + recent],
        limit=max_commitments,
    )

    if not relevant and not recent and not commitments:
        return None

    lines = [
        "## Cross-session recall preflight",
        "",
        "Private system context assembled from previous Hermes sessions for this same one-on-one user. Use it only to avoid losing context; do not present it as a separate observer layer. The current user message wins if there is any conflict.",
    ]

    if relevant:
        lines.extend(["", "Potentially relevant recent sessions:"])
        for item in relevant:
            lines.append(_format_session_item(item))

    if recent:
        lines.extend(["", "Other recent same-user sessions:"])
        for item in recent:
            lines.append(_format_session_item(item))

    if commitments:
        lines.extend(["", "Candidate open promises / follow-ups to verify before relying on:"])
        for item in commitments:
            lines.append(
                f"- {item['date']} | {item['role']}: {_clip(item['text'], _MAX_SNIPPET_CHARS)}"
            )

    return _clip("\n".join(lines), _MAX_PROMPT_CHARS)


def _eligible_private_source(source: Any) -> bool:
    if source is None:
        return False
    if bool(getattr(source, "external_safe_mode", False)):
        return False
    if str(getattr(source, "chat_type", "") or "").lower() != "dm":
        return False
    if not str(getattr(source, "user_id", "") or "").strip():
        return False
    platform = _platform_value(getattr(source, "platform", None))
    return bool(platform and platform != "local")


def _platform_value(platform: Any) -> str:
    return str(getattr(platform, "value", platform) or "").strip().lower()


def _find_relevant_sessions(
    *,
    db: Any,
    platform: str,
    user_id: str,
    current_session_id: str,
    user_message: str,
    limit: int,
) -> list[dict[str, str]]:
    query = _query_from_message(user_message)
    if not query:
        return []
    try:
        matches = db.search_messages(
            query=query,
            source_filter=[platform],
            exclude_sources=list(_HIDDEN_SESSION_SOURCES),
            role_filter=["user", "assistant"],
            limit=40,
            offset=0,
        )
    except Exception:
        return []

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in matches or []:
        sid = str(match.get("session_id") or "")
        if not sid or sid == current_session_id or sid in seen:
            continue
        meta = _safe_get_session(db, sid)
        if not _same_private_user(meta, platform=platform, user_id=user_id):
            continue
        seen.add(sid)
        results.append(_session_item_from_meta(meta, match=match))
        if len(results) >= limit:
            break
    return results


def _find_recent_sessions(
    *,
    db: Any,
    platform: str,
    user_id: str,
    current_session_id: str,
    exclude_ids: set[str],
    limit: int,
) -> list[dict[str, str]]:
    try:
        sessions = db.list_sessions_rich(
            source=platform,
            exclude_sources=list(_HIDDEN_SESSION_SOURCES),
            limit=20,
            order_by_last_active=True,
        )
    except Exception:
        return []

    results: list[dict[str, str]] = []
    for meta in sessions or []:
        sid = str(meta.get("id") or "")
        if not sid or sid == current_session_id or sid in exclude_ids:
            continue
        if not _same_private_user(meta, platform=platform, user_id=user_id):
            continue
        results.append(_session_item_from_meta(meta))
        if len(results) >= limit:
            break
    return results


def _find_candidate_commitments(*, db: Any, session_ids: Iterable[str], limit: int) -> list[dict[str, str]]:
    commitments: list[dict[str, str]] = []
    seen_text: set[str] = set()
    for sid in session_ids:
        if len(commitments) >= limit:
            break
        meta = _safe_get_session(db, sid)
        date = _format_timestamp(meta.get("last_active") or meta.get("started_at") if meta else None)
        try:
            messages = db.get_messages_as_conversation(sid)
        except Exception:
            continue
        for msg in list(messages or [])[-16:]:
            if len(commitments) >= limit:
                break
            role = str(msg.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            text = _clean_text(msg.get("content"))
            if not text or not _COMMITMENT_RE.search(text):
                continue
            key = text.lower()[:180]
            if key in seen_text:
                continue
            seen_text.add(key)
            commitments.append({"date": date, "role": role, "text": text})
    return commitments


def _same_private_user(meta: Optional[dict[str, Any]], *, platform: str, user_id: str) -> bool:
    if not meta:
        return False
    return (
        str(meta.get("source") or "").lower() == platform
        and str(meta.get("user_id") or "") == user_id
    )


def _safe_get_session(db: Any, session_id: str) -> Optional[dict[str, Any]]:
    try:
        return db.get_session(session_id)
    except Exception:
        return None


def _session_item_from_meta(meta: dict[str, Any], *, match: Optional[dict[str, Any]] = None) -> dict[str, str]:
    sid = str(meta.get("id") or (match or {}).get("session_id") or "")
    title = _clean_text(meta.get("title")) or "untitled"
    preview = _clean_text((match or {}).get("snippet")) or _clean_text(meta.get("preview"))
    return {
        "session_id": sid,
        "date": _format_timestamp(meta.get("last_active") or meta.get("started_at") or (match or {}).get("session_started")),
        "title": title,
        "preview": preview,
    }


def _format_session_item(item: dict[str, str]) -> str:
    sid = item.get("session_id", "")[:12]
    preview = item.get("preview") or "no preview"
    return f"- {item.get('date', 'unknown')} | {sid} | {item.get('title', 'untitled')} — {_clip(preview, _MAX_SNIPPET_CHARS)}"


def _query_from_message(message: str) -> str:
    text = _clean_text(message).lower()
    if not text:
        return ""
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(text):
        clean = token.strip("._-/").lower()
        if len(clean) < 4 or clean in _STOPWORDS:
            continue
        if clean not in tokens:
            tokens.append(clean)
        if len(tokens) >= 8:
            break
    return " OR ".join(tokens)


def _format_timestamp(value: Any) -> str:
    if value in (None, ""):
        return "unknown"
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d")
        text = str(value)
        if text.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(text)).strftime("%Y-%m-%d")
        return text[:10]
    except Exception:
        return str(value)[:10]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = _TAG_RE.sub("", value)
    return _WS_RE.sub(" ", value).strip()


def _clip(text: str, limit: int) -> str:
    text = _clean_text(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
