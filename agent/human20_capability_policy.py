"""Fail-closed Human20 capability policy for gateway and tool dispatch.

The policy is intentionally independent of prompt text.  Callers construct a
trusted :class:`CapabilityContext` from gateway metadata and ask for a decision
before invoking a tool, quick command, or privileged helper.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml


DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "human20_capabilities.yaml"
VALID_STATES = {"allow", "deny", "approval"}


@dataclass(frozen=True)
class CapabilityContext:
    actor_id: str
    role: str
    context: str
    chat_id: str = ""


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    capability: str
    reason_code: str
    message: str
    before_dispatch: bool = True


@dataclass(frozen=True)
class CapabilityPolicy:
    version: int
    roles: tuple[str, ...]
    contexts: tuple[str, ...]
    capabilities: tuple[str, ...]
    matrix: Mapping[str, Mapping[str, Mapping[str, str]]]
    admin_ids: frozenset[str]
    member_ids: frozenset[str]
    member_chat_ids: frozenset[str]
    sandbox_root: Path
    candidate_root: Path
    approval_root: Path
    tool_routes: Mapping[str, str]
    enforcement_surfaces: frozenset[str]

    @classmethod
    def load(cls, path: str | Path = DEFAULT_POLICY_PATH) -> "CapabilityPolicy":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("policy root must be a mapping")
        schema = raw.get("schema") or {}
        actors = raw.get("actors") or {}
        paths = raw.get("paths") or {}
        routes = raw.get("tool_routes") or {}
        policy = cls(
            version=int(raw.get("version", 0)),
            roles=tuple(str(value) for value in schema.get("roles", [])),
            contexts=tuple(str(value) for value in schema.get("contexts", [])),
            capabilities=tuple(str(value) for value in schema.get("capabilities", [])),
            matrix=raw.get("matrix") or {},
            admin_ids=frozenset(str(value) for value in actors.get("admin_ids", [])),
            member_ids=frozenset(str(value) for value in actors.get("member_ids", [])),
            member_chat_ids=frozenset(str(value) for value in actors.get("member_chat_ids", [])),
            sandbox_root=Path(str(paths.get("sandbox_root", ""))).expanduser(),
            candidate_root=Path(str(paths.get("candidate_root", ""))).expanduser(),
            approval_root=Path(str(paths.get("approval_root", ""))).expanduser(),
            tool_routes={str(key): str(value) for key, value in routes.items()},
            enforcement_surfaces=frozenset(str(value) for value in raw.get("enforcement_surfaces", [])),
        )
        errors = policy.validation_errors()
        if errors:
            raise ValueError("; ".join(errors))
        return policy

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        if self.version != 1:
            errors.append("version must be 1")
        if not self.roles or not self.contexts or not self.capabilities:
            errors.append("roles, contexts and capabilities must be non-empty")
        if set(self.roles) != {"guest", "member", "admin"}:
            errors.append("roles must be guest/member/admin")
        if set(self.contexts) != {"dm", "group"}:
            errors.append("contexts must be dm/group")
        for role in self.roles:
            role_matrix = self.matrix.get(role)
            if not isinstance(role_matrix, dict):
                errors.append(f"missing matrix role {role}")
                continue
            for context in self.contexts:
                cells = role_matrix.get(context)
                if not isinstance(cells, dict):
                    errors.append(f"missing matrix context {role}.{context}")
                    continue
                missing = set(self.capabilities) - set(cells)
                extra = set(cells) - set(self.capabilities)
                invalid = {key for key, value in cells.items() if value not in VALID_STATES}
                if missing:
                    errors.append(f"missing cells {role}.{context}: {sorted(missing)}")
                if extra:
                    errors.append(f"extra cells {role}.{context}: {sorted(extra)}")
                if invalid:
                    errors.append(f"invalid states {role}.{context}: {sorted(invalid)}")
        bad_routes = {key: value for key, value in self.tool_routes.items() if value not in self.capabilities}
        if bad_routes:
            errors.append(f"unknown route capabilities: {bad_routes}")
        if not self.sandbox_root.is_absolute() or not self.candidate_root.is_absolute() or not self.approval_root.is_absolute():
            errors.append("sandbox_root, candidate_root and approval_root must be absolute")
        required_surfaces = {"direct-tool", "quick-command", "helper"}
        if not required_surfaces <= self.enforcement_surfaces:
            errors.append("missing enforcement surfaces")
        return errors

    def context_for(
        self,
        *,
        actor_id: str | None,
        chat_type: str | None,
        chat_id: str | None = None,
    ) -> CapabilityContext:
        actor = str(actor_id or "")
        chat = str(chat_id or "")
        if actor in self.admin_ids:
            role = "admin"
        elif actor in self.member_ids or (actor and chat in self.member_chat_ids):
            role = "member"
        else:
            role = "guest"
        context = "dm" if str(chat_type or "").lower() in {"dm", "private"} else "group"
        return CapabilityContext(actor_id=actor, role=role, context=context, chat_id=chat)

    def coverage(self) -> tuple[int, int, float]:
        total = len(self.roles) * len(self.contexts) * len(self.capabilities)
        covered = 0
        for role in self.roles:
            for context in self.contexts:
                cells = self.matrix.get(role, {}).get(context, {})
                covered += sum(1 for capability in self.capabilities if cells.get(capability) in VALID_STATES)
        return covered, total, covered / total if total else 0.0


def _decision(allowed: bool, capability: str, reason: str, message: str) -> CapabilityDecision:
    return CapabilityDecision(allowed, capability, reason, message)


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _within(path: str | Path, root: Path) -> bool:
    try:
        _resolved(path).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _credential_path(path: str) -> bool:
    lowered = path.lower()
    return any(
        marker in lowered
        for marker in ("/.ssh", "/secrets", ".env", "credential", "token", "apikey", "api_key", "/etc/shadow")
    )


def _path_capability(policy: CapabilityPolicy, actor: CapabilityContext, path: str) -> tuple[str, str | None]:
    if _credential_path(path):
        return "credentials", "H20_CAP_CREDENTIALS_DENIED"
    own_sandbox = policy.sandbox_root / actor.actor_id
    if actor.role == "member":
        if _within(path, own_sandbox):
            return "sandbox_artifact", None
        return "sandbox_artifact", "H20_CAP_PATH_OUTSIDE_SANDBOX"
    if actor.role == "admin":
        if _within(path, policy.candidate_root):
            return "code_beta", None
        return "private_data", None
    return "private_data", None


_COMMAND_CAPABILITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("credentials", re.compile(r"(?i)(/secrets|\.env\b|credential|api[_-]?key|token\b|/etc/shadow)")),
    ("payment", re.compile(r"(?i)\b(payment|billing|invoice|prodamus|tochka|refund|payout)\b")),
    ("access", re.compile(r"(?i)\b(grant|revoke|entitlement|allowlist|access)\b")),
    ("public_send", re.compile(r"(?i)\b(broadcast|mass[_ -]?send|public[_ -]?send|send[_ -]?message)\b")),
    ("gateway_control", re.compile(r"(?i)(systemctl\s+(restart|stop|start)|hermes\s+gateway|gateway.*restart)")),
    ("production", re.compile(r"(?i)(\bdeploy\b|\bpromote\b|git\s+push|/apps/hermes-agent(?:/|\s|$)|\bprod(?:uction)?\b)")),
)


def _command_capability(policy: CapabilityPolicy, args: Mapping[str, Any]) -> str:
    command = str(args.get("command") or args.get("code") or "")
    for capability, pattern in _COMMAND_CAPABILITY_PATTERNS:
        if pattern.search(command):
            return capability
    workdir = str(args.get("workdir") or "")
    if workdir and _within(workdir, policy.candidate_root):
        return "code_beta"
    return "infrastructure"


def _route_tool(
    policy: CapabilityPolicy,
    actor: CapabilityContext,
    tool_name: str,
    args: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    if tool_name == "tool_call":
        underlying = str(args.get("name") or "")
        underlying_args = args.get("arguments")
        if not isinstance(underlying_args, dict):
            underlying_args = {}
        if not underlying:
            return None, "H20_CAP_UNKNOWN_ROUTE"
        return _route_tool(policy, actor, underlying, underlying_args)
    if tool_name in {"write_file", "patch"}:
        path = str(args.get("path") or "")
        if not path:
            return None, "H20_CAP_UNKNOWN_ROUTE"
        if _credential_path(path):
            return "credentials", "H20_CAP_CREDENTIALS_DENIED"
        if actor.role == "member":
            own_sandbox = policy.sandbox_root / actor.actor_id
            if _within(path, own_sandbox):
                return "sandbox_artifact", None
            return "sandbox_artifact", "H20_CAP_PATH_OUTSIDE_SANDBOX"
        if actor.role == "admin":
            return ("code_beta", None) if _within(path, policy.candidate_root) else ("production", None)
        return "code_beta", None
    if tool_name in {"read_file", "search_files"}:
        path = str(args.get("path") or "")
        if not path:
            return None, "H20_CAP_UNKNOWN_ROUTE"
        return _path_capability(policy, actor, path)
    if tool_name in {"terminal", "execute_code"}:
        return _command_capability(policy, args), None
    capability = policy.tool_routes.get(tool_name)
    if capability is None:
        return None, "H20_CAP_UNKNOWN_ROUTE"
    return capability, None


def _canonical_action(args: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in args.items() if key != "approval_receipt"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_approval(
    policy: CapabilityPolicy,
    actor: CapabilityContext,
    capability: str,
    args: Mapping[str, Any],
) -> CapabilityDecision:
    receipt = args.get("approval_receipt")
    if not isinstance(receipt, dict):
        return _decision(False, capability, "H20_CAP_APPROVAL_REQUIRED", "Exact approval receipt required")
    expected_hash = hashlib.sha256(_canonical_action(args).encode()).hexdigest()
    expected = {
        "actor_id": actor.actor_id,
        "capability": capability,
        "context": actor.context,
        "action_sha256": expected_hash,
    }
    approval_id = str(receipt.get("id") or "")
    if re.fullmatch(r"APPROVAL-[A-Z0-9-]+", approval_id) is None:
        return _decision(False, capability, "H20_CAP_APPROVAL_MISMATCH", "Approval id is invalid")
    if any(str(receipt.get(key) or "") != value for key, value in expected.items()):
        return _decision(False, capability, "H20_CAP_APPROVAL_MISMATCH", "Approval does not match this action")
    if str(receipt.get("approved_by") or "") not in policy.admin_ids:
        return _decision(False, capability, "H20_CAP_APPROVAL_MISMATCH", "Approver is not an admin")
    try:
        expires = datetime.fromisoformat(str(receipt.get("expires_at") or "").replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            return _decision(False, capability, "H20_CAP_APPROVAL_EXPIRED", "Approval has expired")
    except (TypeError, ValueError):
        return _decision(False, capability, "H20_CAP_APPROVAL_MISMATCH", "Approval expiry is invalid")
    receipt_path = policy.approval_root / f"{approval_id}.json"
    try:
        if not receipt_path.is_file():
            return _decision(False, capability, "H20_CAP_APPROVAL_NOT_FOUND", "Approval is absent from ledger")
        if receipt_path.stat().st_mode & 0o077:
            return _decision(False, capability, "H20_CAP_APPROVAL_INSECURE", "Approval ledger record permissions are unsafe")
        ledger_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _decision(False, capability, "H20_CAP_APPROVAL_INVALID", "Approval ledger record is unreadable")
    if ledger_receipt != receipt:
        return _decision(False, capability, "H20_CAP_APPROVAL_MISMATCH", "Approval does not exactly match ledger")
    return _decision(True, capability, "H20_CAP_APPROVAL_VALID", "Capability allowed by exact approval")


def _evaluate_capability(
    policy: CapabilityPolicy,
    actor: CapabilityContext,
    capability: str | None,
    args: Mapping[str, Any],
    route_reason: str | None = None,
) -> CapabilityDecision:
    if actor.role not in policy.roles:
        return _decision(False, capability or "unknown", "H20_CAP_INVALID_ACTOR", "Actor role is not declared")
    if actor.context not in policy.contexts:
        return _decision(False, capability or "unknown", "H20_CAP_INVALID_CONTEXT", "Context is not declared")
    if capability is None or capability not in policy.capabilities:
        return _decision(False, capability or "unknown", route_reason or "H20_CAP_UNKNOWN_ROUTE", "Route is not declared")
    if route_reason:
        return _decision(False, capability, route_reason, "Path or route boundary denied")
    state = policy.matrix[actor.role][actor.context][capability]
    if state == "allow":
        return _decision(True, capability, "H20_CAP_ALLOWED", "Capability allowed")
    if state == "approval":
        return _validate_approval(policy, actor, capability, args)
    return _decision(False, capability, "H20_CAP_ROLE_DENIED", "Capability denied for actor and context")


def evaluate_tool(
    policy: CapabilityPolicy,
    actor: CapabilityContext,
    tool_name: str,
    args: Mapping[str, Any] | None,
) -> CapabilityDecision:
    safe_args = args if isinstance(args, Mapping) else {}
    capability, route_reason = _route_tool(policy, actor, str(tool_name), safe_args)
    return _evaluate_capability(policy, actor, capability, safe_args, route_reason)


def evaluate_quick_command(
    policy: CapabilityPolicy,
    actor: CapabilityContext,
    command_name: str,
    command_config: Mapping[str, Any] | None,
) -> CapabilityDecision:
    config = command_config if isinstance(command_config, Mapping) else {}
    explicit = str(config.get("capability") or "")
    capability = explicit if explicit in policy.capabilities else _command_capability(
        policy, {"command": str(config.get("command") or command_name)}
    )
    args = {
        "command": str(config.get("command") or command_name),
        "approval_receipt": config.get("approval_receipt"),
    }
    return _evaluate_capability(policy, actor, capability, args)


def evaluate_helper(
    policy: CapabilityPolicy,
    actor: CapabilityContext,
    helper_name: str,
    args: Mapping[str, Any] | None,
) -> CapabilityDecision:
    safe_args = dict(args or {})
    safe_args.setdefault("command", helper_name)
    capability = _command_capability(policy, safe_args)
    return _evaluate_capability(policy, actor, capability, safe_args)


def configured_policy_path() -> Path:
    value = os.getenv("HUMAN20_CAPABILITIES_CONFIG", "").strip()
    return Path(value).expanduser() if value else DEFAULT_POLICY_PATH


def evaluate_agent_tool_if_configured(agent: Any, tool_name: str, args: Mapping[str, Any]) -> CapabilityDecision | None:
    path = configured_policy_path()
    if not path.exists():
        return None
    try:
        policy = CapabilityPolicy.load(path)
        actor = policy.context_for(
            actor_id=getattr(agent, "user_id", None),
            chat_type=getattr(agent, "chat_type", None),
            chat_id=getattr(agent, "chat_id", None),
        )
        return evaluate_tool(policy, actor, tool_name, args)
    except Exception:
        return _decision(False, "unknown", "H20_CAP_POLICY_INVALID", "Capability policy could not be validated")
