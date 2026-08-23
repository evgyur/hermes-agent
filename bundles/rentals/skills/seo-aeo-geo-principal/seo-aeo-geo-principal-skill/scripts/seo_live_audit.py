#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from seo_scorecard import compute_scorecard


@dataclass
class FetchResult:
    url: str
    final_url: str | None = None
    status: int | None = None
    content_type: str = ""
    bytes_len: int = 0
    text: str = ""
    error: str | None = None
    headers: dict[str, str] | None = None


def fetch(session: requests.Session, url: str, timeout: int = 25) -> FetchResult:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        ctype = r.headers.get("content-type", "")
        text = r.text if any(x in ctype for x in ("text", "html", "xml", "json", "javascript")) else ""
        return FetchResult(
            url=url,
            final_url=r.url,
            status=r.status_code,
            content_type=ctype,
            bytes_len=len(r.content),
            text=text,
            headers=dict(r.headers),
        )
    except Exception as exc:  # pragma: no cover - evidence script
        return FetchResult(url=url, error=repr(exc), headers={})


def jsonld_types(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        typ = value.get("@type")
        if isinstance(typ, str):
            out.append(typ)
        elif isinstance(typ, list):
            out.extend(str(x) for x in typ)
        for v in value.values():
            out.extend(jsonld_types(v))
    elif isinstance(value, list):
        for item in value:
            out.extend(jsonld_types(item))
    return out


def norm_attr(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value or "")


def is_meaningful_img_missing_alt(img: Any) -> bool:
    aria_hidden = norm_attr(img.get("aria-hidden")).strip().lower()
    role = norm_attr(img.get("role")).strip().lower()
    alt = norm_attr(img.get("alt")).strip()
    if aria_hidden == "true" or role in {"presentation", "none"}:
        return False
    return not alt


def analyze_html(item: FetchResult, public_netloc: str) -> dict[str, Any]:
    if item.status != 200 or "text/html" not in item.content_type:
        return {
            "url": item.url,
            "final_url": item.final_url,
            "status": item.status,
            "content_type": item.content_type,
            "bytes": item.bytes_len,
            "error": item.error,
        }

    soup = BeautifulSoup(item.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    # Re-parse scripts from original HTML for JSON-LD because we removed scripts above for text extraction.
    soup_scripts = BeautifulSoup(item.text, "html.parser")
    jsonld: list[dict[str, Any]] = []
    for script in soup_scripts.find_all("script", type="application/ld+json"):
        raw = script.get_text(strip=True)
        try:
            parsed = json.loads(raw)
            jsonld.append({"types": jsonld_types(parsed), "chars": len(raw), "ok": True})
        except Exception as exc:
            jsonld.append({"ok": False, "error": str(exc), "chars": len(raw), "raw_prefix": raw[:200]})

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    desc_tag = soup.find("meta", attrs={"name": "description"})
    canonical_tag = soup.find("link", rel="canonical")
    robots_meta = soup.find("meta", attrs={"name": re.compile("robots", re.I)})
    h1 = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    h2 = [h.get_text(" ", strip=True) for h in soup.find_all("h2")]
    imgs = soup.find_all("img")

    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(item.final_url or item.url, norm_attr(a.get("href"))).split("#")[0]
        if urlparse(href).netloc == public_netloc:
            links.append(href)

    og = {}
    for meta in BeautifulSoup(item.text, "html.parser").find_all("meta"):
        key = norm_attr(meta.get("property") or meta.get("name"))
        if key.startswith("og:") or key.startswith("twitter:"):
            og[key] = norm_attr(meta.get("content"))

    text = unescape(soup.get_text(" ", strip=True))
    meaningful_missing = []
    for img in imgs:
        if is_meaningful_img_missing_alt(img):
            meaningful_missing.append({
                "src": norm_attr(img.get("src"))[:220],
                "class": norm_attr(img.get("class"))[:160],
            })

    return {
        "url": item.url,
        "final_url": item.final_url,
        "status": item.status,
        "content_type": item.content_type,
        "bytes": item.bytes_len,
        "title": title,
        "title_len": len(title),
        "description": norm_attr(desc_tag.get("content")) if desc_tag else "",
        "description_len": len(norm_attr(desc_tag.get("content"))) if desc_tag else 0,
        "canonical": norm_attr(canonical_tag.get("href")) if canonical_tag else "",
        "robots_meta": norm_attr(robots_meta.get("content")) if robots_meta else "",
        "h1": h1,
        "h1_count": len(h1),
        "h2_sample": h2[:10],
        "jsonld": jsonld,
        "jsonld_types": sorted({t for block in jsonld for t in block.get("types", [])}),
        "jsonld_count": len(jsonld),
        "jsonld_parse_errors": [b for b in jsonld if not b.get("ok")],
        "og": og,
        "og_count": len(og),
        "img_count": len(imgs),
        "img_without_alt_raw": sum(1 for img in imgs if not norm_attr(img.get("alt")).strip()),
        "meaningful_img_without_alt": len(meaningful_missing),
        "meaningful_img_without_alt_items": meaningful_missing,
        "text_chars": len(text),
        "internal_links_unique": len(set(links)),
        "question_mark_count": text.count("?"),
    }


def sitemap_urls_from_text(text: str) -> list[str]:
    return re.findall(r"<loc>\s*(.*?)\s*</loc>", text, flags=re.I | re.S)


def audit(base: str, discovery_paths: list[str], max_pages: int | None = None) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update({"User-Agent": "SEOAEOAudit/1.0"})
    base = base.rstrip("/")
    parsed = urlparse(base)
    public_netloc = parsed.netloc

    discovery = {base + p: asdict(fetch(session, base + p)) for p in discovery_paths}
    sitemap_text = discovery.get(base + "/sitemap.xml", {}).get("text") or ""
    sitemap_urls = sitemap_urls_from_text(sitemap_text)
    if max_pages:
        sitemap_urls = sitemap_urls[:max_pages]

    pages = [analyze_html(fetch(session, url), public_netloc) for url in sitemap_urls]
    ok_html = [p for p in pages if p.get("status") == 200 and "text/html" in p.get("content_type", "")]

    missing_jsonld = [p["url"] for p in ok_html if p.get("jsonld_count", 0) == 0]
    thin_pages = [{"url": p["url"], "text_chars": p.get("text_chars", 0)} for p in ok_html if p.get("text_chars", 0) < 1500]
    raw_alt_debt = [{"url": p["url"], "img_count": p.get("img_count", 0), "img_without_alt_raw": p.get("img_without_alt_raw", 0)} for p in ok_html if p.get("img_without_alt_raw", 0) > 0]
    meaningful_alt_debt = [{"url": p["url"], "items": p.get("meaningful_img_without_alt_items", [])} for p in ok_html if p.get("meaningful_img_without_alt", 0) > 0]

    result: dict[str, Any] = {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base": base,
        "sitemap_url_count": len(sitemap_urls),
        "ok_html_count": len(ok_html),
        "non_200": [p for p in pages if p.get("status") != 200],
        "missing_title": [p["url"] for p in ok_html if not p.get("title")],
        "missing_description": [p["url"] for p in ok_html if not p.get("description")],
        "missing_canonical": [p["url"] for p in ok_html if not p.get("canonical")],
        "bad_h1": [{"url": p["url"], "h1_count": p.get("h1_count"), "h1": p.get("h1")} for p in ok_html if p.get("h1_count") != 1],
        "noindex": [p["url"] for p in ok_html if "noindex" in str(p.get("robots_meta", "")).lower()],
        "missing_jsonld": missing_jsonld,
        "thin_pages_under_1500": thin_pages,
        "img_alt_debt_raw": raw_alt_debt,
        "meaningful_image_alt_debt": meaningful_alt_debt,
        "text_chars_median": statistics.median([p.get("text_chars", 0) for p in ok_html]) if ok_html else 0,
        "html_bytes_median": statistics.median([p.get("bytes", 0) for p in ok_html]) if ok_html else 0,
        "pages": pages,
        "discovery": discovery,
    }
    result["score"] = compute_scorecard(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Live SEO/AEO/GEO audit crawler")
    parser.add_argument("--base", required=True, help="Base URL, e.g. https://example.com")
    parser.add_argument("--output", required=True, help="Path to write JSON audit")
    parser.add_argument("--max-pages", type=int, default=None, help="Limit sitemap crawl for smoke tests")
    parser.add_argument("--summary", action="store_true", help="Print compact summary")
    parser.add_argument("--discovery-path", action="append", default=None, help="Extra or replacement discovery path; repeatable. Defaults cover common SEO/GEO endpoints")
    args = parser.parse_args()

    discovery_paths = args.discovery_path or [
        "/release.json",
        "/robots.txt",
        "/sitemap.xml",
        "/llms.txt",
        "/.well-known/mcp.json",
        "/.well-known/api-catalog",
        "/.well-known/agent-skills/index.json",
    ]
    result = audit(args.base, discovery_paths, args.max_pages)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if args.summary:
        summary = {
            "fetched_at": result["fetched_at"],
            "base": result["base"],
            "score": result["score"]["score"],
            "sitemap_url_count": result["sitemap_url_count"],
            "ok_html_count": result["ok_html_count"],
            "non_200": len(result["non_200"]),
            "missing_jsonld": len(result["missing_jsonld"]),
            "thin_pages_under_1500": len(result["thin_pages_under_1500"]),
            "meaningful_image_alt_debt_pages": len(result["meaningful_image_alt_debt"]),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
