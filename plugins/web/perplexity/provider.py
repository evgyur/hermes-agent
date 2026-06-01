"""Perplexity Sonar web search provider.

Config keys::

    web:
      search_backend: "perplexity"
      backend: "perplexity"

Env vars::

    PERPLEXITY_API_KEY=...  # preferred
    PPLX_API_KEY=...        # compatibility alias

This provider intentionally implements search only. Use a separate extract
backend (Firecrawl/Parallel/etc.) for page extraction.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

import requests

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)


_API_URL = "https://api.perplexity.ai/chat/completions"
_DEFAULT_MODEL = "sonar"


def _api_key() -> str:
    """Return the configured Perplexity API key, supporting both env names."""
    return (os.getenv("PERPLEXITY_API_KEY") or os.getenv("PPLX_API_KEY") or "").strip()


class PerplexityWebSearchProvider(WebSearchProvider):
    """Perplexity Sonar search provider."""

    @property
    def name(self) -> str:
        return "perplexity"

    @property
    def display_name(self) -> str:
        return "Perplexity Sonar"

    def is_available(self) -> bool:
        """Return True when a Perplexity API key is configured."""
        return bool(_api_key())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a Perplexity Sonar query and normalize citations as web hits."""
        key = _api_key()
        if not key:
            return {
                "success": False,
                "error": "PERPLEXITY_API_KEY or PPLX_API_KEY environment variable not set.",
            }

        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return {"success": False, "error": "Interrupted"}

            limit = min(max(int(limit or 5), 1), 20)
            logger.info("Perplexity search: '%s' (limit=%d)", query, limit)
            response = requests.post(
                _API_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": os.getenv("PERPLEXITY_MODEL", _DEFAULT_MODEL),
                    "messages": [{"role": "user", "content": query}],
                    "temperature": 0.2,
                    "search_sources": ["social", "web"],
                },
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()

            answer = (
                (payload.get("choices") or [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            citations = payload.get("citations") or []
            web_results = []
            for i, citation in enumerate(citations[:limit]):
                if isinstance(citation, str):
                    url = citation
                    title = citation
                else:
                    url = citation.get("url") or ""
                    title = citation.get("title") or url
                if not url:
                    continue
                web_results.append(
                    {
                        "url": url,
                        "title": title or url,
                        "description": answer[:500],
                        "position": i + 1,
                    }
                )

            if not web_results and answer:
                web_results.append(
                    {
                        "url": "https://www.perplexity.ai/",
                        "title": "Perplexity Sonar answer",
                        "description": answer[:500],
                        "position": 1,
                    }
                )

            return {
                "success": True,
                "data": {"web": web_results},
                "answer": answer,
                "citations": citations,
                "usage": payload.get("usage", {}),
            }
        except requests.HTTPError as exc:
            return {"success": False, "error": f"Perplexity HTTP error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Perplexity search error: %s", exc)
            return {"success": False, "error": f"Perplexity search failed: {exc}"}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Perplexity Sonar",
            "badge": "paid",
            "tag": "Perplexity Sonar search with social+web sources.",
            "env_vars": [
                {
                    "key": "PERPLEXITY_API_KEY",
                    "prompt": "Perplexity API key",
                    "url": "https://www.perplexity.ai/settings/api",
                },
                {
                    "key": "PPLX_API_KEY",
                    "prompt": "Perplexity API key compatibility alias",
                    "url": "https://www.perplexity.ai/settings/api",
                },
            ],
        }
