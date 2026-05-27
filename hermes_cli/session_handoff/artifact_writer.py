"""HandoffArtifactWriter — writes profile-local .md/.json handoff artifacts.

Acceptance: writes profile-local Markdown and JSON artifacts under
~/.hermes/handoffs/<slug>/<timestamp>.{md,json}; no mem0g writes,
no raw transcript dumps, no secret leakage.

Privacy constraints enforced:
- Content previews capped at 200 chars by trace_reader.
- Secret-pattern scan before writing any artifact.
- No raw message content in artifacts.
- No raw transcript dumps.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Profile-aware home helper.
try:
    from hermes_constants import get_hermes_home
except ImportError:
    # Fallback: read HERMES_HOME from env directly.
    import os
    def get_hermes_home() -> Path:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))

from hermes_cli.session_handoff.handoff_extractor import HandoffResult


# ------------------------------------------------------------------
# Privacy scan
# ------------------------------------------------------------------

# Patterns matching things that must NOT appear in artifacts.
# Order matters: more-specific patterns first.
_SECRET_PATTERNS = (
    re.compile(r"(-----BEGIN (RSA |EC )?PRIVATE KEY-----)"),   # Private key headers (most specific)
    re.compile(r"\b(sk-[a-zA-Z0-9]{20,})\b"),                  # OpenAI keys: sk- + 20+ alphanumerics
    re.compile(r"\b(ghp_[a-zA-Z0-9]{20,})\b"),                 # GitHub tokens: ghp_ + 20+ alphanumerics
    re.compile(r"\b(ai[a-zA-Z0-9]{30,})\b"),                   # Generic AI tokens
    re.compile(r"\b( Bearer [a-zA-Z0-9_.-]{10,})\b"),          # Authorization headers
    re.compile(r"\b(password[:=]\s*\S+)\b", re.IGNORECASE),    # password:= or password: <value>
    re.compile(r"\b(hmac-[a-zA-Z0-9_-]{10,})\b"),             # HMAC keys
    re.compile(r"sk-[a-zA-Z0-9*]{2,}\.\.\.[a-zA-Z0-9*]{2,}"),  # Masked API key placeholders (e.g. sk-abc...wxyz)
    re.compile(r"\b(ghp-[a-zA-Z0-9*]{2,}\.\.\.[a-zA-Z0-9*]{2,})\b"), # Masked GitHub tokens (ghp-abc...xyz)
    re.compile(r"\b(ghp_[a-zA-Z0-9*]{2,}\.\.\.[a-zA-Z0-9*]{2,})\b"), # Masked GitHub tokens with underscore (ghp_ab...xyz)
    re.compile(r"\b([a-zA-Z0-9._-]+@[a-zA-Z0-9_-]+\.[a-zA-Z]{2,})\b"),  # emails -- relaxed, allow context
)

_REDACT_MARKER = "[REDACTED]"


def _scan_for_secrets(text: str) -> List[str]:
    findings: List[str] = []
    for pattern in _SECRET_PATTERNS:
        for m in pattern.finditer(text):
            found = m.group()
            # Allow context that looks like a file path or email or URL — redact token portion
            if re.match(r"^[a-zA-Z0-9._-]+@[a-zA-Z0-9_-]+\.[a-zA-Z]{2,}$", found):
                continue  # skip email-like (allowed with context)
            if re.match(r"^\S+\.(py|md|json|yaml|txt)$", found):
                continue  # looks like a filename, not a secret
            findings.append(found)
    return findings


def _redact(text: str) -> str:
    """Redact secret-like strings from text."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACT_MARKER, text)
    return text


# ------------------------------------------------------------------
# Artifact paths
# ------------------------------------------------------------------

@dataclass(frozen=True)
class ArtifactPaths:
    """Resolved paths for a handoff artifact pair."""

    dir: Path
    md_path: Path
    json_path: Path
    slug: str


def resolve_artifact_paths(topic: str, base_dir: Optional[Path] = None) -> ArtifactPaths:
    """Resolve the directory and file paths for a handoff artifact.

    Args:
        topic: The handoff topic string (used to generate a slug).
        base_dir: Base directory. Defaults to ~/.hermes/handoffs.

    Returns:
        ArtifactPaths with dir, md_path, json_path.
    """
    if base_dir is None:
        base_dir = get_hermes_home() / "handoffs"

    # Slugify topic into a safe directory name.
    slug = _slugify(topic)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    dir_path = base_dir / slug / ts
    return ArtifactPaths(
        dir=dir_path,
        md_path=dir_path / f"handoff.{ts}.md",
        json_path=dir_path / f"handoff.{ts}.json",
        slug=slug,
    )


