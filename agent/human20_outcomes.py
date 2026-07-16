"""Typed Human20 action outcomes and verifier-backed completion language."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Callable, Mapping, Sequence


class OutcomeKind(str, Enum):
    SUCCESS = "success"
    VALID_BLOCKER = "valid_blocker"
    POLICY_DENIAL = "policy_denial"
    TECHNICAL_FAILURE = "technical_failure"


@dataclass(frozen=True)
class TypedOutcome:
    kind: OutcomeKind
    code: str
    user_text: str
    completed: bool


@dataclass(frozen=True)
class ActionReceipt:
    actor_tier: str
    policy_decision: str
    target_id: str
    tool: str
    effect: str
    verifier: Mapping[str, str]
    evidence_refs: Sequence[str]
    outcome: str
    rollback_point: str


def typed_outcome(
    kind: OutcomeKind,
    code: str,
    *,
    verified_receipt: ActionReceipt | None = None,
    receipt_verifier: Callable[[ActionReceipt], bool] | None = None,
) -> TypedOutcome:
    if kind is OutcomeKind.SUCCESS:
        if verified_receipt is None or _completion_blocker(verified_receipt, receipt_verifier) is not None:
            raise ValueError("H20_SUCCESS_REQUIRES_VERIFIED_RECEIPT")
        return TypedOutcome(kind, code, "Действие подтверждено проверяющим.", True)
    messages = {
        OutcomeKind.VALID_BLOCKER: "Источник недоступен; действие не выполнено.",
        OutcomeKind.POLICY_DENIAL: "Политика запрещает это действие без нужного разрешения.",
        OutcomeKind.TECHNICAL_FAILURE: "Технический сбой; подтверждённого результата нет.",
    }
    return TypedOutcome(kind, code, messages[kind], False)


def _completion_blocker(
    receipt: ActionReceipt,
    receipt_verifier: Callable[[ActionReceipt], bool] | None,
) -> str | None:
    if receipt.outcome != "success":
        return "H20_COMPLETION_OUTCOME_NOT_SUCCESS"
    if receipt.policy_decision != "allow":
        return "H20_COMPLETION_POLICY_NOT_ALLOWED"
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", receipt.target_id):
        return "H20_COMPLETION_TARGET_INVALID"
    if not receipt.tool or not receipt.effect:
        return "H20_COMPLETION_TARGET_REQUIRED"
    if not receipt.evidence_refs:
        return "H20_COMPLETION_EVIDENCE_REQUIRED"
    if receipt.verifier.get("status") != "verified":
        return "H20_COMPLETION_VERIFIER_REQUIRED"
    if receipt.verifier.get("target_id") != receipt.target_id:
        return "H20_COMPLETION_TARGET_MISMATCH"
    if not receipt.rollback_point:
        return "H20_COMPLETION_ROLLBACK_REQUIRED"
    if receipt_verifier is None:
        return "H20_COMPLETION_VERIFIER_REQUIRED"
    try:
        trusted = receipt_verifier(receipt)
    except Exception:
        trusted = False
    if trusted is not True:
        return "H20_COMPLETION_VERIFIER_REJECTED"
    return None


def render_action_result(
    receipt: ActionReceipt | None,
    *,
    receipt_verifier: Callable[[ActionReceipt], bool] | None = None,
) -> str:
    if receipt is None:
        return "H20_COMPLETION_RECEIPT_REQUIRED · подтверждённого результата нет"
    blocker = _completion_blocker(receipt, receipt_verifier)
    if blocker:
        return blocker + " · подтверждённого результата нет"
    return f"Выполнено · target={receipt.target_id} · verifier=verified"


def render_normal_reply(text: str) -> str:
    return text
