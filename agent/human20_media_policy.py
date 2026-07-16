"""Fail-closed outbound media intent and provenance policy."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class MediaDecision:
    allowed: bool
    code: str
    analyze: bool = False


class MediaIntent:
    @staticmethod
    def evaluate(
        requested: bool,
        inbound: bool,
        age_seconds: float,
        max_age_seconds: float,
        variants: int,
        variants_requested: bool,
    ) -> MediaDecision:
        if inbound:
            return MediaDecision(False, "H20_MEDIA_INBOUND_ANALYZE_ONLY", analyze=True)
        if not requested:
            return MediaDecision(False, "H20_MEDIA_UNSOLICITED")
        if age_seconds < 0 or age_seconds > max_age_seconds:
            return MediaDecision(False, "H20_MEDIA_STALE")
        if variants < 1:
            return MediaDecision(False, "H20_MEDIA_EMPTY")
        if variants > 1 and not variants_requested:
            return MediaDecision(False, "H20_MEDIA_VARIANTS_NOT_REQUESTED")
        return MediaDecision(True, "H20_MEDIA_ALLOWED")


class MediaPathPolicy:
    def __init__(self, actor_sandbox: str | Path, approved_roots: Iterable[str | Path]) -> None:
        self.actor_sandbox = Path(actor_sandbox).expanduser().resolve()
        self.approved_roots = tuple(Path(root).expanduser().resolve() for root in approved_roots)
        self.roots = (self.actor_sandbox, *self.approved_roots)

    @staticmethod
    def _contains_symlink(path: Path) -> bool:
        current = path
        while True:
            if current.is_symlink():
                return True
            if current == current.parent:
                return False
            current = current.parent

    def check(self, path: str | Path) -> MediaDecision:
        candidate = Path(path).expanduser()
        if self._contains_symlink(candidate):
            return MediaDecision(False, "H20_MEDIA_SYMLINK_DENIED")
        if not candidate.exists() or not candidate.is_file():
            return MediaDecision(False, "H20_MEDIA_FILE_MISSING")
        resolved = candidate.resolve()
        if not any(resolved.is_relative_to(root) for root in self.roots):
            return MediaDecision(False, "H20_MEDIA_PATH_DENIED")
        return MediaDecision(True, "H20_MEDIA_PATH_ALLOWED")
