"""Privacy-safe counters and bounded incident receipts for Human20 runtime health."""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

_ALLOWED_OUTCOMES = {"success", "valid_blocker", "policy_denial", "technical_failure", "unknown"}
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _integer(value: object, *, low: int = 0, high: int = 10_000_000) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low


def _number(value: object, *, low: float = 0.0, high: float = 1_000_000.0) -> float:
    try:
        return round(max(low, min(high, float(value))), 6)
    except (TypeError, ValueError):
        return low


def sanitize_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    event: dict[str, Any] = {"call_count": _integer(raw.get("call_count"))}
    if "latency_ms" in raw:
        event["latency_ms"] = _integer(raw.get("latency_ms"))
    if "cost_units" in raw:
        event["cost_units"] = _number(raw.get("cost_units"))
    if "outcome" in raw:
        outcome = str(raw.get("outcome"))
        event["outcome"] = outcome if outcome in _ALLOWED_OUTCOMES else "unknown"
    if "failure_code" in raw:
        code = str(raw.get("failure_code"))
        event["failure_code"] = code if _CODE.fullmatch(code) else "REDACTED_FAILURE"
    if "denial_code" in raw:
        code = str(raw.get("denial_code"))
        event["denial_code"] = code if _CODE.fullmatch(code) else "REDACTED_DENIAL"
    return event


def _secure_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        if path.is_symlink():
            raise OSError("H20_OBSERVABILITY_SYMLINK_DENIED") from exc
        raise
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    path.chmod(0o600)


def build_ops_report(*, fixture: Path, alert_at: int, hard_stop_at: int, out: Path) -> dict[str, Any]:
    if alert_at != 6 or hard_stop_at != 8:
        raise ValueError("H20_OBSERVABILITY_BUDGET_MISMATCH")
    fixture = Path(fixture)
    if fixture.stat().st_size > 1_000_000:
        raise ValueError("H20_TELEMETRY_FIXTURE_TOO_LARGE")
    lines = fixture.read_text().splitlines()
    if len(lines) > 10_000:
        raise ValueError("H20_TELEMETRY_EVENT_LIMIT")
    events = []
    for line in lines:
        if line.strip():
            events.append(sanitize_event(json.loads(line)))
    events.sort(key=lambda item: item["call_count"])
    max_calls = max((event["call_count"] for event in events), default=0)
    alert_call = next((event["call_count"] for event in events if event["call_count"] >= alert_at), None)
    repeated_call = None
    failure_counts: Counter[str] = Counter()
    for event in events:
        code = event.get("failure_code")
        if code:
            failure_counts[str(code)] += 1
            if failure_counts[str(code)] >= 2:
                repeated_call = event["call_count"]
                break
    budget_call = next((event["call_count"] for event in events if event["call_count"] >= hard_stop_at), None)
    candidates = [(repeated_call, "identical_failure_limit"), (budget_call, "call_budget")]
    candidates = [(call, reason) for call, reason in candidates if call is not None]
    hard_stop_call, hard_stop_reason = min(candidates, default=(None, None), key=lambda pair: pair[0])
    outcomes = Counter(str(event.get("outcome", "unknown")) for event in events)
    denials = Counter(str(event["denial_code"]) for event in events if "denial_code" in event)
    incident_path = Path(out).with_suffix(".incident.json")
    incident = {
        "schema_version": 1, "redacted": True,
        "calls_observed": max_calls, "alert_at": alert_at, "alert_fired": alert_call is not None,
        "hard_stop_at": hard_stop_at, "hard_stop": hard_stop_call is not None,
        "hard_stop_call": hard_stop_call, "hard_stop_reason": hard_stop_reason,
        "failure_counts": dict(sorted(failure_counts.items())),
        "outcome_counts": dict(sorted(outcomes.items())), "denial_counts": dict(sorted(denials.items())),
        "latency_ms_total": sum(_integer(event.get("latency_ms")) for event in events),
        "cost_units_total": round(sum(_number(event.get("cost_units")) for event in events), 6),
    }
    _secure_write(incident_path, json.dumps(incident, indent=2, sort_keys=True) + "\n")
    report = "\n".join([
        "# Human20 runtime health",
        "",
        f"- Calls observed: {max_calls}",
        f"- Alert: {'fired at call ' + str(alert_call) if alert_call is not None else 'not fired'}",
        f"- Hard stop: {hard_stop_reason + ' at call ' + str(hard_stop_call) if hard_stop_call is not None else 'not fired'}",
        f"- Outcomes: `{json.dumps(dict(sorted(outcomes.items())), sort_keys=True)}`",
        f"- Incident receipt: [{incident_path.name}]({incident_path.name})",
        "- Privacy: redacted counters only; no prompt bodies, raw payloads, secrets, or stable user/content identifiers.",
        "",
    ])
    _secure_write(Path(out), report)
    return {
        "ok": True, "alert_fired": alert_call is not None, "alert_call": alert_call,
        "hard_stop": hard_stop_call is not None, "hard_stop_call": hard_stop_call,
        "hard_stop_reason": hard_stop_reason, "incident_receipt": str(incident_path),
        "events": len(events), "redacted": True,
    }
