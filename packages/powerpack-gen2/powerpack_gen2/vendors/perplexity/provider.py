"""Perplexity web search routed exclusively through Human20 Keys."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict

import requests

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

_PROXY_PATH = "/proxy/perplexity/chat/completions"
_MODEL = "pplx-sonar"


def _env_value(name: str) -> str:
    try:
        from hermes_cli.config import get_env_value

        value = get_env_value(name)
    except Exception:
        value = None
    if value is None:
        value = os.getenv(name, "")
    return (value or "").strip()


def _api_key() -> str:
    return _env_value("H20_KEYS_API_KEY")


def _api_url() -> str:
    base_url = _env_value("H20_KEYS_BASE_URL").rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    return f"{base_url}{_PROXY_PATH}" if base_url else ""


def _web_results(payload: dict[str, Any], answer: str, limit: int) -> list[dict[str, Any]]:
    choices = payload.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    raw_results = payload.get("search_results") or message.get("search_results") or []
    raw_citations = payload.get("citations") or message.get("citations") or []
    if not raw_results and not raw_citations:
        raw_citations = re.findall(r"https?://[^\s)\]}>\"']+", answer)

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in list(raw_results) + list(raw_citations):
        if isinstance(item, str):
            item = {"url": item, "title": item}
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("source_url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(
            {
                "url": url,
                "title": str(item.get("title") or item.get("name") or url),
                "description": str(
                    item.get("snippet") or item.get("description") or answer[:500]
                )[:500],
                "position": len(results) + 1,
            }
        )
        if len(results) >= limit:
            break
    return results


class PerplexityWebSearchProvider(WebSearchProvider):
    """Bounded Perplexity Sonar search using a scoped Human20 customer key."""

    @property
    def name(self) -> str:
        return "human20-perplexity"

    @property
    def display_name(self) -> str:
        return "Perplexity via Human20 Keys"

    def is_available(self) -> bool:
        return bool(_api_url() and _api_key())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        api_url = _api_url()
        key = _api_key()
        if not api_url or not key:
            return {
                "success": False,
                "error": "H20_KEYS_BASE_URL and H20_KEYS_API_KEY must be configured.",
            }

        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return {"success": False, "error": "Interrupted"}

            limit = min(max(int(limit or 5), 1), 20)
            logger.info("Human20 Perplexity search: '%s' (limit=%d)", query, limit)
            response = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _MODEL,
                    "messages": [{"role": "user", "content": query}],
                    "max_tokens": 700,
                    "web_search_options": {"search_context_size": "low"},
                },
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
            choices = payload.get("choices") or []
            message = choices[0].get("message", {}) if choices else {}
            answer = str(message.get("content") or "").strip()
            web_results = _web_results(payload, answer, limit)
            if not answer or not web_results:
                return {
                    "success": False,
                    "error": "H20 Perplexity returned an ungrounded response without citations.",
                }
            return {
                "success": True,
                "data": {"web": web_results},
                "answer": answer,
                "citations": [item["url"] for item in web_results],
                "usage": payload.get("usage", {}),
                "request_id": response.headers.get("x-request-id") or payload.get("id"),
                "grounded": True,
            }
        except requests.HTTPError as exc:
            return {"success": False, "error": f"H20 Perplexity HTTP error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Human20 Perplexity search error: %s", exc)
            return {"success": False, "error": f"H20 Perplexity search failed: {exc}"}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Perplexity via Human20 Keys",
            "badge": "managed",
            "tag": "Perplexity Sonar through the metered Human20 Keys proxy.",
            "env_vars": [
                {"key": "H20_KEYS_BASE_URL", "prompt": "Human20 Keys base URL"},
                {"key": "H20_KEYS_API_KEY", "prompt": "Human20 customer key"},
            ],
        }
