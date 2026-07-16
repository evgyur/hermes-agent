"""Narrow operations broker with pre-lookup, scoped approval, and readback."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping


class OpsBlocker(RuntimeError):
    pass


class FakeOpsBackend:
    def __init__(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        self._records = deepcopy(dict(records))
        self.lookup_count = 0
        self.apply_count = 0

    def lookup(self, target_id: str) -> dict[str, Any] | None:
        self.lookup_count += 1
        record = self._records.get(target_id)
        return deepcopy(record) if record is not None else None

    def set_state(self, target_id: str, desired_state: str) -> None:
        if target_id not in self._records:
            raise OpsBlocker("H20_OPS_TARGET_NOT_FOUND")
        self.apply_count += 1
        self._records[target_id]["state"] = desired_state


class OpsBroker:
    _ALLOWED_ROUTES = {"ops_broker", "kanban", "status"}

    def __init__(
        self,
        backend: FakeOpsBackend,
        *,
        approval_verifier: Callable[[Mapping[str, Any], Mapping[str, str]], bool] | None = None,
    ) -> None:
        self.backend = backend
        self.approval_verifier = approval_verifier

    def mutate(
        self,
        *,
        actor_role: str,
        target_id: str,
        desired_state: str,
        environment: str,
        dry_run: bool,
        route: str = "ops_broker",
        approval: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if actor_role != "admin":
            raise OpsBlocker("H20_OPS_ADMIN_REQUIRED")
        if route not in self._ALLOWED_ROUTES:
            raise OpsBlocker("H20_OPS_ROUTE_BYPASS")
        if not target_id or not desired_state:
            raise OpsBlocker("H20_OPS_SCOPE_INVALID")
        if environment not in {"beta", "production"}:
            raise OpsBlocker("H20_OPS_ENVIRONMENT_INVALID")
        expected_scope = {
            "environment": environment,
            "target_id": target_id,
            "desired_state": desired_state,
        }
        if environment == "production" and not dry_run:
            if approval is None or self.approval_verifier is None:
                raise OpsBlocker("H20_OPS_PRODUCTION_APPROVAL_REQUIRED")
            if not self.approval_verifier(approval, expected_scope):
                raise OpsBlocker("H20_OPS_PRODUCTION_APPROVAL_MISMATCH")
        before = self.backend.lookup(target_id)
        if before is None:
            raise OpsBlocker("H20_OPS_TARGET_NOT_FOUND")
        if dry_run:
            return {
                "ok": True,
                "target_id": target_id,
                "before": before,
                "after": deepcopy(before),
                "applied": False,
                "verified": True,
            }
        self.backend.set_state(target_id, desired_state)
        after = self.backend.lookup(target_id)
        if after is None or after.get("state") != desired_state:
            raise OpsBlocker("H20_OPS_READBACK_MISMATCH")
        return {
            "ok": True,
            "target_id": target_id,
            "before": before,
            "after": after,
            "applied": True,
            "verified": True,
        }
