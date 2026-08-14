"""Perplexity Agent API web search provider.

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


_API_URL = "https://api.perplexity.ai/v1/agent"
_DEFAULT_MODEL = "perplexity/sonar"


def _api_key() -> str:
    """Return the configured Perplexity API key, supporting both env names."""
    for name in ("PERPLEXITY_API_KEY", "PPLX_API_KEY"):
        value = _env_value(name)
        if value:
            return value
    return ""


def _env_value(name: str) -> str:
    try:
        from hermes_cli.config import get_env_value

        value = get_env_value(name)
    except Exception:
        value = None
    if value is None:
        value = os.getenv(name, "")
    return (value or "").strip()


class PerplexityWebSearchProvider(WebSearchProvider):
    """Perplexity Agent API search provider."""

    @property
    def name(self) -> str:
        return "perplexity"

    @property
    def display_name(self) -> str:
        return "Perplexity Agent"

    def is_available(self) -> bool:
        """Return True when a Perplexity API key is configured."""
        return bool(_api_key())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute an Agent API query and normalize typed search results as web hits."""
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
            configured_model = os.getenv("PERPLEXITY_MODEL", _DEFAULT_MODEL).strip()
            request_payload: Dict[str, Any] = {
                "input": query,
                "tools": [
                    {
                        "type": "web_search",
                        "max_results": limit,
                        "search_context_size": "low",
                    }
                ],
                "max_output_tokens": 700,
                "max_tool_calls": 1,
            }
            preset_map = {
                "sonar-pro": "low",
                "sonar-reasoning-pro": "medium",
                "sonar-deep-research": "high",
            }
            if configured_model in preset_map:
                request_payload["preset"] = preset_map[configured_model]
            else:
                if configured_model == "sonar":
                    configured_model = _DEFAULT_MODEL
                request_payload["model"] = configured_model
                request_payload["max_steps"] = 1

            response = requests.post(
                _API_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()

            answer_parts = []
            search_results = []
            for item in payload.get("output") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "search_results":
                    for result in item.get("results") or []:
                        if isinstance(result, dict):
                            search_results.append(result)
                    continue
                if item.get("type") != "message":
                    continue
                for part in item.get("content") or []:
                    if not isinstance(part, dict) or part.get("type") != "output_text":
                        continue
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        answer_parts.append(text.strip())
                    for annotation in part.get("annotations") or []:
                        if isinstance(annotation, dict) and annotation.get("url"):
                            search_results.append(annotation)

            answer = "\n".join(answer_parts).strip()
            web_results = []
            seen_urls = set()
            for citation in search_results:
                url = citation.get("url") or citation.get("source_url") or ""
                title = citation.get("title") or citation.get("name") or url
                if not url:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                description = (
                    citation.get("snippet")
                    or citation.get("description")
                    or answer[:500]
                )
                web_results.append(
                    {
                        "url": url,
                        "title": title or url,
                        "description": str(description)[:500],
                        "position": len(web_results) + 1,
                    }
                )
                if len(web_results) >= limit:
                    break

            return {
                "success": True,
                "data": {"web": web_results},
                "answer": answer,
                "citations": [item["url"] for item in web_results],
                "usage": payload.get("usage", {}),
                "request_id": payload.get("id"),
                "grounded": bool(web_results),
            }
        except requests.HTTPError as exc:
            return {"success": False, "error": f"Perplexity HTTP error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Perplexity search error: %s", exc)
            return {"success": False, "error": f"Perplexity search failed: {exc}"}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Perplexity Agent",
            "badge": "paid",
            "tag": "Perplexity Agent API grounded web search.",
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
