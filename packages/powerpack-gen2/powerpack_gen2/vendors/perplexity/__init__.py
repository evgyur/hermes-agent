"""Perplexity Sonar web search provider."""
from __future__ import annotations

from .provider import PerplexityWebSearchProvider


def register(ctx, *, required: bool = False) -> None:
    """Register the Perplexity provider with the plugin context."""
    provider = PerplexityWebSearchProvider()
    handle = ctx.register_web_search_provider(provider)
    from agent.web_search_registry import get_provider

    if required and handle is None and get_provider(provider.name) is not provider:
        raise RuntimeError("Powerpack Gen2 provider registration collided: human20-perplexity")
