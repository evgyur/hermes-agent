"""HandoffExtractor — builds grounded handoff dict from top sessions.

Acceptance: /handoff returns Context, Decisions, Evidence refs, Files,
Commands, Blockers, Next steps, Resume prompt; no raw transcript dumps.

Architecture notes:
- Operates on SessionTraceReader results (session summaries + message previews).
- Does NOT write artifacts — only returns a structured HandoffResult dict.
- Privacy: content preview capped at 200 chars by trace_reader; this module
  adds no raw payload access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from hermes_cli.session_handoff.trace_reader import SessionTraceReader


# ------------------------------------------------------------------
# Output dataclass
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HandoffResult:
    """Structured handoff returned by HandoffExtractor.extract()."""

    topic: str
    # Human-readable context summary
    context: str
    # List of decisions made during the session
    decisions: List[str] = field(default_factory=list)
    # Evidence refs: session://<id>, file:///<path>
    evidence_refs: List[str] = field(default_factory=list)
    # Files referenced or modified
    files: List[str] = field(default_factory=list)
    # Terminal/CLI commands executed (not their output)
    commands: List[str] = field(default_factory=list)
    # Blocker descriptions
    blockers: List[str] = field(default_factory=list)
    # Ordered next steps
    next_steps: List[str] = field(default_factory=list)
    # Readable prompt that resumes the task in a fresh session
    resume_prompt: str = ""
    # Confidence 0-1 that the handoff is grounded and usable
    confidence: float = 0.0
    # Staleness note
    staleness: str = ""
    # Which sessions were consulted
    session_ids: List[str] = field(default_factory=list)
    # How many messages were inspected per session (for grounding)
    messages_inspected: int = 0


# ------------------------------------------------------------------
# Internals
# ------------------------------------------------------------------

# Patterns that look like terminal commands in message content.
_COMMAND_PATTERNS_COMPILED = [
    re.compile(r"^[\$#>]\s*(.+)$", re.MULTILINE),
    re.compile(
        r"\b(hermes|cd|mkdir|chmod|python3|python|pip|git|ssh|curl|rm|cp|mv|touch|patch|terminal|write_file)\s+\S+",
        re.IGNORECASE,
    ),
]

# Patterns that look like file paths.
_FILE_PATTERNS = re.compile(
    r"(?<![\w.-])(?:~|/|\./|\.\./)[^\s`'\"<>|]{1,180}\.[a-zA-Z0-9]{1,20}\b"
)

# Patterns that signal a blocker.
_BLOCKER_SIGNALS = (
    re.compile(r"\b(fail|block|error|timeout|stuck|waiting|no idea|can't proceed|cannot continue)\b", re.IGNORECASE),
    re.compile(r"\b(need .* from|waiting on|pending .* approval|waiting for)\b", re.IGNORECASE),
)

# Patterns that signal a completed decision.
_DECISION_SIGNALS = (
    re.compile(r"\b(decided|chose|selected|agreed|concluded|settled on|went with|opted for)\b", re.IGNORECASE),
    re.compile(r"\b(using |switched to |moved to |replaced |instead of )\b", re.IGNORECASE),
)

# Patterns that signal next steps.
_NEXT_STEP_SIGNALS = (
    re.compile(r"\b(next|then|after that|once done|when ready|下一步|следующий)\b", re.IGNORECASE),
    re.compile(r"\b(need to|should|must|will|going to|plan to)\s+(do|check|verify|run|test|implement)\b", re.IGNORECASE),
)


class HandoffExtractor:
    """Build a grounded HandoffResult from a list of sessions.

    All methods are pure / read-only. Does NOT write artifacts.
    """

    def __init__(self, reader: SessionTraceReader) -> None:
        self._reader = reader
        self._tool_name_cache: Dict[str, List[str]] = {}

    def extract(
        self,
        sessions: List[Dict[str, Any]],
        topic: str,
        *,
        max_messages_per_session: int = 20,
        confidence_threshold: float = 0.3,
    ) -> HandoffResult:
        """Build a HandoffResult from the top-ranked sessions.

        Args:
            sessions: List of session summary dicts from SessionTraceReader.query().
            topic: The original handoff topic string.
            max_messages_per_session: How many message summaries to inspect per session.
            confidence_threshold: Minimum confidence to consider the handoff grounded.

        Returns:
            HandoffResult with all fields populated from session evidence.
        """
        if not sessions:
            return self._empty_result(topic)

        top = sessions[:3]  # Top 3 sessions for grounding
        session_ids = [s.get("id", "") for s in top]
        self._tool_name_cache.clear()

        # Gather tool names for each session.
        for sid in session_ids:
            if sid:
                tools = self._reader.get_tool_names(sid)
                self._tool_name_cache[sid] = tools

        # Collect evidence refs.
        refs: List[str] = []
        for s in top:
            sid = s.get("id", "")
            if sid:
                refs.append(f"session://{sid}")
                started = s.get("started_at", "")
                if started:
                    refs.append(f"session://{sid}?at={started[:10]}")

        # Context: build from session metadata.
        context_parts = self._build_context(top, topic)
        context = context_parts or f"Topic: {topic}"

        # Decisions from messages.
        decisions = self._extract_decisions(top, max_messages_per_session)

        # Files mentioned.
        files = self._extract_files(top, max_messages_per_session)

        # Commands.
        commands = self._extract_commands(top, max_messages_per_session)

        # Add tools as implicit commands.
        for sid, tools in self._tool_name_cache.items():
            for t in tools[:5]:
                if t and t not in ("terminal", "read_file", "write_file", "patch"):
                    commands.append(f"[tool:{t}]")

        # Blockers.
        blockers = self._extract_blockers(top, max_messages_per_session)

        # Next steps.
        next_steps = self._extract_next_steps(top, max_messages_per_session)

        # Resume prompt.
        resume_prompt = self._build_resume_prompt(topic, context, next_steps, blockers)

        # Confidence.
        confidence = self._score_confidence(top, decisions, next_steps, blockers)

        # Staleness.
        staleness = self._assess_staleness(top)

        return HandoffResult(
            topic=topic,
            context=context,
            decisions=decisions,
            evidence_refs=refs,
            files=list(dict.fromkeys(files)),  # dedupe preserve order
            commands=commands,
            blockers=blockers,
            next_steps=next_steps,
            resume_prompt=resume_prompt,
            confidence=confidence,
            staleness=staleness,
            session_ids=session_ids,
            messages_inspected=len(top) * max_messages_per_session,
        )

    def _empty_result(self, topic: str) -> HandoffResult:
        return HandoffResult(
            topic=topic,
            context=f"No prior sessions found for topic: {topic}",
            confidence=0.0,
            staleness="no sessions found",
        )

    # ------------------------------------------------------------------
    # Private extractors
    # ------------------------------------------------------------------

    def _build_context(self, sessions: List[Dict[str, Any]], topic: str) -> str:
        parts = [f"Topic: {topic}"]
        for s in sessions:
            sid = s.get("id", "?")
            title = s.get("title") or "(no title)"
            model = s.get("model") or "?"
            source = s.get("source") or "?"
            started = (s.get("started_at") or "?")[:10]
            msg_count = s.get("message_count", 0)
            parts.append(
                f"- [{source}/{model}] {title} (started {started}, {msg_count} msgs)"
            )
        return "\n".join(parts)

    def _extract_text_snippets(
        self, sessions: List[Dict[str, Any]], max_per_session: int
    ) -> List[Tuple[str, str]]:
        """Return [(session_id, content_preview)] pairs for analysis."""
        snippets: List[Tuple[str, str]] = []
        for s in sessions:
            sid = s.get("id", "")
            if not sid:
                continue
            messages = self._reader.get_messages(sid, limit=max_per_session)
            # Join truncated content previews with a separator.
            texts: List[str] = []
            for m in messages:
                txt = m.get("content") or ""
                if txt:
                    texts.append(txt)
            combined = " | ".join(texts)
            snippets.append((sid, combined))
        return snippets

    def _extract_decisions(
        self, sessions: List[Dict[str, Any]], max_per_session: int
    ) -> List[str]:
        findings: List[str] = []
        seen = set()
        for sid, text in self._extract_text_snippets(sessions, max_per_session):
            for signal in _DECISION_SIGNALS:
                for m in signal.finditer(text):
                    excerpt = text[m.start() : m.start() + 120].strip()
                    if excerpt not in seen:
                        seen.add(excerpt)
                        findings.append(excerpt)
                    if len(findings) >= 5:
                        break
            if len(findings) >= 5:
                break
        return findings

    def _extract_files(
        self, sessions: List[Dict[str, Any]], max_per_session: int
    ) -> List[str]:
        findings: List[str] = []
        seen = set()
        for sid, text in self._extract_text_snippets(sessions, max_per_session):
            for m in _FILE_PATTERNS.finditer(text):
                path = m.group()
                if (
                    path not in seen
                    and "/" in path
                    and not path.startswith("#")
                    and len(path) > 3
                ):
                    seen.add(path)
                    findings.append(path)
                if len(findings) >= 10:
                    break
            if len(findings) >= 10:
                break
        return findings

    def _extract_commands(
        self, sessions: List[Dict[str, Any]], max_per_session: int
    ) -> List[str]:
        findings: List[str] = []
        seen = set()
        for sid, text in self._extract_text_snippets(sessions, max_per_session):
            for pattern in _COMMAND_PATTERNS_COMPILED:
                for m in pattern.finditer(text):
                    cmd = m.group(1) if m.lastindex else m.group()
                    if cmd and cmd not in seen and len(cmd) < 200:
                        seen.add(cmd)
                        findings.append(cmd)
                    if len(findings) >= 10:
                        break
            if len(findings) >= 10:
                break
        return findings

    def _extract_blockers(
        self, sessions: List[Dict[str, Any]], max_per_session: int
    ) -> List[str]:
        findings: List[str] = []
        seen = set()
        for sid, text in self._extract_text_snippets(sessions, max_per_session):
            for pattern in _BLOCKER_SIGNALS:
                for m in pattern.finditer(text):
                    excerpt = text[m.start() : m.start() + 120].strip()
                    if excerpt not in seen:
                        seen.add(excerpt)
                        findings.append(excerpt)
                    if len(findings) >= 5:
                        break
            if len(findings) >= 5:
                break
        return findings

    def _extract_next_steps(
        self, sessions: List[Dict[str, Any]], max_per_session: int
    ) -> List[str]:
        findings: List[str] = []
        seen = set()
        for sid, text in self._extract_text_snippets(sessions, max_per_session):
            for pattern in _NEXT_STEP_SIGNALS:
                for m in pattern.finditer(text):
                    excerpt = text[m.start() : m.start() + 120].strip()
                    if excerpt not in seen:
                        seen.add(excerpt)
                        findings.append(excerpt)
                    if len(findings) >= 5:
                        break
            if len(findings) >= 5:
                break
        return findings

    def _build_resume_prompt(
        self,
        topic: str,
        context: str,
        next_steps: List[str],
        blockers: List[str],
    ) -> str:
        parts = [
            f"# Resume: {topic}",
            "",
            f"{context}",
            "",
        ]
        if next_steps:
            parts.append("## Next steps")
            for i, step in enumerate(next_steps, 1):
                parts.append(f"{i}. {step}")
            parts.append("")
        if blockers:
            parts.append("## Blockers")
            for b in blockers:
                parts.append(f"- {b}")
            parts.append("")
        parts.append(
            "Start from the last completed step. "
            "Do not repeat work already done unless explicitly necessary."
        )
        return "\n".join(parts)

    def _score_confidence(
        self,
        sessions: List[Dict[str, Any]],
        decisions: List[str],
        next_steps: List[str],
        blockers: List[str],
    ) -> float:
        score = 0.3  # base: sessions found
        if len(sessions) >= 2:
            score += 0.1
        if decisions:
            score += 0.15
        if next_steps:
            score += 0.15
        if blockers:
            score -= 0.1  # blockers reduce confidence slightly
        total_messages = sum(s.get("message_count", 0) for s in sessions)
        if total_messages > 20:
            score += 0.15
        return min(max(score, 0.0), 1.0)

    def _assess_staleness(self, sessions: List[Dict[str, Any]]) -> str:
        if not sessions:
            return "no sessions"
        now = 0.0  # timezone utc
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).timestamp()
        except Exception:
            pass
        latest_start = 0.0
        for s in sessions:
            started = s.get("started_at", "")
            if started:
                try:
                    from datetime import datetime, timezone
                    ts = datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
                    if ts > latest_start:
                        latest_start = ts
                except Exception:
                    pass
        if latest_start == 0.0:
            return "unknown staleness"
        age_days = (now - latest_start) / 86400
        if age_days < 1:
            return "fresh (< 1 day)"
        elif age_days < 7:
            return f"recent ({age_days:.1f} days old)"
        elif age_days < 30:
            return f"stale ({age_days:.0f} days old)"
        else:
            return f"very stale ({age_days:.0f} days old)"
