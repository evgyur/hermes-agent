"""Deterministic recall orchestration for Hermes local memory.

The orchestrator builds an observe-only ``RecallPack`` from current context,
live/canonical evidence, mem0g shared candidates, Hermes-local hot/warm/cold
notes, and session_search summaries. Hermes-local memory is private derived
working memory; conflicts with mem0g or live evidence are surfaced and the local
item is suppressed instead of overriding the canonical fact.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .local_memory import HermesLocalMemory, LocalMemoryEnvelope, LocalMemoryLayer, LocalMemoryState


class RecallSource(str, Enum):
    CURRENT_CONTEXT = "current_context"
    LIVE_EVIDENCE = "live_evidence"
    MEM0G_SHARED = "mem0g_shared"
    LOCAL_HOT = "local_hot"
    LOCAL_WARM = "local_warm"
    LOCAL_COLD = "local_cold"
    SESSION_SEARCH = "session_search"


class AuthorityClass(str, Enum):
    CURRENT_USER = "current_user"
    LIVE_EVIDENCE = "live_evidence"
    MEM0G_CANONICAL = "mem0g_canonical"
    LOCAL_DERIVED = "local_derived"
    SESSION_SUMMARY = "session_summary"


class ConflictStatus(str, Enum):
    NONE = "none"
    WINNER = "winner"
    CONFLICT_SUPPRESSED = "conflict_suppressed"
    WARNING = "warning"


AUTHORITY_PRIORITY: dict[AuthorityClass, int] = {
    AuthorityClass.CURRENT_USER: 500,
    AuthorityClass.LIVE_EVIDENCE: 400,
    AuthorityClass.MEM0G_CANONICAL: 300,
    AuthorityClass.LOCAL_DERIVED: 100,
    AuthorityClass.SESSION_SUMMARY: 50,
}

SOURCE_ORDER: tuple[RecallSource, ...] = (
    RecallSource.CURRENT_CONTEXT,
    RecallSource.LIVE_EVIDENCE,
    RecallSource.MEM0G_SHARED,
    RecallSource.LOCAL_HOT,
    RecallSource.LOCAL_WARM,
    RecallSource.LOCAL_COLD,
    RecallSource.SESSION_SEARCH,
)

SOURCE_AUTHORITY: dict[RecallSource, AuthorityClass] = {
    RecallSource.CURRENT_CONTEXT: AuthorityClass.CURRENT_USER,
    RecallSource.LIVE_EVIDENCE: AuthorityClass.LIVE_EVIDENCE,
    RecallSource.MEM0G_SHARED: AuthorityClass.MEM0G_CANONICAL,
    RecallSource.LOCAL_HOT: AuthorityClass.LOCAL_DERIVED,
    RecallSource.LOCAL_WARM: AuthorityClass.LOCAL_DERIVED,
    RecallSource.LOCAL_COLD: AuthorityClass.LOCAL_DERIVED,
    RecallSource.SESSION_SEARCH: AuthorityClass.SESSION_SUMMARY,
}


@dataclass(frozen=True)
class RecallBudget:
    max_items: int = 12
    max_chars: int = 6000
    per_source_items: dict[RecallSource, int] = field(default_factory=dict)

    def source_limit(self, source: RecallSource) -> int:
        return self.per_source_items.get(source, self.max_items)


@dataclass(frozen=True)
class RecallCandidate:
    claim: str
    source: RecallSource
    source_ref: str
    confidence: float = 0.5
    fact_key: str | None = None
    canonical_value: str | None = None
    staleness: str = "fresh"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecallItem:
    claim: str
    source: RecallSource
    source_ref: str
    authority: AuthorityClass
    confidence: float
    staleness: str
    rank: int
    fact_key: str | None = None
    canonical_value: str | None = None
    conflict_status: ConflictStatus = ConflictStatus.NONE
    conflicts_with: list[str] = field(default_factory=list)
    suppressed_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        data["authority"] = self.authority.value
        data["conflict_status"] = self.conflict_status.value
        return data


@dataclass(frozen=True)
class RecallConflict:
    fact_key: str
    winner_ref: str
    suppressed_ref: str
    reason: str
    winner_authority: AuthorityClass
    suppressed_authority: AuthorityClass

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["winner_authority"] = self.winner_authority.value
        data["suppressed_authority"] = self.suppressed_authority.value
        return data


@dataclass(frozen=True)
class RecallPack:
    query: str
    items: list[RecallItem]
    source_order: list[RecallSource]
    conflicts: list[RecallConflict]
    suppressed: list[RecallItem]
    budget_used: dict[str, Any]
    overall_confidence: float
    generated_at: str
    classification: str = "observe_only"

    def to_json(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "items": [item.to_json() for item in self.items],
            "source_order": [source.value for source in self.source_order],
            "conflicts": [conflict.to_json() for conflict in self.conflicts],
            "suppressed": [item.to_json() for item in self.suppressed],
            "budget_used": self.budget_used,
            "overall_confidence": self.overall_confidence,
            "generated_at": self.generated_at,
            "classification": self.classification,
        }


class CandidateProvider(Protocol):
    def __call__(self, query: str, context: Mapping[str, Any]) -> Iterable[RecallCandidate | Mapping[str, Any] | str]: ...


ProviderMap = Mapping[RecallSource, CandidateProvider]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_source(value: RecallSource | str) -> RecallSource:
    if isinstance(value, RecallSource):
        return value
    return RecallSource(str(value))


def _coerce_candidate(raw: RecallCandidate | Mapping[str, Any] | str, source: RecallSource, index: int) -> RecallCandidate:
    if isinstance(raw, RecallCandidate):
        if raw.source is source:
            return raw
        return replace(raw, source=source)
    if isinstance(raw, str):
        return RecallCandidate(claim=raw, source=source, source_ref=f"{source.value}:{index}")
    return RecallCandidate(
        claim=str(raw.get("claim") or raw.get("summary") or raw.get("text") or ""),
        source=source,
        source_ref=str(raw.get("source_ref") or raw.get("id") or f"{source.value}:{index}"),
        confidence=float(raw.get("confidence", 0.5)),
        fact_key=raw.get("fact_key"),
        canonical_value=raw.get("canonical_value"),
        staleness=str(raw.get("staleness") or "fresh"),
        metadata=dict(raw.get("metadata") or {}),
    )


def _normal_terms(query: str) -> set[str]:
    return {part.lower() for part in query.replace("_", " ").split() if len(part) > 2}


def _matches_query(query: str, text: str) -> bool:
    terms = _normal_terms(query)
    if not terms:
        return True
    lower = text.lower()
    return any(re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", lower) for term in terms)


def _item_sort_key(candidate: RecallCandidate) -> tuple[int, int, float, str]:
    source_idx = SOURCE_ORDER.index(candidate.source)
    authority = SOURCE_AUTHORITY[candidate.source]
    return (-AUTHORITY_PRIORITY[authority], source_idx, -candidate.confidence, candidate.source_ref)


class RecallOrchestrator:
    """Build deterministic RecallPack envelopes with explicit conflict flags."""

    def __init__(
        self,
        *,
        local_memory: HermesLocalMemory | None = None,
        providers: ProviderMap | None = None,
        source_order: Sequence[RecallSource | str] = SOURCE_ORDER,
    ) -> None:
        self.local_memory = local_memory
        self.providers = dict(providers or {})
        self.source_order = tuple(_coerce_source(source) for source in source_order)

    def recall(self, query: str, context: Mapping[str, Any] | None = None, budgets: RecallBudget | Mapping[str, Any] | None = None) -> RecallPack:
        ctx = dict(context or {})
        budget = self._coerce_budget(budgets)
        gathered: list[RecallCandidate] = []
        source_counts: dict[str, int] = {}

        for source in self.source_order:
            candidates = self._candidates_for_source(source, query, ctx)
            candidates = sorted(candidates, key=lambda c: (-c.confidence, c.source_ref))
            limited = candidates[: budget.source_limit(source)]
            source_counts[source.value] = len(limited)
            gathered.extend(limited)

        ranked_candidates = sorted(gathered, key=_item_sort_key)
        items = [self._candidate_to_item(candidate, rank=idx + 1) for idx, candidate in enumerate(ranked_candidates) if candidate.claim]
        items, suppressed, conflicts = self._apply_conflicts(items)
        items, extra_suppressed, chars_used = self._apply_budget(items, budget)
        suppressed.extend(extra_suppressed)
        items = [replace(item, rank=idx + 1) for idx, item in enumerate(items)]
        confidence = round(sum(item.confidence for item in items) / len(items), 4) if items else 0.0
        return RecallPack(
            query=query,
            items=items,
            source_order=list(self.source_order),
            conflicts=conflicts,
            suppressed=suppressed,
            budget_used={"items": len(items), "chars": chars_used, "max_items": budget.max_items, "max_chars": budget.max_chars, "source_counts": source_counts},
            overall_confidence=confidence,
            generated_at=_now_iso(),
        )

    def _coerce_budget(self, budgets: RecallBudget | Mapping[str, Any] | None) -> RecallBudget:
        if budgets is None:
            return RecallBudget()
        if isinstance(budgets, RecallBudget):
            return budgets
        raw_per_source = budgets.get("per_source_items") or {}
        per_source = {_coerce_source(k): int(v) for k, v in raw_per_source.items()}
        return RecallBudget(max_items=int(budgets.get("max_items", 12)), max_chars=int(budgets.get("max_chars", 6000)), per_source_items=per_source)

    def _candidates_for_source(self, source: RecallSource, query: str, context: Mapping[str, Any]) -> list[RecallCandidate]:
        if source in self.providers:
            raw_items = list(self.providers[source](query, context))
            return [_coerce_candidate(raw, source, idx) for idx, raw in enumerate(raw_items)]
        if source is RecallSource.CURRENT_CONTEXT:
            return self._current_context_candidates(query, context)
        if source in {RecallSource.LOCAL_HOT, RecallSource.LOCAL_WARM, RecallSource.LOCAL_COLD}:
            return self._local_candidates(source, query)
        return []

    def _current_context_candidates(self, query: str, context: Mapping[str, Any]) -> list[RecallCandidate]:
        raw = context.get("current_context") or context.get("current") or []
        if isinstance(raw, (str, Mapping)):
            raw = [raw]
        return [_coerce_candidate(item, RecallSource.CURRENT_CONTEXT, idx) for idx, item in enumerate(raw)]

    def _local_candidates(self, source: RecallSource, query: str) -> list[RecallCandidate]:
        if self.local_memory is None:
            return []
        layer = {
            RecallSource.LOCAL_HOT: LocalMemoryLayer.HOT,
            RecallSource.LOCAL_WARM: LocalMemoryLayer.WARM,
            RecallSource.LOCAL_COLD: LocalMemoryLayer.COLD,
        }[source]
        try:
            notes = self.local_memory._read(layer)  # local memory has no public search API yet.
        except Exception:
            return []
        candidates: list[RecallCandidate] = []
        for note in notes:
            if note.profile_id != self.local_memory.profile_id:
                continue
            if note.state not in {LocalMemoryState.ACCEPTED_HOT, LocalMemoryState.COMPACTED_WARM, LocalMemoryState.CURATED_COLD}:
                continue
            text = note.payload_text or ""
            if not _matches_query(query, text):
                continue
            candidates.append(self._local_note_to_candidate(note, source))
        return candidates

    def _local_note_to_candidate(self, note: LocalMemoryEnvelope, source: RecallSource) -> RecallCandidate:
        metadata = {"note_id": note.id, "origin_ref": note.origin_ref, "policy_labels": note.policy_labels, "created_at": note.created_at}
        fact_key = None
        canonical_value = None
        for label in note.policy_labels:
            if label.startswith("fact_key:"):
                fact_key = label.split(":", 1)[1]
            elif label.startswith("canonical_value:"):
                canonical_value = label.split(":", 1)[1]
        return RecallCandidate(
            claim=note.payload_text or "",
            source=source,
            source_ref=f"local:{note.layer.value}:{note.id}",
            confidence=note.confidence,
            fact_key=fact_key,
            canonical_value=canonical_value,
            staleness=note.staleness,
            metadata=metadata,
        )

    def _candidate_to_item(self, candidate: RecallCandidate, *, rank: int) -> RecallItem:
        return RecallItem(
            claim=candidate.claim,
            source=candidate.source,
            source_ref=candidate.source_ref,
            authority=SOURCE_AUTHORITY[candidate.source],
            confidence=max(0.0, min(1.0, candidate.confidence)),
            staleness=candidate.staleness,
            rank=rank,
            fact_key=candidate.fact_key,
            canonical_value=candidate.canonical_value,
            metadata=candidate.metadata,
        )

    def _apply_conflicts(self, items: list[RecallItem]) -> tuple[list[RecallItem], list[RecallItem], list[RecallConflict]]:
        winners: dict[str, RecallItem] = {}
        kept: list[RecallItem] = []
        suppressed: list[RecallItem] = []
        conflicts: list[RecallConflict] = []

        for item in items:
            if not item.fact_key or item.canonical_value is None:
                kept.append(item)
                continue
            existing = winners.get(item.fact_key)
            if existing is None:
                winner = replace(item, conflict_status=ConflictStatus.WINNER)
                winners[item.fact_key] = winner
                kept.append(winner)
                continue
            if existing.canonical_value == item.canonical_value:
                kept.append(item)
                continue
            existing_priority = AUTHORITY_PRIORITY[existing.authority]
            item_priority = AUTHORITY_PRIORITY[item.authority]
            if item_priority > existing_priority:
                suppressed_existing = replace(
                    existing,
                    conflict_status=ConflictStatus.CONFLICT_SUPPRESSED,
                    suppressed_reason=f"conflicts_with_higher_authority:{item.source_ref}",
                    conflicts_with=[item.source_ref],
                )
                kept = [suppressed_existing if kept_item.source_ref == existing.source_ref else kept_item for kept_item in kept]
                suppressed.append(suppressed_existing)
                winner = replace(item, conflict_status=ConflictStatus.WINNER, conflicts_with=[existing.source_ref])
                winners[item.fact_key] = winner
                kept.append(winner)
                conflicts.append(self._conflict(item.fact_key, winner, suppressed_existing))
            else:
                suppressed_item = replace(
                    item,
                    conflict_status=ConflictStatus.CONFLICT_SUPPRESSED,
                    suppressed_reason=f"conflicts_with_higher_authority:{existing.source_ref}",
                    conflicts_with=[existing.source_ref],
                )
                suppressed.append(suppressed_item)
                conflicts.append(self._conflict(item.fact_key, existing, suppressed_item))
        final_kept = [item for item in kept if item.conflict_status is not ConflictStatus.CONFLICT_SUPPRESSED]
        return final_kept, suppressed, conflicts

    def _conflict(self, fact_key: str, winner: RecallItem, suppressed: RecallItem) -> RecallConflict:
        return RecallConflict(
            fact_key=fact_key,
            winner_ref=winner.source_ref,
            suppressed_ref=suppressed.source_ref,
            reason="higher_authority_precedence",
            winner_authority=winner.authority,
            suppressed_authority=suppressed.authority,
        )

    def _apply_budget(self, items: list[RecallItem], budget: RecallBudget) -> tuple[list[RecallItem], list[RecallItem], int]:
        kept: list[RecallItem] = []
        suppressed: list[RecallItem] = []
        chars = 0
        for item in items:
            next_chars = chars + len(item.claim)
            if len(kept) >= budget.max_items or next_chars > budget.max_chars:
                suppressed.append(replace(item, suppressed_reason="budget_exceeded"))
                continue
            kept.append(item)
            chars = next_chars
        return kept, suppressed, chars


__all__ = [
    "AuthorityClass",
    "ConflictStatus",
    "RecallBudget",
    "RecallCandidate",
    "RecallConflict",
    "RecallItem",
    "RecallOrchestrator",
    "RecallPack",
    "RecallSource",
]
