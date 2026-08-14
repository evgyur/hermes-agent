"""Fail-closed skill-routing policy for bounded canaries.

Persistent config is only an allow-list. Lean routing additionally requires an
explicit task override, an explicit low-risk classification, and an explicit
empty protected-boundary set. Missing or unknown input stays conservative.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import os
from typing import Any

CONSERVATIVE = "conservative"
LEAN_CANARY = "lean_canary"
VALID_ROUTING_POLICIES = frozenset((CONSERVATIVE, LEAN_CANARY))

PROTECTED_BOUNDARIES = frozenset(
    (
        "access",
        "auth",
        "authorization",
        "control_plane",
        "destructive",
        "dns",
        "exact_candidate",
        "identity",
        "irreversible",
        "live_activation",
        "mass_messaging",
        "network_control",
        "payment",
        "privacy",
        "production",
        "public_messaging",
        "release_certification",
        "secrets",
    )
)


def normalize_routing_policy(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return normalized if normalized in VALID_ROUTING_POLICIES else CONSERVATIVE


def _normalized_boundaries(values: Iterable[Any] | None) -> set[str] | None:
    if values is None:
        return None
    return {
        str(value).strip().lower().replace("-", "_")
        for value in values
        if str(value).strip()
    }


def resolve_skill_routing_policy(
    *,
    config_policy: Any = CONSERVATIVE,
    task_override: Any = None,
    risk_class: Any = None,
    protected_boundaries: Iterable[Any] | None = None,
) -> str:
    """Return the effective policy; every uncertainty fails conservative."""
    if normalize_routing_policy(config_policy) != LEAN_CANARY:
        return CONSERVATIVE
    if normalize_routing_policy(task_override) != LEAN_CANARY:
        return CONSERVATIVE
    if str(risk_class or "").strip().lower().replace("-", "_") != "low":
        return CONSERVATIVE

    boundaries = _normalized_boundaries(protected_boundaries)
    if boundaries is None:
        return CONSERVATIVE
    if boundaries & PROTECTED_BOUNDARIES:
        return CONSERVATIVE
    # Unknown boundary labels are not silently treated as safe.
    if boundaries - PROTECTED_BOUNDARIES:
        return CONSERVATIVE
    return LEAN_CANARY


CANARY_CONFIG_ENV = "HERMES_SKILL_ROUTING_CANARY_CONFIG"
CANARY_TASK_ENV = "HERMES_SKILL_ROUTING_TASK_OVERRIDE"
CANARY_RISK_ENV = "HERMES_SKILL_ROUTING_RISK_CLASS"
CANARY_BOUNDARIES_ENV = "HERMES_SKILL_ROUTING_PROTECTED_BOUNDARIES"


def agent_skill_routing_boundaries(
    agent: Any,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...] | None:
    """Return explicit task boundaries; ``None`` means unclassified."""
    env = environ if environ is not None else os.environ
    marker = object()
    protected: Any = getattr(agent, "skill_routing_protected_boundaries", marker)
    if protected is marker:
        if CANARY_BOUNDARIES_ENV not in env:
            return None
        raw = env.get(CANARY_BOUNDARIES_ENV, "")
        protected = tuple(item.strip() for item in raw.split(",") if item.strip())
    normalized = _normalized_boundaries(protected)
    return None if normalized is None else tuple(sorted(normalized))


def resolve_agent_skill_routing_policy(
    agent: Any,
    *,
    config_policy: Any,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve agent attributes plus fresh-process canary environment gates."""
    env = environ if environ is not None else os.environ
    configured = env.get(CANARY_CONFIG_ENV) or config_policy
    task_override = getattr(agent, "skill_routing_policy_override", None)
    if task_override is None:
        task_override = env.get(CANARY_TASK_ENV)
    risk_class = getattr(agent, "skill_routing_risk_class", None)
    if risk_class is None:
        risk_class = env.get(CANARY_RISK_ENV)

    protected = agent_skill_routing_boundaries(agent, environ=env)

    return resolve_skill_routing_policy(
        config_policy=configured,
        task_override=task_override,
        risk_class=risk_class,
        protected_boundaries=protected,
    )


_BOUNDARY_SPECIALISTS = {
    "auth": "authentication/authorization security specialist",
    "access": "access-control specialist",
    "payment": "payment/idempotency specialist",
    "privacy": "privacy/confidentiality specialist",
    "secret": "secret-handling specialist",
    "production": "production rollout specialist",
    "destructive": "destructive-effect safety specialist",
    "public_messaging": "public/mass-messaging safety specialist",
    "dns_network_control": "DNS/network-control specialist",
    "control_plane": "control-plane specialist",
    "exact_candidate": "exact-candidate integrity specialist",
    "formal_release": "formal release-assurance specialist",
}


def protected_boundary_guidance(boundaries: Iterable[Any] | None) -> str:
    """Render a fail-closed reminder only for explicit task classifications."""
    normalized = _normalized_boundaries(boundaries)
    if not normalized:
        return ""
    specialists = sorted(
        {_BOUNDARY_SPECIALISTS.get(boundary, "the relevant protected specialist") for boundary in normalized}
    )
    return (
        "### Explicit protected-boundary gate\n"
        "This task has an explicit protected classification, so lean routing is disabled. Before the next action, "
        "load every independently triggered protected specialist, including "
        + ", ".join(specialists)
        + ". Do not substitute a generic workflow or general coding skill for a protected specialist.\n"
    )


CONSERVATIVE_GUIDANCE = (
    "## Skills (mandatory)\n"
    "Before replying, scan the skills below. If a skill matches or is even partially relevant to your task, "
    "you MUST load it with skill_view(name) and follow its instructions. Err on the side of loading — it is "
    "always better to have context you don't need than to miss critical steps, pitfalls, or established workflows. "
    "Skills contain specialized knowledge — API endpoints, tool-specific commands, and proven workflows that "
    "outperform general-purpose approaches. Load the skill even if you think you could handle the task with basic "
    "tools like web_search or terminal. Skills also encode the user's preferred approach, conventions, and quality "
    "standards for tasks like code review, planning, and testing — load them even for tasks you already know how to "
    "do, because the skill defines how it should be done here.\n"
)

LEAN_CANARY_GUIDANCE = (
    "## Skills (lean canary; low-risk task only)\n"
    "Load a skill only when its explicit trigger is independently satisfied and reading its body will materially "
    "change the next action. Related-skill metadata is discovery-only and never triggers loading. Generic words such "
    "as bug, fix, commit, push, ship, done, fully, or non-trivial are not sufficient. For a reversible low-risk coding "
    "task already covered by the default working contract, follow the Shaw-compatible inspect → smallest viable "
    "change → focused verification loop without loading a coding skill. Do not load the Shaw body merely because a "
    "task is coding; load it only when its explicit trigger is independently satisfied and its body will change the "
    "next action. Strict TDD, independent review, durable state, and release certification remain opt-in unless their "
    "own trigger or repository policy is present. If any auth, access, payment, privacy, secret, production, destructive, "
    "public messaging, DNS/network control, control-plane, exact-candidate, or release boundary appears, stop using lean "
    "routing and follow the conservative specialist policy.\n"
)


def skill_routing_guidance(policy: Any) -> str:
    return LEAN_CANARY_GUIDANCE if normalize_routing_policy(policy) == LEAN_CANARY else CONSERVATIVE_GUIDANCE
