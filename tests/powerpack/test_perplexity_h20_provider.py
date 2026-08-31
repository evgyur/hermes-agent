from __future__ import annotations

import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "packages" / "powerpack-gen2"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from powerpack_gen2.vendors.perplexity import provider  # noqa: E402


class _Response:
    headers = {"x-request-id": "h20req_test"}

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": "Grounded answer"}}],
            "citations": ["https://example.com/source"],
            "usage": {"total_tokens": 42},
        }


def test_provider_uses_only_h20_keys_proxy(monkeypatch):
    values = {
        "H20_KEYS_BASE_URL": "http://127.0.0.1:18750/",
        "H20_KEYS_API_KEY": "customer-key",
    }
    monkeypatch.setattr(provider, "_env_value", lambda name: values.get(name, ""))
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _Response()

    monkeypatch.setattr(provider.requests, "post", fake_post)
    result = provider.PerplexityWebSearchProvider().search("current facts", limit=3)

    assert result["success"] is True
    assert captured["url"] == "http://127.0.0.1:18750/proxy/perplexity/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer customer-key"
    assert captured["json"] == {
        "model": "pplx-sonar",
        "messages": [{"role": "user", "content": "current facts"}],
        "max_tokens": 700,
        "web_search_options": {"search_context_size": "low"},
    }
    assert result["request_id"] == "h20req_test"


def test_direct_perplexity_secret_cannot_enable_provider(monkeypatch):
    values = {"PERPLEXITY_API_KEY": "direct-secret", "PPLX_API_KEY": "direct-secret"}
    monkeypatch.setattr(provider, "_env_value", lambda name: values.get(name, ""))

    instance = provider.PerplexityWebSearchProvider()

    assert instance.is_available() is False
    assert instance.search("query")["success"] is False
    schema_keys = {item["key"] for item in instance.get_setup_schema()["env_vars"]}
    assert schema_keys == {"H20_KEYS_BASE_URL", "H20_KEYS_API_KEY"}
