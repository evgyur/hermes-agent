"""Typed delivery ownership for completed agent turns.

The model-facing result remains a JSON-compatible dictionary, but delivery
ownership is normalized through :class:`TurnDeliveryControl` before any gateway
or platform code can enqueue text or artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, MutableMapping


DELIVERY_CONTROL_KEY = "delivery_control"


class DeliveryDisposition(str, Enum):
    """Exclusive authority for delivering one completed turn."""

    SEND = "SEND"
    DEFER = "DEFER"
    ALREADY_DELIVERED = "ALREADY_DELIVERED"


@dataclass(frozen=True)
class TurnDeliveryControl:
    """The fields needed to decide who may deliver a turn."""

    disposition: DeliveryDisposition = DeliveryDisposition.SEND
    barrier_id: str = ""
    defer_goal_evaluation: bool = False
    outcome_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "barrier_id": self.barrier_id,
            "defer_goal_evaluation": self.defer_goal_evaluation,
            "outcome_id": self.outcome_id,
        }


def _legacy_deferred(result: Mapping[str, Any]) -> bool:
    return (
        bool(result.get("suppress_delivery"))
        or bool(result.get("delivery_suppressed"))
        or bool(result.get("defer_goal_evaluation"))
    )


def _legacy_control(result: Mapping[str, Any]) -> TurnDeliveryControl:
    deferred = _legacy_deferred(result)
    already_delivered = bool(result.get("response_already_delivered"))
    if deferred:
        disposition = DeliveryDisposition.DEFER
    elif already_delivered:
        disposition = DeliveryDisposition.ALREADY_DELIVERED
    else:
        disposition = DeliveryDisposition.SEND
    return TurnDeliveryControl(
        disposition=disposition,
        barrier_id=str(result.get("parent_task_barrier_id") or ""),
        defer_goal_evaluation=bool(result.get("defer_goal_evaluation")),
        outcome_id=str(result.get("outcome_id") or ""),
    )


def _typed_control(raw: Any) -> TurnDeliveryControl | None:
    if isinstance(raw, TurnDeliveryControl):
        return raw
    if not isinstance(raw, Mapping):
        return None
    try:
        disposition = DeliveryDisposition(str(raw.get("disposition") or ""))
    except ValueError:
        return None
    return TurnDeliveryControl(
        disposition=disposition,
        barrier_id=str(raw.get("barrier_id") or ""),
        defer_goal_evaluation=bool(raw.get("defer_goal_evaluation")),
        outcome_id=str(raw.get("outcome_id") or ""),
    )


def normalize_delivery_control(
    result: Mapping[str, Any], *, logger: Any = None
) -> TurnDeliveryControl:
    """Return one fail-closed delivery control from typed and legacy fields.

    Missing control is ordinary compatibility and defaults to ``SEND``. During
    migration, legacy booleans remain readable. A malformed typed value or a
    contradiction between explicit typed and legacy authority resolves to
    ``DEFER``; diagnostics contain only enum/field names, never response text.
    """

    legacy = _legacy_control(result)
    if DELIVERY_CONTROL_KEY not in result:
        if _legacy_deferred(result) and bool(result.get("response_already_delivered")):
            if logger is not None:
                logger.warning(
                    "contradictory legacy delivery controls; deferring turn"
                )
            return TurnDeliveryControl(
                disposition=DeliveryDisposition.DEFER,
                barrier_id=legacy.barrier_id,
                defer_goal_evaluation=True,
                outcome_id=legacy.outcome_id,
            )
        return legacy

    typed = _typed_control(result.get(DELIVERY_CONTROL_KEY))
    if typed is None:
        if logger is not None:
            logger.warning("invalid typed delivery control; deferring turn")
        return TurnDeliveryControl(
            disposition=DeliveryDisposition.DEFER,
            barrier_id=legacy.barrier_id,
            defer_goal_evaluation=True,
            outcome_id=legacy.outcome_id,
        )

    legacy_has_disposition = bool(
        _legacy_deferred(result) or result.get("response_already_delivered")
    )
    disposition_conflict = (
        legacy_has_disposition and legacy.disposition != typed.disposition
    )
    barrier_conflict = bool(
        legacy.barrier_id
        and typed.barrier_id
        and legacy.barrier_id != typed.barrier_id
    )
    goal_conflict = bool(
        result.get("defer_goal_evaluation")
        and not typed.defer_goal_evaluation
    )
    outcome_conflict = bool(
        legacy.outcome_id
        and typed.outcome_id
        and legacy.outcome_id != typed.outcome_id
    )
    if disposition_conflict or barrier_conflict or goal_conflict or outcome_conflict:
        if logger is not None:
            logger.warning(
                "contradictory typed/legacy delivery controls; deferring turn: "
                "disposition=%s barrier=%s goal=%s outcome=%s",
                disposition_conflict,
                barrier_conflict,
                goal_conflict,
                outcome_conflict,
            )
        return TurnDeliveryControl(
            disposition=DeliveryDisposition.DEFER,
            barrier_id=typed.barrier_id or legacy.barrier_id,
            defer_goal_evaluation=True,
            outcome_id=typed.outcome_id or legacy.outcome_id,
        )
    return TurnDeliveryControl(
        disposition=typed.disposition,
        barrier_id=typed.barrier_id or legacy.barrier_id,
        defer_goal_evaluation=(
            typed.defer_goal_evaluation or legacy.defer_goal_evaluation
        ),
        outcome_id=typed.outcome_id or legacy.outcome_id,
    )


def normalize_gateway_turn_result(
    payload: Mapping[str, Any], source: Mapping[str, Any], *, logger: Any = None
) -> dict[str, Any]:
    """Create the sole gateway-facing representation of delivery authority."""

    control = normalize_delivery_control(source, logger=logger)
    normalized: MutableMapping[str, Any] = dict(payload)
    for key in ("turn_exit_reason", "completed"):
        if key in source and key not in normalized:
            normalized[key] = source[key]
    normalized[DELIVERY_CONTROL_KEY] = control.to_dict()

    # Compatibility fields remain available to old consumers during migration,
    # but absence is meaningful: downstream provenance code uses ``get(key,
    # fallback)`` to mark earlier queued outcomes as already delivered.  Never
    # materialize false controls or overwrite caller-supplied provenance.
    if control.disposition == DeliveryDisposition.DEFER:
        normalized["suppress_delivery"] = True
        normalized["delivery_suppressed"] = True
    elif control.disposition == DeliveryDisposition.ALREADY_DELIVERED:
        normalized["response_already_delivered"] = True
    if control.defer_goal_evaluation:
        normalized["defer_goal_evaluation"] = True
    if control.barrier_id:
        normalized["parent_task_barrier_id"] = control.barrier_id
    if control.outcome_id:
        normalized["outcome_id"] = control.outcome_id
    return dict(normalized)
