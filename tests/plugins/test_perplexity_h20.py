from plugins.web.perplexity.provider import PerplexityWebSearchProvider


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "id": "h20-test",
            "choices": [{"message": {"content": "Grounded answer"}}],
            "citations": ["https://example.com/source"],
            "usage": {"total_tokens": 12},
        }


def test_h20_chat_completions_perplexity_returns_grounded_hits(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "tenant-key")
    monkeypatch.setenv("PERPLEXITY_API_URL", "http://127.0.0.1:18750/v1/chat/completions")
    monkeypatch.setenv("PERPLEXITY_MODEL", "pplx-sonar")
    seen = {}

    def _post(url, **kwargs):
        seen["url"] = url
        seen["payload"] = kwargs["json"]
        return _Response()

    monkeypatch.setattr("plugins.web.perplexity.provider.requests.post", _post)
    result = PerplexityWebSearchProvider().search("query", limit=5)
    assert result["success"] is True
    assert result["grounded"] is True
    assert result["data"]["web"][0]["url"] == "https://example.com/source"
    assert seen["payload"]["model"] == "pplx-sonar"


def test_h20_chat_completions_perplexity_fails_closed_without_citations(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "tenant-key")
    monkeypatch.setenv("PERPLEXITY_API_URL", "http://127.0.0.1:18750/v1/chat/completions")
    monkeypatch.setattr("plugins.web.perplexity.provider.requests.post", lambda *a, **k: _Response())
    monkeypatch.setattr(_Response, "json", lambda self: {"choices": [{"message": {"content": "No sources"}}]})
    result = PerplexityWebSearchProvider().search("query", limit=5)
    assert result["success"] is False
    assert "ungrounded" in result["error"]


def test_h20_chat_completions_perplexity_extracts_inline_citation_urls(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "tenant-key")
    monkeypatch.setenv("PERPLEXITY_API_URL", "http://127.0.0.1:18750/v1/chat/completions")
    monkeypatch.setattr(
        "plugins.web.perplexity.provider.requests.post",
        lambda *a, **k: _Response(),
    )
    monkeypatch.setattr(
        _Response,
        "json",
        lambda self: {
            "choices": [{"message": {"content": "Grounded: https://example.com/source"}}]
        },
    )
    result = PerplexityWebSearchProvider().search("query", limit=5)
    assert result["success"] is True
    assert result["citations"] == ["https://example.com/source"]