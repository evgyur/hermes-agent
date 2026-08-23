#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class AdapterResult:
    source: str
    available: bool
    score: float | int | None
    evidence_tier: str
    confidence: str
    residual_gap: str
    raw: dict[str, Any]


class MockMeasurementAdapter:
    """Offline adapter used by tests and docs.

    Real GSC/Yandex/PageSpeed/CrUX connectors must pass through explicit credential
    approval and should produce this same shape. Tests must never require secrets.
    """

    def __init__(self, source: str, score: float | int | None = None, available: bool = True):
        self.source = source
        self.score = score
        self.available = available

    def fetch(self, base_url: str) -> AdapterResult:
        return AdapterResult(
            source=self.source,
            available=self.available,
            score=self.score,
            evidence_tier="external_current_source" if self.available else "assumption",
            confidence="high" if self.available else "low",
            residual_gap="none" if self.available else f"{self.source} unavailable or not approved",
            raw={"base_url": base_url, "mode": "mock_offline"},
        )


def merge_measurement(audit: dict[str, Any], *results: AdapterResult) -> dict[str, Any]:
    merged = dict(audit)
    measurement = dict(merged.get("measurement") or {})
    for result in results:
        measurement[result.source] = asdict(result)
        if result.source in {"lighthouse", "pagespeed", "crux"}:
            measurement.setdefault("performance", asdict(result))
    merged["measurement"] = measurement
    return merged


def load_measurement_file(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe offline measurement adapter smoke")
    parser.add_argument("--base", required=True)
    parser.add_argument("--source", default="lighthouse", choices=["lighthouse", "pagespeed", "crux", "gsc", "yandex"])
    parser.add_argument("--score", type=float, default=0.82)
    parser.add_argument("--unavailable", action="store_true")
    args = parser.parse_args()
    result = MockMeasurementAdapter(args.source, args.score, not args.unavailable).fetch(args.base)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
