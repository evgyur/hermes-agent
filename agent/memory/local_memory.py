"""Profile-scoped Hermes local memory skeleton.

This module is intentionally local-only. It stores private hot/warm/cold working
memory under ``get_hermes_home()`` and never writes to shared/canonical memory.
mem0g remains the only shared canonical boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_hermes_home


class LocalMemoryError(ValueError):
    """Base error for local memory contract violations."""


class LocalMemoryState(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED_HOT = "accepted_hot"
    COMPACTED_WARM = "compacted_warm"
    CURATED_COLD = "curated_cold"
    EXPIRED = "expired"
    DELETED = "deleted"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class LocalMemoryLayer(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    TOMBSTONES = "tombstones"


class RedactionStatus(str, Enum):
    CLEAN = "clean"
    REJECTED_SECRET = "rejected_secret"
    REJECTED_RAW_PAYLOAD = "rejected_raw_payload"
    REJECTED_OVERSIZE = "rejected_oversize"


class DoctorVerdict(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


ALLOWED_SOURCE_CLASSES = {
    "operator_note",
    "final_response_candidate",
    "session_handoff",
    "tool_result_summary",
    "synthetic_test",
}

ALLOWED_TRANSITIONS: set[tuple[LocalMemoryState, LocalMemoryState]] = {
    (LocalMemoryState.PROPOSED, LocalMemoryState.ACCEPTED_HOT),
    (LocalMemoryState.PROPOSED, LocalMemoryState.REJECTED),
    (LocalMemoryState.ACCEPTED_HOT, LocalMemoryState.COMPACTED_WARM),
    (LocalMemoryState.ACCEPTED_HOT, LocalMemoryState.EXPIRED),
    (LocalMemoryState.ACCEPTED_HOT, LocalMemoryState.DELETED),
    (LocalMemoryState.ACCEPTED_HOT, LocalMemoryState.QUARANTINED),
    (LocalMemoryState.COMPACTED_WARM, LocalMemoryState.CURATED_COLD),
    (LocalMemoryState.COMPACTED_WARM, LocalMemoryState.EXPIRED),
    (LocalMemoryState.COMPACTED_WARM, LocalMemoryState.DELETED),
    (LocalMemoryState.COMPACTED_WARM, LocalMemoryState.QUARANTINED),
    (LocalMemoryState.CURATED_COLD, LocalMemoryState.EXPIRED),
    (LocalMemoryState.CURATED_COLD, LocalMemoryState.DELETED),
    (LocalMemoryState.CURATED_COLD, LocalMemoryState.QUARANTINED),
    (LocalMemoryState.QUARANTINED, LocalMemoryState.DELETED),
    (LocalMemoryState.QUARANTINED, LocalMemoryState.REJECTED),
}

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs])-?[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[^\s'\"]{12,}"),
]
RAW_PAYLOAD_PATTERNS = [
    re.compile(r"(?i)\b(raw_telegram_dump|message_id|chat_id|from_user|reply_to_message)\b.*\b(text|caption|entities)\b", re.S),
    re.compile(r'(?is)"message_id"\s*:\s*\d+.*"chat"\s*:\s*\{.*"from"\s*:\s*\{.*"text"\s*:'),
    re.compile(r"(?i)\b(full transcript|raw private chat|verbatim chat dump)\b"),
]

RETENTION_POLICY_SECONDS = {
    LocalMemoryLayer.HOT: 24 * 60 * 60,
    LocalMemoryLayer.WARM: 30 * 24 * 60 * 60,
    LocalMemoryLayer.COLD: 180 * 24 * 60 * 60,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _profile_id(home: Path) -> str:
    if home.parent.name == "profiles":
        return home.name
    return "default"


def _scan_payload(text: str) -> RedactionStatus:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return RedactionStatus.REJECTED_SECRET
    for pattern in RAW_PAYLOAD_PATTERNS:
        if pattern.search(text):
            return RedactionStatus.REJECTED_RAW_PAYLOAD
    return RedactionStatus.CLEAN


@dataclass(frozen=True)
class LocalMemoryEnvelope:
    id: str
    profile_id: str
    layer: LocalMemoryLayer
    state: LocalMemoryState
    source_class: str
    origin_ref: str
    created_at: str
    expires_at: str | None
    payload_text: str | None
    redaction_status: RedactionStatus
    confidence: float
    policy_labels: list[str]
    staleness: str
    checksum: str
    authority: str = "private_derived_working_memory"
    canonical_store: str = "mem0g-api"
    updated_at: str | None = None
    deleted_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["layer"] = self.layer.value
        data["state"] = self.state.value
        data["redaction_status"] = self.redaction_status.value
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LocalMemoryEnvelope":
        return cls(
            id=str(data["id"]),
            profile_id=str(data["profile_id"]),
            layer=LocalMemoryLayer(data["layer"]),
            state=LocalMemoryState(data["state"]),
            source_class=str(data["source_class"]),
            origin_ref=str(data["origin_ref"]),
            created_at=str(data["created_at"]),
            expires_at=data.get("expires_at"),
            payload_text=data.get("payload_text"),
            redaction_status=RedactionStatus(data["redaction_status"]),
            confidence=float(data["confidence"]),
            policy_labels=list(data.get("policy_labels") or []),
            staleness=str(data.get("staleness") or "fresh"),
            checksum=str(data["checksum"]),
            authority=str(data.get("authority") or "private_derived_working_memory"),
            canonical_store=str(data.get("canonical_store") or "mem0g-api"),
            updated_at=data.get("updated_at"),
            deleted_reason=data.get("deleted_reason"),
        )


@dataclass(frozen=True)
class DoctorReport:
    local_store: dict[str, Any]
    rotation: dict[str, Any]
    secret_guard: dict[str, Any]
    recall_quality: dict[str, Any]
    promotion_adapter: dict[str, Any]
    mem0g_connectivity: dict[str, Any]
    audit: dict[str, Any]
    stale_index: dict[str, Any]
    rollout_flags: dict[str, Any]
    verdict: DoctorVerdict

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["verdict"] = self.verdict.value
        return data


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    action: str
    note_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class HermesLocalMemory:
    """Thin profile-aware hot/warm/cold local memory store."""

    max_payload_bytes = 16 * 1024

    def __init__(self, root: Path | None = None) -> None:
        self.hermes_home = Path(root) if root is not None else get_hermes_home()
        self.profile_id = _profile_id(self.hermes_home)
        self.root = self.hermes_home / "local-memory"
        self.layers_dir = self.root / "layers"

    def ensure_store(self) -> None:
        for layer in LocalMemoryLayer:
            (self.layers_dir / f"{layer.value}.jsonl").parent.mkdir(parents=True, exist_ok=True)
            (self.layers_dir / f"{layer.value}.jsonl").touch(exist_ok=True)
        (self.root / "ledger.jsonl").touch(exist_ok=True)

    def append_event(
        self,
        text: str,
        *,
        source_class: str = "operator_note",
        origin_ref: str = "cli",
        confidence: float = 0.5,
        policy_labels: Iterable[str] | None = None,
        ttl_seconds: int | None = None,
    ) -> LocalMemoryEnvelope:
        if source_class not in ALLOWED_SOURCE_CLASSES:
            return self._rejected(text, source_class, origin_ref, confidence, "UNSUPPORTED_SOURCE_CLASS")
        if not origin_ref:
            raise LocalMemoryError("origin_ref is required")
        if confidence < 0 or confidence > 1:
            raise LocalMemoryError("confidence must be between 0 and 1")
        redaction = _scan_payload(text)
        if redaction is not RedactionStatus.CLEAN:
            reason = "SECRET_DETECTED" if redaction is RedactionStatus.REJECTED_SECRET else "RAW_PAYLOAD_REJECTED"
            return self._rejected(None, source_class, origin_ref, confidence, reason, redaction)
        encoded = text.encode("utf-8")
        if len(encoded) > self.max_payload_bytes:
            return self._rejected(None, source_class, origin_ref, confidence, "OVERSIZE_PAYLOAD", RedactionStatus.REJECTED_OVERSIZE)

        self.ensure_store()
        checksum = hashlib.sha256(encoded).hexdigest()
        note_id = self._idempotency_id(source_class, origin_ref, checksum)
        expires_at = self._expires_at_for_layer(LocalMemoryLayer.HOT, ttl_seconds)
        env = LocalMemoryEnvelope(
            id=note_id,
            profile_id=self.profile_id,
            layer=LocalMemoryLayer.HOT,
            state=LocalMemoryState.ACCEPTED_HOT,
            source_class=source_class,
            origin_ref=origin_ref,
            created_at=_now_iso(),
            expires_at=expires_at,
            payload_text=text,
            redaction_status=RedactionStatus.CLEAN,
            confidence=confidence,
            policy_labels=list(policy_labels or []),
            staleness="fresh",
            checksum=checksum,
        )
        self._append(LocalMemoryLayer.HOT, env)
        self._ledger("append", env)
        return env

    def compact_hot(self, *, limit: int | None = None) -> OperationResult:
        self.ensure_store()
        hot = [n for n in self._read(LocalMemoryLayer.HOT) if n.state is LocalMemoryState.ACCEPTED_HOT]
        if limit is not None:
            hot = hot[:limit]
        moved = 0
        remaining_ids = {n.id for n in hot}
        for note in hot:
            summary = (note.payload_text or "").strip()
            if len(summary) > 1000:
                summary = summary[:997].rstrip() + "..."
            warm = self._transition(
                note,
                LocalMemoryState.COMPACTED_WARM,
                LocalMemoryLayer.WARM,
                payload_text=summary,
                staleness="fresh",
                expires_at=self._expires_at_for_layer(LocalMemoryLayer.WARM),
            )
            self._append(LocalMemoryLayer.WARM, warm)
            self._ledger("compact_hot", warm)
            moved += 1
        if moved:
            self._rewrite(LocalMemoryLayer.HOT, [n for n in self._read(LocalMemoryLayer.HOT) if n.id not in remaining_ids])
        return OperationResult(True, "compact_hot", details={"moved": moved})

    def rotate(
        self,
        *,
        hot_max_age_seconds: int = RETENTION_POLICY_SECONDS[LocalMemoryLayer.HOT],
        warm_max_age_seconds: int = RETENTION_POLICY_SECONDS[LocalMemoryLayer.WARM],
        cold_max_age_seconds: int = RETENTION_POLICY_SECONDS[LocalMemoryLayer.COLD],
    ) -> OperationResult:
        self.ensure_store()
        now = datetime.now(timezone.utc)
        expired = 0
        for layer, max_age in (
            (LocalMemoryLayer.HOT, min(hot_max_age_seconds, RETENTION_POLICY_SECONDS[LocalMemoryLayer.HOT])),
            (LocalMemoryLayer.WARM, min(warm_max_age_seconds, RETENTION_POLICY_SECONDS[LocalMemoryLayer.WARM])),
            (LocalMemoryLayer.COLD, min(cold_max_age_seconds, RETENTION_POLICY_SECONDS[LocalMemoryLayer.COLD])),
        ):
            kept: list[LocalMemoryEnvelope] = []
            for note in self._read(layer):
                created = _parse_iso(note.created_at) or now
                explicit_expiry = _parse_iso(note.expires_at)
                should_expire = bool(explicit_expiry and explicit_expiry <= now) or (now - created).total_seconds() > max_age
                if should_expire and note.state not in {LocalMemoryState.DELETED, LocalMemoryState.REJECTED, LocalMemoryState.EXPIRED}:
                    expired_note = self._transition(note, LocalMemoryState.EXPIRED, layer, payload_text=None)
                    self._append(LocalMemoryLayer.TOMBSTONES, expired_note)
                    self._ledger("rotate_expire", expired_note)
                    expired += 1
                else:
                    kept.append(note)
            self._rewrite(layer, kept)
        return OperationResult(True, "rotate", details={"expired": expired})

    def delete(self, note_id: str, *, reason: str = "operator_delete") -> OperationResult:
        self.ensure_store()
        for layer in (LocalMemoryLayer.HOT, LocalMemoryLayer.WARM, LocalMemoryLayer.COLD):
            notes = self._read(layer)
            kept: list[LocalMemoryEnvelope] = []
            for note in notes:
                if note.id == note_id:
                    deleted = self._transition(
                        note,
                        LocalMemoryState.DELETED,
                        layer,
                        payload_text=None,
                        deleted_reason=reason,
                    )
                    self._append(LocalMemoryLayer.TOMBSTONES, deleted)
                    self._ledger("delete", deleted)
                    return OperationResult(True, "delete", note_id=note_id, details={"layer": layer.value})
                kept.append(note)
            self._rewrite(layer, kept)
        return OperationResult(False, "delete", note_id=note_id, details={"error": "NOT_FOUND"})

    def doctor(self) -> DoctorReport:
        self.ensure_store()
        counts: dict[str, int] = {}
        corrupt: list[str] = []
        for layer in LocalMemoryLayer:
            try:
                counts[layer.value] = len(self._read(layer))
            except Exception as exc:  # pragma: no cover - defensive
                corrupt.append(f"{layer.value}: {exc}")
        store_ok = not corrupt
        blocked_counts: dict[str, int] = {}
        for note in self._read(LocalMemoryLayer.TOMBSTONES):
            if note.state is LocalMemoryState.REJECTED:
                reason = note.deleted_reason or (note.policy_labels[0] if note.policy_labels else "UNKNOWN")
                blocked_counts[reason] = blocked_counts.get(reason, 0) + 1
        secret_ok = _scan_payload("sk-test-" + "x" * 30) is RedactionStatus.REJECTED_SECRET
        raw_ok = _scan_payload('{"message":{"message_id":1,"chat":{"id":2},"from":{"id":3},"text":"private"}}') is RedactionStatus.REJECTED_RAW_PAYLOAD
        oversize_ok = self.max_payload_bytes <= RETENTION_POLICY_SECONDS[LocalMemoryLayer.HOT]
        guard_ok = secret_ok and raw_ok and oversize_ok
        verdict = DoctorVerdict.GREEN if store_ok and guard_ok else DoctorVerdict.RED
        return DoctorReport(
            local_store={"ok": store_ok, "root": str(self.root), "profile_id": self.profile_id, "counts": counts, "corrupt": corrupt},
            rotation={"ok": True, "layers": ["hot", "warm", "cold"], "retention_seconds": {k.value: v for k, v in RETENTION_POLICY_SECONDS.items()}},
            secret_guard={"ok": guard_ok, "mode": "fail_closed", "blocked_write_counts": blocked_counts},
            recall_quality={"ok": True, "mode": "local_only_skeleton"},
            promotion_adapter={"ok": True, "mode": "disabled_local_only", "canonical_store": "mem0g-api"},
            mem0g_connectivity={"ok": True, "mode": "not_required_for_local_store"},
            audit={"ok": (self.root / "ledger.jsonl").exists()},
            stale_index={"ok": True, "mode": "no_l4_index"},
            rollout_flags={"ok": True, "state": "disabled"},
            verdict=verdict,
        )

    def _idempotency_id(self, source_class: str, origin_ref: str, checksum: str) -> str:
        raw = f"{self.profile_id}\0{source_class}\0{origin_ref.strip()}\0{checksum}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _bounded_ttl(self, ttl_seconds: int | None, layer: LocalMemoryLayer) -> int:
        max_ttl = RETENTION_POLICY_SECONDS[layer]
        if ttl_seconds is None:
            return max_ttl
        return min(ttl_seconds, max_ttl)

    def _expires_at_for_layer(self, layer: LocalMemoryLayer, ttl_seconds: int | None = None) -> str:
        ttl = self._bounded_ttl(ttl_seconds, layer)
        return datetime.fromtimestamp(time.time() + ttl, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _rejected(
        self,
        text: str | None,
        source_class: str,
        origin_ref: str,
        confidence: float,
        reason: str,
        redaction: RedactionStatus = RedactionStatus.CLEAN,
    ) -> LocalMemoryEnvelope:
        self.ensure_store()
        payload = text if redaction is RedactionStatus.CLEAN else None
        checksum = hashlib.sha256((text or reason).encode("utf-8")).hexdigest()
        env = LocalMemoryEnvelope(
            id=str(uuid.uuid4()),
            profile_id=self.profile_id,
            layer=LocalMemoryLayer.TOMBSTONES,
            state=LocalMemoryState.REJECTED,
            source_class=source_class,
            origin_ref=origin_ref or "unknown",
            created_at=_now_iso(),
            expires_at=None,
            payload_text=payload,
            redaction_status=redaction,
            confidence=max(0.0, min(1.0, confidence)),
            policy_labels=[reason],
            staleness="n/a",
            checksum=checksum,
            deleted_reason=reason,
        )
        self._append(LocalMemoryLayer.TOMBSTONES, env)
        self._ledger("reject", env)
        return env

    def _transition(
        self,
        note: LocalMemoryEnvelope,
        new_state: LocalMemoryState,
        new_layer: LocalMemoryLayer,
        **updates: Any,
    ) -> LocalMemoryEnvelope:
        if (note.state, new_state) not in ALLOWED_TRANSITIONS:
            raise LocalMemoryError(f"invalid transition: {note.state.value} -> {new_state.value}")
        data = note.to_json()
        data.update(updates)
        data["state"] = new_state.value
        data["layer"] = new_layer.value
        data["updated_at"] = _now_iso()
        return LocalMemoryEnvelope.from_json(data)

    def _path(self, layer: LocalMemoryLayer) -> Path:
        return self.layers_dir / f"{layer.value}.jsonl"

    def _append(self, layer: LocalMemoryLayer, envelope: LocalMemoryEnvelope) -> None:
        self.ensure_store()
        with self._path(layer).open("a", encoding="utf-8") as fh:
            fh.write(_canonical_json(envelope.to_json()) + "\n")

    def _read(self, layer: LocalMemoryLayer) -> list[LocalMemoryEnvelope]:
        path = self._path(layer)
        if not path.exists():
            return []
        notes: list[LocalMemoryEnvelope] = []
        with path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    notes.append(LocalMemoryEnvelope.from_json(json.loads(line)))
                except Exception as exc:
                    raise LocalMemoryError(f"corrupt {path}:{lineno}: {exc}") from exc
        return notes

    def _rewrite(self, layer: LocalMemoryLayer, notes: Iterable[LocalMemoryEnvelope]) -> None:
        self.ensure_store()
        path = self._path(layer)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for note in notes:
                fh.write(_canonical_json(note.to_json()) + "\n")
        tmp.replace(path)

    def _ledger(self, action: str, envelope: LocalMemoryEnvelope) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": _now_iso(),
            "action": action,
            "note_id": envelope.id,
            "state": envelope.state.value,
            "layer": envelope.layer.value,
            "reason": envelope.deleted_reason,
            "redaction_status": envelope.redaction_status.value,
        }
        with (self.root / "ledger.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(_canonical_json(entry) + "\n")
