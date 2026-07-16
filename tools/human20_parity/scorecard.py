from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dataset import load_dataset


def build_scorecard(path: Path) -> dict[str, Any]:
    episodes = load_dataset(path)
    systems = ("human20bot", "hermes")
    totals = {name: 0.0 for name in systems}
    family_totals: dict[str, dict[str, float]] = defaultdict(lambda: {name: 0.0 for name in systems})
    family_counts: Counter[str] = Counter()
    disagreements = []
    for ep in episodes:
        family = ep["task_family"]
        family_counts[family] += 1
        observed = ep["observed"]
        for name in systems:
            credit = float(observed[name]["credit"])
            if not 0.0 <= credit <= 1.0:
                raise RuntimeError(f"credit outside 0..1: {ep['episode_id']} {name}")
            totals[name] += credit
            family_totals[family][name] += credit
        if observed["human20bot"]["outcome"] != observed["hermes"]["outcome"] or observed["human20bot"]["credit"] != observed["hermes"]["credit"]:
            disagreements.append({
                "episode_id": ep["episode_id"],
                "task_family": family,
                "human20bot": observed["human20bot"]["outcome"],
                "hermes": observed["hermes"]["outcome"],
                "reason": "direct-reply observation differs; manual adjudication required before candidate gate",
            })
    count = len(episodes)
    scores = {name: round(totals[name] / count, 4) if count else 0.0 for name in systems}
    by_family = {
        family: {
            "episodes": family_counts[family],
            **{name: round(family_totals[family][name] / family_counts[family], 4) for name in systems},
        }
        for family in sorted(family_counts)
    }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scoring": "direct-reply deterministic baseline; machine labels are not candidate-gate adjudication",
        "episodes": count,
        "scores": scores,
        "gap_pp": round((scores["hermes"] - scores["human20bot"]) * 100, 2),
        "by_family": by_family,
        "disagreement_count": len(disagreements),
        "disagreement_ledger": disagreements,
    }


def write_scorecard(input_path: Path, out: Path) -> dict[str, Any]:
    report = build_scorecard(input_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"ok": True, "episodes": report["episodes"], "scores": report["scores"], "gap_pp": report["gap_pp"], "disagreements": report["disagreement_count"], "out": str(out)}