def _slugify(text: str) -> str:
    """Convert a topic string into a safe directory name."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    # Bound length
    return (text[:50] + "-handoff") if len(text) > 50 else (text + "-handoff")


# ------------------------------------------------------------------
# Writer
# ------------------------------------------------------------------

_MAX_CONTENT_PREVIEW = 200


class HandoffArtifactWriter:
    """Write handoff artifacts to profile-local storage.

    Privacy-first:
    - Scans for secret-like patterns before writing.
    - Uses content previews only (max 200 chars, enforced upstream in trace_reader).
    - No raw transcripts.
    - No mem0g / external system writes.
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base_dir = base_dir or (get_hermes_home() / "handoffs")

    def write(self, result: HandoffResult) -> ArtifactPaths:
        """Write .md and .json artifacts from a HandoffResult.

        Args:
            result: The HandoffResult from HandoffExtractor.

        Returns:
            ArtifactPaths with resolved paths.

        Raises:
            ValueError: If secret-like patterns are found in the handoff content.
        """
        paths = resolve_artifact_paths(result.topic, self._base_dir)

        # Gather all text for secret scan BEFORE writing any files.
        # This ensures no artifact is created if secrets are detected.
        scan_text = " ".join([
            result.topic,
            result.context,
            " ".join(result.decisions),
            " ".join(result.next_steps),
            " ".join(result.blockers),
            " ".join(result.files),
            " ".join(result.commands),
            result.resume_prompt,
        ])

        if _scan_for_secrets(scan_text):
            raise ValueError(
                "Handoff content contains secret-like patterns. "
                "Refusing to write artifact. Review and redact manually."
            )

        # Write Markdown.
        md_content = self._render_markdown(result)
        paths.dir.mkdir(parents=True, exist_ok=True)
        paths.md_path.write_text(md_content, encoding="utf-8")

        # Write JSON.
        json_content = self._render_json(result)
        paths.json_path.write_text(json_content, encoding="utf-8")

        return paths

    def _render_markdown(self, result: HandoffResult) -> str:
        lines = [
            f"# Handoff: {result.topic}",
            "",
            f"**Confidence:** {result.confidence:.0%} | **Staleness:** {result.staleness}",
            "",
        ]

        if result.evidence_refs:
            lines.append("## Evidence refs")
            for ref in result.evidence_refs:
                lines.append(f"- `{ref}`")
            lines.append("")

        if result.context:
            lines.append("## Context")
            lines.append(result.context)
            lines.append("")

        if result.decisions:
            lines.append("## Decisions")
            for d in result.decisions:
                lines.append(f"- {d}")
            lines.append("")

        if result.files:
            lines.append("## Files")
            seen: set[str] = set()
            for f in result.files:
                redacted = _redact(f)
                if f not in seen:
                    seen.add(f)
                    lines.append(f"- `{redacted}`")
            lines.append("")

        if result.commands:
            lines.append("## Commands / Tools")
            for c in result.commands:
                lines.append(f"```sh\n{_redact(c)}\n```")
            lines.append("")

        if result.blockers:
            lines.append("## Blockers")
            for b in result.blockers:
                lines.append(f"- ⚠️ {b}")
            lines.append("")

        if result.next_steps:
            lines.append("## Next steps")
            for i, s in enumerate(result.next_steps, 1):
                lines.append(f"{i}. {s}")
            lines.append("")

        lines.extend([
            "## Resume prompt",
            "```",
            result.resume_prompt,
            "```",
            "",
            f"*Generated: {datetime.now(timezone.utc).isoformat()} | Sessions: {', '.join(result.session_ids)}*",
        ])

        return "\n".join(lines)

    def _render_json(self, result: HandoffResult) -> str:
        payload: Dict[str, Any] = {
            "schema": "session-handoff-artifact.v1",
            "topic": result.topic,
            "context": _redact(result.context),
            "decisions": [_redact(d) for d in result.decisions],
            "evidence_refs": result.evidence_refs,
            "files": list(dict.fromkeys([_redact(f) for f in result.files])),
            "commands": [_redact(c) for c in result.commands],
            "blockers": [_redact(b) for b in result.blockers],
            "next_steps": [_redact(s) for s in result.next_steps],
            "resume_prompt": _redact(result.resume_prompt),
            "confidence": result.confidence,
            "staleness": result.staleness,
            "session_ids": result.session_ids,
            "messages_inspected": result.messages_inspected,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
