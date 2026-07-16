"""Minimal, privacy-aware context and Human20 source-of-truth router."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

REQUIRED_DOMAINS = {"prices", "access", "payments", "site_state", "kanban", "team_identity"}
PRIVATE_GROUP_KEYS = {
    "personal_lesson",
    "lesson_progress",
    "progress",
    "homework",
    "current_lesson",
    "mcp_state",
    "personal_mcp",
}


@dataclass(frozen=True)
class ContextPlan:
    allowed: bool
    operations: tuple[dict[str, Any], ...]
    blocker_code: str | None = None
    redirect_to_dm: bool = False


@dataclass(frozen=True)
class FactResult:
    ok: bool
    domain: str
    value: Any = None
    receipt: dict[str, Any] | None = None
    blocker_code: str | None = None
    blocker: str | None = None


def load_source_map(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("source map root must be a mapping")
    errors = validate_source_map(raw)
    if errors:
        raise ValueError("; ".join(errors))
    return raw


def validate_source_map(raw: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if raw.get("version") != 1:
        errors.append("version must be 1")
    domains = raw.get("domains")
    if not isinstance(domains, dict):
        return errors + ["domains must be a mapping"]
    missing = REQUIRED_DOMAINS - set(domains)
    extra = set(domains) - REQUIRED_DOMAINS
    if missing:
        errors.append(f"missing domains: {sorted(missing)}")
    if extra:
        errors.append(f"unknown domains: {sorted(extra)}")
    for name, domain in domains.items():
        if not isinstance(domain, dict):
            errors.append(f"{name}: domain must be a mapping")
            continue
        sources = domain.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{name}: sources must be non-empty")
            continue
        mutable = [source for source in sources if isinstance(source, dict) and source.get("mutable") is True]
        canonical = [source for source in sources if isinstance(source, dict) and source.get("role") == "canonical"]
        if len(mutable) != 1:
            errors.append(f"{name}: expected exactly one mutable source, got {len(mutable)}")
        if len(canonical) != 1:
            errors.append(f"{name}: expected exactly one canonical source, got {len(canonical)}")
        if mutable and canonical and mutable[0] is not canonical[0]:
            errors.append(f"{name}: mutable source must be canonical")
        for source in sources:
            if not isinstance(source, dict):
                errors.append(f"{name}: source must be a mapping")
                continue
            for key in ("provider", "kind", "locator", "role", "mutable"):
                if key not in source:
                    errors.append(f"{name}: source missing {key}")
        if not str(domain.get("blocker_code") or "").startswith("H20_SOURCE_"):
            errors.append(f"{name}: stable blocker_code required")
        freshness = domain.get("freshness")
        if not isinstance(freshness, dict) or int(freshness.get("max_age_seconds", 0)) <= 0:
            errors.append(f"{name}: positive freshness.max_age_seconds required")
    return errors


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def resolve_fact(
    source_map: Mapping[str, Any],
    domain_name: str,
    *,
    observations: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> FactResult:
    domain = (source_map.get("domains") or {}).get(domain_name)
    if not isinstance(domain, dict):
        return FactResult(False, domain_name, blocker_code="H20_SOURCE_DOMAIN_UNKNOWN", blocker="unknown fact domain")
    canonical = next(source for source in domain["sources"] if source.get("role") == "canonical")
    provider = str(canonical["provider"])
    observed = dict((observations or {}).get(provider) or {})
    if not observed and "pinned_value" in canonical:
        observed = {
            "value": canonical["pinned_value"],
            "version": canonical.get("version"),
            "timestamp": canonical.get("verified_at"),
        }
    blocker_code = str(domain["blocker_code"])
    if "value" not in observed:
        return FactResult(False, domain_name, blocker_code=blocker_code, blocker=f"{provider}: canonical source unavailable")
    timestamp = _parse_timestamp(observed.get("timestamp"))
    max_age = int(domain["freshness"]["max_age_seconds"])
    current = now or datetime.now(timezone.utc)
    if timestamp is None or (current - timestamp).total_seconds() > max_age:
        return FactResult(False, domain_name, blocker_code=blocker_code, blocker=f"{provider}: canonical source stale")
    for other_provider, candidate in (observations or {}).items():
        if other_provider != provider and "value" in candidate and candidate["value"] != observed["value"]:
            return FactResult(False, domain_name, blocker_code=blocker_code, blocker=f"{provider}: conflicting source {other_provider}")
    receipt = {
        "source_provider": provider,
        "source_kind": canonical["kind"],
        "source_version": observed.get("version") or canonical.get("version"),
        "source_timestamp": timestamp.isoformat(),
        "locator": canonical["locator"],
    }
    return FactResult(True, domain_name, value=observed["value"], receipt=receipt)


def telegram_context_plan(
    *,
    reply_message_id: int | None = None,
    media_message_id: int | None = None,
    history_anchor_id: int | None = None,
    history_limit: int = 6,
    membership_known: bool = True,
    personal_request: bool = False,
) -> ContextPlan:
    if personal_request and not membership_known:
        return ContextPlan(False, (), "H20_CONTEXT_MEMBERSHIP_UNKNOWN", redirect_to_dm=True)
    operations: list[dict[str, Any]] = []
    if reply_message_id is not None:
        operations.append({"tool": "telegram-chip", "operation": "get_message", "message_id": reply_message_id, "read_only": True})
    if media_message_id is not None:
        operations.append({"tool": "telegram-chip", "operation": "get_media", "message_id": media_message_id, "read_only": True})
    if history_anchor_id is not None:
        operations.append({
            "tool": "telegram-chip",
            "operation": "get_history_window",
            "anchor_message_id": history_anchor_id,
            "limit": max(1, min(int(history_limit), 8)),
            "read_only": True,
        })
    if not operations:
        return ContextPlan(False, (), "H20_CONTEXT_NO_RETRIEVABLE_ANCHOR")
    return ContextPlan(True, tuple(operations))


def group_privacy_result(payload: Mapping[str, Any], *, personal_request: bool = False) -> dict[str, Any]:
    public = {key: value for key, value in payload.items() if key not in PRIVATE_GROUP_KEYS}
    removed = sorted(set(payload) & PRIVATE_GROUP_KEYS)
    return {
        "public_context": public,
        "removed_private_fields": removed,
        "redirect_to_dm": bool(personal_request or removed),
        "inline_cta": None,
        "personal_state_exposed": False,
    }


def replay_incident(source_map: Mapping[str, Any], incident_id: int) -> dict[str, Any]:
    if incident_id == 10889:
        result = resolve_fact(source_map, "prices")
        if not result.ok:
            return {"episode": incident_id, "status": "exact_blocker", "blocker_code": result.blocker_code, "blocker": result.blocker}
        return {"episode": incident_id, "status": "verified_fact", "prices": result.value, "receipt": result.receipt}
    plans = {
        9946: telegram_context_plan(history_anchor_id=9946, history_limit=6),
        10659: telegram_context_plan(media_message_id=10659),
        11228: telegram_context_plan(reply_message_id=11227),
    }
    plan = plans.get(incident_id)
    if plan is None:
        return {"episode": incident_id, "status": "exact_blocker", "blocker_code": "H20_CONTEXT_EPISODE_UNKNOWN", "blocker": "replay episode is not declared"}
    if not plan.allowed:
        return {"episode": incident_id, "status": "exact_blocker", "blocker_code": plan.blocker_code, "blocker": "telegram-chip context unavailable"}
    return {"episode": incident_id, "status": "context_plan", "operations": list(plan.operations)}
