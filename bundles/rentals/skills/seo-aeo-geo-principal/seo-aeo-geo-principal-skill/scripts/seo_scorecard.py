from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Any


EVIDENCE_DIRECT = "direct_artifact"
EVIDENCE_CONTEXT = "provided_context"
EVIDENCE_EXTERNAL = "external_current_source"
EVIDENCE_ASSUMPTION = "assumption"

IMPACT_WEIGHT = {"low": 1, "medium": 2, "high": 3, "critical": 4}
EFFORT_WEIGHT = {"low": 1, "medium": 2, "high": 3}
CONFIDENCE_WEIGHT = {"low": 0.5, "medium": 0.75, "high": 1.0}


def _count(value: Any) -> int:
    return len(value or [])


def _ratio_score(missing: int, total: int, *, full: int = 10) -> int:
    if total <= 0:
        return 0
    ratio = max(0.0, min(1.0, 1.0 - missing / total))
    return round(ratio * full)


def jsonld_type_counter(audit: dict[str, Any]) -> Counter[str]:
    c: Counter[str] = Counter()
    for page in audit.get("pages", []):
        for typ in page.get("jsonld_types", []) or []:
            c[str(typ)] += 1
    return c


def _measurement(audit: dict[str, Any], key: str) -> dict[str, Any] | None:
    measurement = audit.get("measurement") or {}
    value = measurement.get(key)
    return value if isinstance(value, dict) else None


def _performance_score(audit: dict[str, Any]) -> tuple[int, str, str, str]:
    """Return score, evidence, evidence_tier, confidence.

    Performance stays conservative without a measurement artifact. This prevents the common fake-green
    failure mode where CWV/Lighthouse/PageSpeed is assumed healthy because HTML checks passed.
    """
    perf = _measurement(audit, "performance") or _measurement(audit, "lighthouse") or _measurement(audit, "pagespeed")
    if perf and perf.get("available"):
        raw = perf.get("score")
        try:
            score = int(round(float(raw) * 10 if 0 <= float(raw) <= 1 else float(raw)))
        except Exception:
            score = 6
        source = perf.get("source", "measurement")
        return max(0, min(10, score)), f"{source}={raw}", EVIDENCE_EXTERNAL, "high"
    return int(audit.get("performance_confidence_score") or 6), "CWV/Lighthouse/CrUX unmeasured; conservative confidence", EVIDENCE_ASSUMPTION, "low"


def _row(
    criterion: str,
    score: int,
    evidence: str,
    *,
    evidence_tier: str = EVIDENCE_DIRECT,
    confidence: str = "high",
    impact: str = "medium",
    effort: str = "medium",
    residual_gap: str = "none",
) -> dict[str, Any]:
    gap = max(0, 10 - int(score))
    priority = round(gap * IMPACT_WEIGHT[impact] * CONFIDENCE_WEIGHT[confidence] / EFFORT_WEIGHT[effort], 2)
    return {
        "criterion": criterion,
        "score": int(score),
        "evidence": evidence,
        "evidence_tier": evidence_tier,
        "confidence": confidence,
        "impact": impact,
        "effort": effort,
        "priority": priority,
        "residual_gap": residual_gap,
    }


