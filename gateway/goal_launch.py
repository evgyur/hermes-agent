"""Gateway launch extraction helpers for `/goal` and SuperGoal handoff text.

These helpers are adapter glue only.  They turn Telegram/gateway reply text,
Markdown launch artifacts, inline-button payloads, and accidental pasted
handoffs into a canonical goal body for the official ``GoalManager`` path.
They do not start goals, run continuations, judge completion, or own any core
`/goal` execution state.
"""

from __future__ import annotations

import re

_SUPERGOAL_BODY_MARKER = "SUPERGOAL_GOAL_BODY:"


_GOAL_STATUS_PREFIXES = (
    "✓ goal done",
    "✓ goal achieved",
    "⊙ goal ",
    "⏸ goal ",
    "goal done (",
    "goal achieved:",
)


def goal_text_from_supergoal_artifacts(raw: str) -> str:
    """Build a canonical SuperGoal goal from visible `.supergoal` artifacts.

    Operators often reply with bare `/goal` to a human-readable planning
    message instead of a dedicated launch file.  Visible `.supergoal` artifact
    paths count as launch intent, but the whole report must not become the goal
    body.
    """
    text = str(raw or "")
    if ".supergoal/" not in text:
        return ""

    roots: list[tuple[str, str]] = []

    def _add_root(root: str) -> None:
        root = str(root or "").strip().strip("`'\" ,);]").rstrip(".")
        if not root:
            return
        project_root = ""
        if "/.supergoal/" in root:
            project_root = root.split("/.supergoal/", 1)[0]
        elif root.endswith("/.supergoal"):
            project_root = root[: -len("/.supergoal")]
        elif root == ".supergoal" or root.startswith(".supergoal/"):
            project_root = ""
        elif ".supergoal/" not in root:
            return
        item = (root, project_root)
        if item not in roots:
            roots.append(item)

    for match in re.finditer(r"(?:MEDIA:)?((?:/[^\s`\"'<>]+|\.supergoal/[^\s`\"'<>]+))", text):
        path = match.group(1).strip().strip("`'\" ,);]")
        if ".supergoal/" not in path:
            continue
        root = path
        for marker in ("/PROTOCOL.md", "/ROADMAP.md", "/STATE.md", "/THINKING.md", "/phases/"):
            idx = root.find(marker)
            if idx != -1:
                root = root[:idx]
                break
        _add_root(root)

    for pattern in (
        r"Supergoal root:\s*`?([^`\s]+)",
        r"Phase specs:\s*`?([^`\s]+?)/phases(?:/|`|\s|$)",
        r"Progress:\s*`?([^`\s]+?)/STATE\.md",
        r"Roadmap:\s*`?([^`\s]+?)/ROADMAP\.md",
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            _add_root(match.group(1))

    if not roots:
        return ""

    root, project_root = roots[0]
    prefix = (
        f"Execute the Supergoal from project root `{project_root}`."
        if project_root
        else "Execute the Supergoal from the project root."
    )
    return (
        f"{prefix} Use `{root}/PROTOCOL.md`, `{root}/ROADMAP.md`, "
        f"`{root}/STATE.md`, and `{root}/phases/phase-*.md`. "
        "Start from STATE.md current phase. Execute phases sequentially. "
        "For every phase, print SUPERGOAL_PHASE_START, "
        "SUPERGOAL_PHASE_VERIFY, and SUPERGOAL_PHASE_DONE. Run the final "
        "audit and finish only after AUDIT_COMPLETE and "
        "SUPERGOAL_RUN_COMPLETE."
    )


def extract_supergoal_body(text: str) -> str:
    """Extract only the canonical body after ``SUPERGOAL_GOAL_BODY:``."""
    raw = str(text or "").strip()
    if _SUPERGOAL_BODY_MARKER not in raw:
        return ""
    body = raw.split(_SUPERGOAL_BODY_MARKER, 1)[1].strip()
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    stop_patterns = (
        r"(?im)^\s*DONE_CONDITION\s*:",
        r"(?im)^\s*OPERATOR_ACTION\s*:",
        r"(?im)^\s*NOTES\s*:",
        r"(?im)^\s*ARTIFACTS\s*:",
        r"(?im)^\s*ФАЙЛЫ\s*:",
        r"(?im)^\s*КНОПКИ\b",
        r"(?im)^\s*1\.\s*(?:Start\s+now|Start\s+goal|Начать|Запустить|Старт|Approve|Confirm)\s*$",
        r"(?m)^\s*##\s+",
        r"(?m)^\s*Теперь\b",
        r"(?m)^\s*Reply to\b",
        r"(?m)^\s*/goal(?:@[A-Za-z0-9_]+)?\s*$",
        r"(?m)^\s*Fallback plain-text line\s*:",
        r"(?m)^\s*Не копируй\b",
        r"(?m)^\s*Не стартовал\b",
        r"(?m)^\s*Сейчас только\b",
        r"(?m)^\s*Да,\s+",
        r"(?m)^\s*Once you\b",
    )
    cut_points = [match.start() for pattern in stop_patterns if (match := re.search(pattern, body))]
    if cut_points:
        body = body[: min(cut_points)].strip()
    if body.startswith("`") and body.endswith("`") and len(body) > 2:
        body = body[1:-1].strip()
    return body


def goal_text_from_reply_context(reply_to_text: str) -> str:
    """Extract a goal body from replied-to text for bare gateway `/goal`.

    Explicit SuperGoal launch bodies win first.  Artifact-path synthesis wins
    second.  Generic replied text remains valid goal text, while prior `/goal`
    status notices are rejected to avoid one-turn false-completion loops.
    """
    raw = str(reply_to_text or "").strip()
    if not raw:
        return ""

    # Status/notice lines are not goal bodies.  This covers exact gateway
    # notices and truncated Telegram quotes that start with the same prefix.
    lowered = raw.lower()
    if lowered.startswith(_GOAL_STATUS_PREFIXES):
        return ""

    if _SUPERGOAL_BODY_MARKER in raw:
        raw = extract_supergoal_body(raw)
    else:
        supergoal_from_artifacts = goal_text_from_supergoal_artifacts(raw)
        if supergoal_from_artifacts:
            return supergoal_from_artifacts

        # A bare `/goal` reply is explicit operator intent: the replied-to text
        # is the goal body, even when it is long.  SuperGoal plans are still
        # normalized above via explicit markers/artifact paths.

    # Fallback if the user replies to a plain `/goal "..."` line.
    if raw.startswith("/goal"):
        raw = raw.split(maxsplit=1)[1].strip() if len(raw.split(maxsplit=1)) > 1 else ""
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            raw = raw[1:-1].strip()

    return raw


def goal_text_from_pasted_supergoal_handoff(text: str) -> str:
    """Extract a SuperGoal body from an accidentally pasted handoff.

    The visible `/goal` line is required so random discussion of a SuperGoal
    body does not silently launch a standing goal.
    """
    raw = str(text or "").strip()
    if _SUPERGOAL_BODY_MARKER not in raw:
        return ""

    has_goal_line = False
    goal_line = re.compile(r"^/goal(?:@[A-Za-z0-9_]+)?$")
    for line in raw.splitlines():
        if goal_line.match(line.strip()):
            has_goal_line = True
            break
    if not has_goal_line:
        return ""

    return extract_supergoal_body(raw)


def is_supergoal_dispatch(goal_text: str, *, from_reply: bool = False) -> bool:
    """Return True for SuperGoal-style long autonomous dispatches.

    ``from_reply`` is accepted for GatewayRunner compatibility and future
    adapter-specific policy, but current detection is content-based.
    """
    text = str(goal_text or "")
    if (
        ".supergoal/" in text
        or "SUPERGOAL_PHASE" in text
        or "SUPERGOAL_RUN_COMPLETE" in text
    ):
        return True
    return text.startswith(_SUPERGOAL_BODY_MARKER)
