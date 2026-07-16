"""Grounded Human20 research contracts with exact source blockers."""
from __future__ import annotations

import datetime as dt
import html
import ipaddress
import json
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen


class ResearchBlocker(RuntimeError):
    """Stable fail-closed research error."""


def _valid_http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not any((
        address.is_private,
        address.is_loopback,
        address.is_link_local,
        address.is_multicast,
        address.is_reserved,
        address.is_unspecified,
    ))


def exact_url_plan(value: str) -> dict[str, str]:
    if not _valid_http_url(value):
        raise ResearchBlocker("H20_RESEARCH_URL_INVALID")
    return {"mode": "url_first", "url": value}


class _DDGLiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._text: list[str] = []
        self.results: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._href:
            return
        href = html.unescape(self._href)
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc or parsed.path == "/l/":
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            href = unquote(target)
        if _valid_http_url(href) and "duckduckgo.com" not in urlparse(href).netloc:
            title = " ".join("".join(self._text).split()) or href
            self.results.append({"title": title, "url": href, "description": ""})
        self._href = None
        self._text = []


def ddg_lite_search(query: str, limit: int) -> dict[str, Any]:
    """Keyless read-only fallback used when the configured provider lacks credentials."""
    url = "https://lite.duckduckgo.com/lite/?" + urlencode({"q": query})
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=15) as response:
        body = response.read(512_000).decode("utf-8", errors="ignore")
    parser = _DDGLiteParser()
    parser.feed(body)
    return {"success": True, "data": {"web": parser.results[:limit]}}


def wikipedia_search(query: str, limit: int) -> dict[str, Any]:
    """Second keyless source when search HTML is rate-limited."""
    broad_query = query.split()[0]
    url = "https://en.wikipedia.org/w/api.php?" + urlencode({
        "action": "opensearch",
        "search": broad_query,
        "limit": limit,
        "namespace": 0,
        "format": "json",
    })
    request = Request(url, headers={"User-Agent": "Human20Research/1.0 chip@human20.app"})
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    titles = payload[1] if isinstance(payload, list) and len(payload) > 3 else []
    descriptions = payload[2] if isinstance(payload, list) and len(payload) > 3 else []
    urls = payload[3] if isinstance(payload, list) and len(payload) > 3 else []
    rows = [
        {"title": title, "url": source_url, "description": description}
        for title, description, source_url in zip(titles, descriptions, urls)
    ]
    return {"success": True, "data": {"web": rows[:limit]}}


def research_query(
    query: str,
    *,
    search_fn: Callable[[str, int], str | dict[str, Any]] | None = None,
    min_sources: int = 2,
    limit: int = 6,
) -> dict[str, Any]:
    if not query or not query.strip():
        raise ResearchBlocker("H20_RESEARCH_QUERY_EMPTY")
    backend = "injected"
    try:
        if search_fn is None:
            from tools.web_tools import web_search_tool
            raw = web_search_tool(query, max(limit, min_sources))
            payload = json.loads(raw) if isinstance(raw, str) else raw
            backend = "configured_web_provider"
            if not isinstance(payload, dict) or payload.get("success") is not True:
                payload = ddg_lite_search(query, max(limit, min_sources))
                backend = "keyless_read_only_fallback"
                ddg_rows = (payload.get("data") or {}).get("web") or []
                if len(ddg_rows) < min_sources:
                    payload = wikipedia_search(query, max(limit, min_sources))
                    backend = "wikipedia_opensearch_fallback"
        else:
            raw = search_fn(query, max(limit, min_sources))
            payload = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        raise ResearchBlocker("H20_RESEARCH_PROVIDER_FAILED") from exc
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ResearchBlocker("H20_RESEARCH_PROVIDER_FAILED")
    rows = (payload.get("data") or {}).get("web") or []
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        if not _valid_http_url(url) or url in seen:
            continue
        seen.add(url)
        sources.append({
            "title": str(row.get("title") or url),
            "url": url,
            "description": str(row.get("description") or "")[:1000],
        })
    if len(sources) < min_sources:
        raise ResearchBlocker("H20_RESEARCH_INSUFFICIENT_SOURCES")
    return {
        "ok": True,
        "query": query,
        "freshness": "live_read_only",
        "backend": backend,
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_count": len(sources),
        "sources": sources,
        "completed": True,
    }
