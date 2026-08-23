#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from seo_scorecard import compute_scorecard
    from report_generator import write_report, render_markdown
    from seo_live_audit import audit as live_audit
except ImportError:  # pragma: no cover
    from scripts.seo_scorecard import compute_scorecard
    from scripts.report_generator import write_report, render_markdown
    from scripts.seo_live_audit import audit as live_audit


def cmd_audit(args: argparse.Namespace) -> int:
    discovery = args.discovery_path or ["/release.json", "/robots.txt", "/sitemap.xml", "/llms.txt", "/.well-known/mcp.json", "/.well-known/api-catalog", "/.well-known/agent-skills/index.json"]
    result = live_audit(args.base, discovery, args.max_pages)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": args.output, "score": result["score"]["score"], "pages": result["ok_html_count"]}, ensure_ascii=False))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    score = data.get("score") if isinstance(data.get("score"), dict) else compute_scorecard(data)
    print(json.dumps(score, ensure_ascii=False, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    write_report(args.input, args.output, title=args.title)
    print(args.output)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    b = before.get("score", compute_scorecard(before))
    a = after.get("score", compute_scorecard(after))
    if isinstance(b, dict):
        bscore = b["score"]
    else:
        bscore = b
    if isinstance(a, dict):
        ascore = a["score"]
    else:
        ascore = a
    print(json.dumps({"before": bscore, "after": ascore, "delta": ascore - bscore}, ensure_ascii=False, indent=2))
    return 0


def cmd_fixture_report(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    print(render_markdown(data, title=f"Fixture report: {Path(args.fixture).stem}", source=args.fixture))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SEO/AEO/GEO Principal+ CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit")
    p.add_argument("--base", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--max-pages", type=int, default=None)
    p.add_argument("--discovery-path", action="append", default=None)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("score")
    p.add_argument("--input", required=True)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("report")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--title", default="SEO/AEO/GEO audit")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("compare")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("fixture-report")
    p.add_argument("--fixture", required=True)
    p.set_defaults(func=cmd_fixture_report)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
