"""Structured completion policies for persistent goals.

This module holds deterministic pre-judge guards for goals that declare a
machine-readable completion contract.  GoalManager remains the engine for
persistence, turn budgets, continuation prompts, and auxiliary judge calls;
this module only answers: "does the latest response already prove continue,
done, or blocked before the LLM judge is consulted?"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuredCompletionDecision:
    """A deterministic completion verdict that bypasses the auxiliary judge."""

    verdict: str  # "done" | "continue"
    reason: str


def _non_fenced_lines(text: str) -> List[str]:
    """Return lines outside markdown fences.

    Completion markers are transcript blocks, not examples.  A handoff message
    may include marker names inside a fallback command or code sample; those
    mentions must not satisfy the `/goal` judge.
    """
    lines: List[str] = []
    in_fence = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def _normalize_marker_line(line: str) -> str:
    """Normalize markdown transcript syntax around an intentional marker line."""
    cleaned = str(line or "").strip()
    # Allow markers rendered as headings, blockquotes, or list/task bullets:
    # `## AUDIT_COMPLETE`, `> AUDIT_COMPLETE`, `- [x] AUDIT_COMPLETE`.
    cleaned = re.sub(r"^(?:>\s*)+", "", cleaned)
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned)
    cleaned = re.sub(r"^(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?", "", cleaned)
    cleaned = cleaned.strip().strip("`*_ ")
    return cleaned.rstrip(':.,;!?)"]}').upper()


def has_standalone_marker(text: str, marker: str) -> bool:
    """Return True only when ``marker`` appears as its own transcript line."""
    target = marker.strip().upper()
    if not target:
        return False
    for line in _non_fenced_lines(text):
        if _normalize_marker_line(line) == target:
            return True
    return False


def has_standalone_marker_prefix(text: str, marker: str) -> bool:
    """Return True when a non-fenced line starts with ``marker``.

    Blocker markers often carry a short reason (for example
    ``BLOCKED_BY_APPROVAL — waiting for DNS approval``). Treat those as
    intentional transcript markers, while still rejecting prose mentions and
    fenced examples.
    """
    target = marker.strip().upper()
    if not target:
        return False
    pattern = re.compile(rf"^{re.escape(target)}\b", re.IGNORECASE)
    for line in _non_fenced_lines(text):
        if pattern.match(_normalize_marker_line(line)):
            return True
    return False


def _looks_like_structured_completion_goal(goal: str) -> bool:
    text = str(goal or "")
    upper = text.upper()
    return (
        "SUPERGOAL_RUN_COMPLETE" in upper
        or "SUPERGOAL_PHASE" in upper
        or ".supergoal/" in text.lower()
        or ("PROTOCOL.MD" in upper and "ROADMAP.MD" in upper)
    )


def _candidate_state_roots(goal: str) -> List[Path]:
    """Return plausible roots containing a `.supergoal/STATE.md` file.

    A stale wrapper may contain malformed paths such as
    ``<root>/.supergoal/AUDIT_HANDOFF.md/PROTOCOL.md``.  Normalize those back
    to ``<root>``.  Order matters: the first root with a STATE file is treated
    as canonical for this goal, so a new active root is not marked done merely
    because the prompt also mentions an older completed rail.
    """
    text = str(goal or "")
    roots: List[Path] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        raw = (raw or "").strip().strip("`'\"<>").rstrip(".,;)]")
        if not raw:
            return
        if raw == ".supergoal" or raw.startswith(".supergoal/"):
            raw = str(Path.cwd())
        elif not raw.startswith("/"):
            return
        elif "/.supergoal/" in raw:
            raw = raw.split("/.supergoal/", 1)[0]
        elif raw.endswith("/.supergoal"):
            raw = raw[: -len("/.supergoal")]
        path = Path(raw)
        key = str(path)
        if key not in seen:
            seen.add(key)
            roots.append(path)

    # Prefer explicit root wording when present.
    for m in re.finditer(
        r"(?:project\s+root|supergoal\s+root|root)\s*[:=]?\s*`([^`]+)`",
        text,
        re.IGNORECASE,
    ):
        add(m.group(1))

    # Then all backticked absolute paths.
    for m in re.finditer(r"`(/[^`]+)`", text):
        add(m.group(1))

    # Finally bare absolute paths that include .supergoal.
    for m in re.finditer(r"(/\S*\.supergoal/\S+)", text):
        add(m.group(1))

    # Relative .supergoal paths are only useful when the goal does not name an
    # explicit absolute project/root path. Otherwise a rendered instruction like
    # "Implement `.supergoal/ROADMAP.md` in `/tmp/current`" would accidentally
    # bind the disk-completion probe to the gateway process cwd.
    if not roots:
        for m in re.finditer(r"`(\.supergoal(?:/[^`]+)?)`", text):
            add(m.group(1))
        for m in re.finditer(r"(\.supergoal/\S+)", text):
            add(m.group(1))

    return roots


def _state_file_completion_reason(goal: str) -> Optional[str]:
    """Detect already-complete structured goals from disk before the judge.

    If the canonical root's ``.supergoal/STATE.md`` already records terminal
    completion, continuing the same /goal is a control-plane bug.  Marking it
    done here prevents repeated synthetic continuation turns from reaching the
    LLM and spamming completion/status text.
    """
    def _direct_state_paths(allow_relative: bool) -> List[Path]:
        paths: List[Path] = []
        seen: set[str] = set()
        for match in re.finditer(r"`?(/\S*\.supergoal/\S*STATE\.md|\.supergoal/\S*STATE\.md)`?", str(goal or "")):
            raw = match.group(1).strip().strip("`'\"<>").rstrip(".,;)]")
            if not allow_relative and raw.startswith(".supergoal/"):
                continue
            path = Path(raw) if raw.startswith("/") else Path.cwd() / raw
            key = str(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)
        return paths

    def _completion_reason_for_state(state_path: Path, label_root: Path) -> Optional[str]:
        if not state_path.exists() or not state_path.is_file():
            return None
        try:
            state_text = state_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.debug("structured completion check: could not read %s: %s", state_path, exc)
            return None

        def _label_value(*labels: str) -> str:
            lines = state_text.splitlines()
            for label in labels:
                # Inline form: `Current phase: DONE` / `Status: COMPLETE`.
                match = re.search(
                    rf"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*:\s*(.+?)\s*$",
                    state_text,
                )
                if match:
                    return match.group(1).strip().strip("`*_ ").upper()

                # Markdown heading form, common in generated SuperGoal state:
                #
                #   ## Current phase
                #   DONE
                heading = re.compile(
                    rf"^\s*#+\s*(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*$",
                    re.IGNORECASE,
                )
                for idx, line in enumerate(lines):
                    if not heading.match(line):
                        continue
                    for nxt in lines[idx + 1 :]:
                        stripped = nxt.strip()
                        if not stripped:
                            continue
                        if stripped.startswith("#"):
                            break
                        return stripped.strip("`*_ -").upper()
            return ""

        def _is_terminal_value(value: str) -> bool:
            value = (value or "").strip().upper()
            return value in {"COMPLETE", "DONE"} or value.startswith(("COMPLETE ", "COMPLETE —", "DONE ", "DONE —"))

        def _audit_markers_recorded_in(text: str) -> bool:
            return has_standalone_marker(text, "AUDIT_COMPLETE") and has_standalone_marker(
                text,
                "SUPERGOAL_RUN_COMPLETE",
            )

        def _audit_markers_recorded() -> bool:
            if _audit_markers_recorded_in(state_text):
                return True
            # Newer packages keep final markers in reports/final-audit.md and
            # STATE.md only points at that report.  If Current phase is already
            # terminal, consult the canonical final-audit report before letting
            # GoalManager synthesize another continuation turn.
            canonical_root = state_path.parent.parent if state_path.parent.name == ".supergoal" else label_root
            allowed_base = canonical_root / ".supergoal"
            candidates = [state_path.parent / "reports" / "final-audit.md"]
            for match in re.finditer(r"`?([^`\n]*final-audit\.md)`?", state_text, re.IGNORECASE):
                raw = match.group(1).strip().strip("`'\"<>").rstrip(".,;)]")
                if not raw:
                    continue
                path = Path(raw)
                # Completion proof must stay inside the canonical SuperGoal package.
                # Absolute external paths can point at stale/unrelated audits and
                # must not satisfy this goal's disk-completion guard.
                if path.is_absolute():
                    continue
                path = state_path.parent / path
                candidates.append(path)
            seen: set[str] = set()
            for audit_path in candidates:
                try:
                    resolved = audit_path.resolve(strict=False)
                    allowed = allowed_base.resolve(strict=False)
                    if allowed not in (resolved, *resolved.parents):
                        continue
                except Exception:
                    continue
                key = str(resolved)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    audit_text = audit_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if _audit_markers_recorded_in(audit_text):
                    return True
            return False

        status_value = _label_value("Status", "Status snapshot")
        phase_value = _label_value("Current phase")
        # A terminal Current phase is enough when final audit markers exist in
        # STATE.md or the canonical final-audit report.  Requiring an inline
        # Status label missed Markdown-style STATE.md files and caused spammy
        # post-complete continuation loops.
        terminal_status = _is_terminal_value(phase_value) or (
            _is_terminal_value(status_value) and _is_terminal_value(phase_value)
        )
        if terminal_status and _audit_markers_recorded():
            return f"supergoal STATE.md already complete at {label_root}"
        return ""

    candidate_roots = _candidate_state_roots(goal)

    for root in candidate_roots:
        state_path = root / ".supergoal" / "STATE.md"
        reason = _completion_reason_for_state(state_path, root)
        if reason:
            return reason
        if reason == "":
            # First existing STATE.md is canonical for this goal.  Do not keep
            # scanning older previous-rail paths that may also be mentioned.
            return None

    for state_path in _direct_state_paths(allow_relative=not candidate_roots):
        label_root = state_path.parent.parent if state_path.parent.name == ".supergoal" else state_path.parent
        reason = _completion_reason_for_state(state_path, label_root)
        if reason:
            return reason
        if reason == "":
            return None
    return None


def is_structured_handoff_reason(reason: str) -> bool:
    """Whether a deterministic DONE verdict should be stored as blocked."""
    upper = str(reason or "").upper()
    return (
        "FAILURE_HANDOFF" in upper
        or "AUDIT_HANDOFF" in upper
        or "BLOCKED_BY_APPROVAL" in upper
    )


def _is_approval_blocker_response(text: str) -> bool:
    """Detect an intentional approval gate stop.

    Structured goals often require terminal audit markers, but approval-gated
    phases must stop before those markers when a side effect needs human
    approval. Without this deterministic guard, a judge can keep returning
    CONTINUE just because final markers are absent, causing the same approval
    phrase to be posted until the turn budget is exhausted.
    """
    return has_standalone_marker_prefix(text, "BLOCKED_BY_APPROVAL")


def evaluate_structured_completion_guard(
    goal: str,
    last_response: str,
) -> Optional[StructuredCompletionDecision]:
    """Return a deterministic verdict for structured goal contracts.

    ``None`` means the generic auxiliary judge should decide.  A non-None
    result is intentionally judge-proof and should be returned before calling
    the LLM judge.
    """
    if not _looks_like_structured_completion_goal(goal):
        return None

    goal_upper = goal.upper()
    disk_done_reason = _state_file_completion_reason(goal)
    if disk_done_reason:
        return StructuredCompletionDecision("done", disk_done_reason)
    if has_standalone_marker_prefix(last_response, "FAILURE_HANDOFF"):
        return StructuredCompletionDecision("done", "supergoal stopped with FAILURE_HANDOFF")
    if has_standalone_marker_prefix(last_response, "AUDIT_HANDOFF"):
        return StructuredCompletionDecision("done", "supergoal stopped with AUDIT_HANDOFF")
    if _is_approval_blocker_response(last_response):
        return StructuredCompletionDecision("done", "supergoal stopped with BLOCKED_BY_APPROVAL")
    if "SUPERGOAL_RUN_COMPLETE" in goal_upper and not has_standalone_marker(
        last_response,
        "SUPERGOAL_RUN_COMPLETE",
    ):
        return StructuredCompletionDecision(
            "continue",
            "missing standalone SUPERGOAL_RUN_COMPLETE terminal marker",
        )
    if "AUDIT_COMPLETE" in goal_upper and not has_standalone_marker(
        last_response,
        "AUDIT_COMPLETE",
    ):
        return StructuredCompletionDecision(
            "continue",
            "missing standalone AUDIT_COMPLETE terminal marker",
        )
    return None
