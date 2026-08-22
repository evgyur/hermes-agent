"""Gateway adapter for the central SessionDB durable-continuation store.

Restart auto-continuation used to rely only on ``SessionStore.resume_pending``.
That JSON marker is still the compatibility/index signal, but execution
ownership and terminal state live here in the shared ``state.db`` store.  The
adapter deliberately persists only digests and small descriptors: prompts,
transcripts, tool inputs, and final response text remain in their existing
stores.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

KIND = "gateway_restart_auto_continuation"
ACTIVE_STATES = frozenset({"pending", "claimed", "waiting_unknown_effect"})
TERMINAL_STATES = frozenset(
    {"completed", "cancelled", "superseded", "failed_terminal"}
)
DEFAULT_LEASE_SECONDS = 15.0


@dataclass(frozen=True)
class ContinuationClaim:
    continuation_id: str
    generation: int
    owner: str
    claim_token: str


@dataclass(frozen=True)
class ClaimDecision:
    status: str
    record: Optional[Mapping[str, Any]] = None
    claim: Optional[ContinuationClaim] = None

    @property
    def acquired(self) -> bool:
        return self.claim is not None


async def _call(target: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    result = getattr(target, method)(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8", "replace")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _owner_stamp() -> str:
    started_at: Any = None
    try:
        from gateway.status import get_process_start_time

        started_at = get_process_start_time(os.getpid())
    except Exception:
        pass
    return f"gateway:{os.getpid()}:{started_at if started_at is not None else 'unknown'}"


def summarize_outcome(agent_result: Any, *, delivered: bool) -> tuple[str, dict[str, Any]]:
    """Return privacy-safe terminal outcome metadata for SessionDB."""

    result = agent_result if isinstance(agent_result, dict) else {}
    response = str(result.get("final_response") or "")
    descriptor = {
        "source": "gateway_restart_auto_continuation",
        "delivered": bool(delivered),
        "response_length": len(response),
        "response_digest": _digest(response),
        "completed": result.get("completed") is not False,
        "failed": bool(result.get("failed")),
        "interrupted": bool(result.get("interrupted")),
        "partial": bool(result.get("partial")),
        "turn_exit_reason": str(result.get("turn_exit_reason") or "")[:120],
    }
    return _digest(descriptor), descriptor


class GatewayContinuationStore:
    """Async-compatible facade over ``SessionDB`` / ``AsyncSessionDB``."""

    def __init__(
        self,
        session_db: Any,
        *,
        owner: Optional[str] = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        if session_db is None:
            raise ValueError("session_db is required")
        self._db = session_db
        self.owner = owner or _owner_stamp()
        self.lease_seconds = max(3.0, float(lease_seconds))

    async def claim_entry(self, entry: Any) -> ClaimDecision:
        """Create/idempotently recover and claim one resume marker."""

        session_key = str(getattr(entry, "session_key", "") or "")
        session_id = str(getattr(entry, "session_id", "") or "") or None
        continuation_id = str(getattr(entry, "resume_task_id", "") or "")
        if not session_key:
            return ClaimDecision("invalid")
        if not continuation_id:
            continuation_id = uuid.uuid4().hex
            try:
                entry.resume_task_id = continuation_id
            except Exception:
                pass

        await _call(self._db, "reap_expired_durable_continuation_claims")
        record = await _call(
            self._db, "get_durable_continuation", continuation_id
        )
        if record is None:
            rows = await _call(
                self._db,
                "list_durable_continuations",
                session_key=session_key,
                kind=KIND,
            )
            active = [row for row in rows if row.get("state") in ACTIVE_STATES]
            if active:
                # A different active identity is never silently adopted: the JSON
                # marker must be reconciled explicitly rather than replaying a
                # possibly newer/older obligation under the wrong generation.
                return ClaimDecision("conflict", active[-1])
            generation = max(
                (int(row.get("generation") or 0) for row in rows), default=0
            ) + 1
            descriptor = {
                "source": "gateway_startup_recovery",
                "resume_reason": str(
                    getattr(entry, "resume_reason", "restart_timeout")
                    or "restart_timeout"
                )[:120],
            }
            record = await _call(
                self._db,
                "create_durable_continuation",
                continuation_id=continuation_id,
                session_key=session_key,
                session_id=session_id,
                origin_turn_id=continuation_id,
                kind=KIND,
                generation=generation,
                input_digest=_digest(
                    {
                        "session_key": session_key,
                        "session_id": session_id,
                        "continuation_id": continuation_id,
                        "resume_reason": descriptor["resume_reason"],
                    }
                ),
                descriptor=descriptor,
            )

        state = str(record.get("state") or "")
        if state in TERMINAL_STATES:
            return ClaimDecision("terminal", record)
        if state == "waiting_unknown_effect":
            return ClaimDecision("unknown_effect", record)
        if state == "claimed" and str(record.get("claim_owner") or "") == self.owner:
            # A synthetic wake may be displaced by a real inbound event after
            # this process claimed it but before adapter dispatch. Reuse the
            # exact same-process token on the next post-delivery retry rather
            # than stranding the obligation until lease expiry. Per-session
            # run-generation/sentinel fencing still prevents concurrent turns.
            existing_token = str(record.get("claim_token") or "")
            if existing_token:
                claim = ContinuationClaim(
                    continuation_id=str(record["continuation_id"]),
                    generation=int(record["generation"]),
                    owner=self.owner,
                    claim_token=existing_token,
                )
                renewed = await self.renew(claim)
                if renewed:
                    current = await _call(
                        self._db,
                        "get_durable_continuation",
                        record["continuation_id"],
                    )
                    return ClaimDecision("claimed", current or record, claim)
        if state != "pending":
            return ClaimDecision("busy", record)

        generation = int(record["generation"])
        claim_token = uuid.uuid4().hex
        claimed = await _call(
            self._db,
            "claim_durable_continuation",
            str(record["continuation_id"]),
            generation,
            owner=self.owner,
            claim_token=claim_token,
            lease_seconds=self.lease_seconds,
        )
        if claimed is None:
            current = await _call(
                self._db, "get_durable_continuation", record["continuation_id"]
            )
            return ClaimDecision("busy", current or record)
        claim = ContinuationClaim(
            continuation_id=str(claimed["continuation_id"]),
            generation=int(claimed["generation"]),
            owner=self.owner,
            claim_token=claim_token,
        )
        return ClaimDecision("claimed", claimed, claim)

    async def mark_effect_started(self, claim: ContinuationClaim) -> bool:
        return bool(
            await _call(
                self._db,
                "mark_durable_continuation_effect_started",
                claim.continuation_id,
                claim.generation,
                owner=claim.owner,
                claim_token=claim.claim_token,
            )
        )

    async def mark_unknown_effect(self, claim: ContinuationClaim) -> bool:
        """Fail closed immediately when a fenced gateway attempt raises."""
        return bool(
            await _call(
                self._db,
                "mark_durable_continuation_effect_unknown",
                claim.continuation_id,
                claim.generation,
                owner=claim.owner,
                claim_token=claim.claim_token,
            )
        )

    async def renew(self, claim: ContinuationClaim) -> bool:
        return bool(
            await _call(
                self._db,
                "renew_durable_continuation_claim",
                claim.continuation_id,
                claim.generation,
                owner=claim.owner,
                claim_token=claim.claim_token,
                lease_seconds=self.lease_seconds,
            )
        )

    async def owns_claim(self, claim: ContinuationClaim) -> bool:
        record = await _call(
            self._db, "get_durable_continuation", claim.continuation_id
        )
        return bool(
            record
            and int(record.get("generation") or -1) == claim.generation
            and record.get("state") == "claimed"
            and record.get("claim_owner") == claim.owner
            and record.get("claim_token") == claim.claim_token
        )

    async def complete(
        self,
        claim: ContinuationClaim,
        agent_result: Any,
        *,
        delivered: bool,
    ) -> bool:
        outcome_digest, descriptor = summarize_outcome(
            agent_result, delivered=delivered
        )
        record = await _call(
            self._db, "get_durable_continuation", claim.continuation_id
        )
        if not record or int(record.get("generation") or -1) != claim.generation:
            return False
        state = str(record.get("state") or "")
        if state == "completed":
            return True
        if state == "waiting_unknown_effect":
            return bool(
                await _call(
                    self._db,
                    "resolve_durable_continuation_unknown_effect",
                    claim.continuation_id,
                    claim.generation,
                    state="completed",
                    outcome_digest=outcome_digest,
                    outcome_descriptor=descriptor,
                )
            )
        if state != "claimed":
            return False
        return bool(
            await _call(
                self._db,
                "terminalize_durable_continuation",
                claim.continuation_id,
                claim.generation,
                owner=claim.owner,
                claim_token=claim.claim_token,
                state="completed",
                outcome_digest=outcome_digest,
                outcome_descriptor=descriptor,
            )
        )

    async def complete_identity(
        self,
        *,
        continuation_id: str,
        generation: int,
        owner: str,
        claim_token: str,
        delivered: bool = True,
    ) -> bool:
        """Complete from a recovered delivery-ledger row."""

        return await self.complete(
            ContinuationClaim(
                continuation_id=continuation_id,
                generation=int(generation),
                owner=owner,
                claim_token=claim_token,
            ),
            {},
            delivered=delivered,
        )