def rank_priorities(scorecard: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return highest-priority scorecard rows first."""
    return sorted(scorecard, key=lambda row: (float(row.get("priority") or 0), 10 - int(row.get("score") or 0)), reverse=True)


def compute_scorecard(audit: dict[str, Any]) -> dict[str, Any]:
    """Compute a conservative 12-dimension SEO/AEO/GEO scorecard from audit JSON.

    V2 adds Principal+ metadata to each row: evidence tier, confidence, impact, effort,
    priority, and residual gap. The top-level numeric score remains compatible with v1.
    """
    total = int(audit.get("ok_html_count") or 0)
    sitemap_total = int(audit.get("sitemap_url_count") or total or 0)
    missing_jsonld = _count(audit.get("missing_jsonld"))
    thin = _count(audit.get("thin_pages_under_1500"))
    non_200 = _count(audit.get("non_200"))
    missing_title = _count(audit.get("missing_title"))
    missing_description = _count(audit.get("missing_description"))
    missing_canonical = _count(audit.get("missing_canonical"))
    bad_h1 = _count(audit.get("bad_h1"))
    noindex = _count(audit.get("noindex"))
    meaningful_alt_debt = _count(audit.get("meaningful_image_alt_debt") or audit.get("meaningful_image_alt_debt_pages"))

    discovery = audit.get("discovery", {}) or {}
    discovery_status = {k: (v or {}).get("status") for k, v in discovery.items()}
    discovery_good = sum(1 for s in discovery_status.values() if s == 200)
    discovery_total = len(discovery_status)

    type_counts = jsonld_type_counter(audit)
    has_org = type_counts.get("Organization", 0) > 0
    has_person = type_counts.get("Person", 0) > 0
    has_website = type_counts.get("WebSite", 0) > 0
    has_faq = type_counts.get("FAQPage", 0) > 0
    has_about = type_counts.get("AboutPage", 0) > 0
    has_learning = any(type_counts.get(t, 0) > 0 for t in ("LearningResource", "Course", "VideoObject", "CreativeWork"))

    internal_links = [int(p.get("internal_links_unique") or 0) for p in audit.get("pages", []) if p.get("status") == 200]
    internal_median = median(internal_links) if internal_links else 0
    text_median = int(audit.get("text_chars_median") or 0)

    rows: list[dict[str, Any]] = []
    rows.append(_row("Crawlability", 10 if total and non_200 == 0 and total >= sitemap_total else max(0, 10 - non_200 * 2), f"ok_html={total}, sitemap={sitemap_total}, non_200={non_200}", impact="critical", effort="medium", residual_gap="non-200 URLs" if non_200 else "none"))
    rows.append(_row("Indexability", 10 if noindex == 0 and missing_canonical == 0 else max(0, 10 - noindex * 3 - missing_canonical), f"noindex={noindex}, missing_canonical={missing_canonical}", impact="critical", effort="low", residual_gap="indexing blockers" if (noindex or missing_canonical) else "none"))
    meta_missing = missing_title + missing_description + bad_h1
    rows.append(_row("Metadata", 10 if meta_missing == 0 else max(0, 10 - meta_missing), f"missing_title={missing_title}, missing_description={missing_description}, bad_h1={bad_h1}", impact="high", effort="low", residual_gap="metadata gaps" if meta_missing else "none"))
    rows.append(_row("Sitemap quality", 9 if sitemap_total >= total and sitemap_total > 0 and non_200 == 0 else 6, f"sitemap_url_count={sitemap_total}", impact="medium", effort="low", residual_gap="sitemap coverage uncertain" if not sitemap_total else "none"))
    rows.append(_row("Structured data coverage", 10 if missing_jsonld == 0 else _ratio_score(missing_jsonld, total), f"missing_jsonld={missing_jsonld}/{total}", impact="high", effort="medium", residual_gap="JSON-LD missing" if missing_jsonld else "none"))
    if thin == 0 and text_median >= 2500:
        content_score = 10
    elif thin == 0:
        content_score = 9
    else:
        content_score = max(4, _ratio_score(thin, total))
    rows.append(_row("Content depth", content_score, f"thin_pages_under_1500={thin}, median_text={text_median}", impact="high", effort="high", residual_gap="thin pages" if thin else "none"))
    rows.append(_row("Internal linking", 9 if internal_median >= 15 else 7 if internal_median >= 8 else 5, f"median_internal_links={internal_median}", impact="medium", effort="medium", residual_gap="low internal link graph" if internal_median < 8 else "none"))
    aeo_score = 8 + int(has_faq) + int(has_learning)
    rows.append(_row("AEO answer blocks", min(10, aeo_score), f"FAQPage={type_counts.get('FAQPage', 0)}, learning/video/creative={has_learning}", impact="high", effort="medium", residual_gap="answer/schema blocks thin" if aeo_score < 10 else "none"))
    rows.append(_row("GEO / agent discovery", 10 if discovery_total and discovery_good >= min(4, discovery_total) else max(5, _ratio_score(discovery_total-discovery_good, discovery_total or 1)), f"discovery_200={discovery_good}/{discovery_total}", impact="high", effort="medium", residual_gap="agent-readable discovery missing" if discovery_good < min(4, discovery_total or 4) else "none"))
    entity_score = 6 + int(has_org) + int(has_person) + int(has_website) + int(has_about)
    rows.append(_row("Entity authority", min(10, entity_score), f"Organization={has_org}, Person={has_person}, WebSite={has_website}, AboutPage={has_about}", impact="medium", effort="medium", residual_gap="entity graph incomplete" if entity_score < 10 else "none"))
    rows.append(_row("Image SEO/accessibility", 9 if meaningful_alt_debt == 0 else max(4, 10 - meaningful_alt_debt), f"meaningful_image_alt_debt_pages={meaningful_alt_debt}", impact="medium", effort="low", residual_gap="meaningful image alt debt" if meaningful_alt_debt else "none"))
    perf_score, perf_evidence, perf_tier, perf_confidence = _performance_score(audit)
    rows.append(_row("Performance confidence", perf_score, perf_evidence, evidence_tier=perf_tier, confidence=perf_confidence, impact="high", effort="high", residual_gap="CWV/PageSpeed/CrUX missing" if perf_tier == EVIDENCE_ASSUMPTION else "none"))

    normalized = round(sum(row["score"] for row in rows) / len(rows) * 10)
    return {
        "score": normalized,
        "scorecard": rows,
        "priorities": rank_priorities(rows),
        "jsonld_type_counts": dict(type_counts),
        "model_version": "2.0-principal-plus",
        "evidence_tiers": [EVIDENCE_DIRECT, EVIDENCE_CONTEXT, EVIDENCE_EXTERNAL, EVIDENCE_ASSUMPTION],
    }
