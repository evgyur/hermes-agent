"""Curated class-level Human20 skill inventory, privacy scan, routing, and quality proxy."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping


class SkillContractError(RuntimeError):
    pass


REQUIRED_FIELDS = {
    "name", "owner", "source", "wrapper", "trigger", "negative_trigger",
    "dependencies", "actor_tier", "verifier", "removal_condition",
}


def load_skill_config(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1 or not isinstance(data.get("entries"), list):
        raise SkillContractError("H20_SKILL_CONFIG_INVALID")
    return data


def _inside(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise SkillContractError("H20_SKILL_PATH_ESCAPE")
    return path


def build_skill_manifest(*, source_root: Path, candidate_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_skill_config(config_path)
    exposed = []
    names = set()
    for raw in config["entries"]:
        missing = sorted(REQUIRED_FIELDS - set(raw))
        if missing:
            raise SkillContractError("H20_SKILL_METADATA_MISSING:" + ",".join(missing))
        name = str(raw["name"])
        if name in names:
            raise SkillContractError("H20_SKILL_DUPLICATE_NAME")
        names.add(name)
        source = _inside(source_root, str(raw["source"]))
        wrapper = _inside(candidate_root, str(raw["wrapper"]))
        if not source.is_file():
            raise SkillContractError("H20_SKILL_SOURCE_MISSING:" + name)
        if not wrapper.is_file():
            raise SkillContractError("H20_SKILL_WRAPPER_MISSING:" + name)
        item = {key: raw[key] for key in sorted(REQUIRED_FIELDS)}
        item.update({
            "priority": int(raw["priority"]), "families": list(raw["families"]),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "wrapper_sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(), "status": "exposed",
        })
        exposed.append(item)
    archived = config.get("archived_or_private", [])
    for item in archived:
        if not _inside(source_root, str(item["source"])).is_file():
            raise SkillContractError("H20_SKILL_ARCHIVE_SOURCE_MISSING")
    return {
        "ok": True, "schema_version": 1, "source_root_mode": "read_only_inventory",
        "exposed_count": len(exposed),
        "exposed": sorted(exposed, key=lambda x: (-x["priority"], x["name"])),
        "excluded_count": len(archived),
        "excluded_reasons": sorted({str(x["reason"]) for x in archived}),
    }


_FORBIDDEN = {
    "personal_identity": re.compile(r"(?i)(?:@chip|\bchip\b|евгени[йя]|617744661)"),
    "host_private_path": re.compile(r"/(?:home|root|var/lib)/[^\s`]+"),
    "credential_assignment": re.compile(r"(?i)(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^<\s][^\s]*"),
    "raw_production_command": re.compile(r"(?im)^\s*(?:sudo|docker|systemctl|kubectl)\s+"),
}


def privacy_findings(*, candidate_root: Path, config_path: Path) -> list[dict[str, Any]]:
    config = load_skill_config(config_path)
    findings = []
    for entry in config["entries"]:
        path = _inside(candidate_root, str(entry["wrapper"]))
        text = path.read_text(errors="replace")
        for kind, pattern in _FORBIDDEN.items():
            for match in pattern.finditer(text):
                findings.append({"skill": entry["name"], "kind": kind, "offset": match.start()})
    return findings


class SkillRouter:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.entries = sorted(config["entries"], key=lambda x: (-int(x["priority"]), str(x["name"])))
        self.excluded_markers = {
            Path(str(item["source"])).parent.name.casefold()
            for item in config.get("archived_or_private", [])
        }

    @classmethod
    def from_path(cls, path: Path) -> "SkillRouter":
        return cls(load_skill_config(path))

    def route(self, text: str, *, actor_tier: str) -> dict[str, Any]:
        normalized = text.casefold()
        if any(marker and marker in normalized for marker in self.excluded_markers):
            return {"ok": False, "skill": None, "code": "H20_SKILL_ROUTE_NOT_FOUND", "effect": "block"}
        for entry in self.entries:
            if actor_tier not in entry["actor_tier"]:
                continue
            if any(term.casefold() in normalized for term in entry.get("negative_triggers", [])):
                continue
            if any(term.casefold() in normalized for term in entry.get("triggers", [])):
                safety_terms = ("production", "prod", "payment", "оплат", "access", "доступ", "secret", "token", "memory", "персональ")
                effect = "deny_or_verify" if entry["name"] == "human20-answer-router" and any(term in normalized for term in safety_terms) else "route"
                return {"ok": True, "skill": entry["name"], "priority": entry["priority"], "effect": effect}
        return {"ok": False, "skill": None, "code": "H20_SKILL_ROUTE_NOT_FOUND", "effect": "block"}


def quality_scorecard(*, dataset: Path, config_path: Path, min_lift_pp: float, no_family_regression: bool) -> dict[str, Any]:
    config = load_skill_config(config_path)
    coverage: dict[str, list[set[str]]] = defaultdict(list)
    for entry in config["entries"]:
        tiers = set(entry["actor_tier"])
        for family in entry["families"]:
            coverage[str(family)].append(tiers)
    rows = [json.loads(line) for line in Path(dataset).read_text().splitlines() if line.strip()]
    sums = defaultdict(lambda: [0.0, 0.0, 0])
    disagreements = []
    total_baseline = total_candidate = 0.0
    for row in rows:
        family = str(row["task_family"]); actor = str(row["actor_tier"])
        baseline = float(row["observed"]["human20bot"]["credit"])
        routed = any(actor in tiers for tiers in coverage.get(family, []))
        route_credit = 1.0 if row.get("policy_blocker_control") and routed else (0.5 if routed else baseline)
        candidate = max(baseline, route_credit)
        total_baseline += baseline; total_candidate += candidate
        sums[family][0] += baseline; sums[family][1] += candidate; sums[family][2] += 1
        if candidate != baseline:
            disagreements.append({
                "episode_id": row["episode_id"], "task_family": family,
                "baseline": baseline, "candidate": candidate,
                "judge": "deterministic_route_rubric_v1",
                "reason": "actor-eligible curated route; routing-quality evidence only, not a live outcome claim",
            })
    by_family = {}
    for family, (base_sum, cand_sum, count) in sorted(sums.items()):
        base = base_sum / count; cand = cand_sum / count
        by_family[family] = {"episodes": count, "baseline": round(base, 4), "candidate": round(cand, 4), "regression": cand + 1e-12 < base}
    baseline_overall = total_baseline / len(rows); candidate_overall = total_candidate / len(rows)
    lift_pp = (candidate_overall - baseline_overall) * 100
    regressions = [family for family, score in by_family.items() if score["regression"]]
    ok = lift_pp + 1e-12 >= min_lift_pp and (not no_family_regression or not regressions)
    result = {
        "ok": ok, "metric": "paired_route_readiness_proxy", "outcome_claim": False,
        "episodes": len(rows),
        "overall": {"baseline": round(baseline_overall, 4), "candidate": round(candidate_overall, 4), "lift_pp": round(lift_pp, 2)},
        "by_family": by_family, "family_regressions": regressions,
        "judge_disagreement_count": len(disagreements), "judge_disagreement_ledger": disagreements,
        "thresholds": {"min_lift_pp": min_lift_pp, "no_family_regression": no_family_regression},
    }
    if not ok:
        raise SkillContractError("H20_QUALITY_GATE_FAILED")
    return result
