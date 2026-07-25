"""Contract tests for the Human20 Keys Codex image provider."""

from __future__ import annotations

import base64
import importlib
from pathlib import Path
from unittest.mock import ANY, MagicMock
from uuid import UUID

import pytest
import requests


provider_module = importlib.import_module(
    "plugins.image_gen.human20-keys-openai-codex"
)


_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nvalid-test-payload").decode()


@pytest.fixture
def provider(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("H20_KEYS_BASE_URL", "http://127.0.0.1:18750/v1/")
    monkeypatch.setenv("H20_KEYS_API_KEY", "customer-secret")
    return provider_module.Human20KeysOpenAICodexImageGenProvider()


def _response(*, status_code=200, payload=None, text=""):
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.text = text
    response.json.return_value = payload
    response.raise_for_status.side_effect = None
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            response=response
        )
    return response


def test_provider_metadata_is_text_only(provider):
    assert provider.name == "human20-keys-openai-codex"
    assert provider.capabilities() == {
        "modalities": ["text"],
        "max_reference_images": 0,
    }
    assert provider.default_model() == "gpt-image-2-medium"
    assert [row["id"] for row in provider.list_models()] == [
        "gpt-image-2-low",
        "gpt-image-2-medium",
        "gpt-image-2-high",
    ]


@pytest.mark.parametrize(
    ("tier", "quality"),
    [
        ("gpt-image-2-low", "low"),
        ("gpt-image-2-medium", "medium"),
        ("gpt-image-2-high", "high"),
    ],
)
@pytest.mark.parametrize(
    ("aspect", "size"),
    [
        ("square", "1024x1024"),
        ("landscape", "1536x1024"),
        ("portrait", "1024x1536"),
    ],
)
def test_generate_posts_exact_gateway_request_and_persists_png(
    provider, monkeypatch, tier, quality, aspect, size
):
    response = _response(payload={"created": 1, "data": [{"b64_json": _PNG}]})
    post = MagicMock(return_value=response)
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("  a lighthouse  ", aspect_ratio=aspect, model=tier)

    assert result["success"] is True
    assert result["provider"] == "human20-keys-openai-codex"
    assert result["model"] == tier
    assert result["quality"] == quality
    assert result["size"] == size
    assert Path(result["image"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    post.assert_called_once_with(
        "http://127.0.0.1:18750/v1/images/generations",
        headers={
            "Authorization": "Bearer customer-secret",
            "Content-Type": "application/json",
            "Idempotency-Key": ANY,
        },
        json={
            "model": "gpt-image-2",
            "prompt": "a lighthouse",
            "size": size,
            "quality": quality,
            "n": 1,
        },
        timeout=330,
    )
    idempotency_key = post.call_args.kwargs["headers"]["Idempotency-Key"]
    assert str(UUID(idempotency_key)) == idempotency_key


@pytest.mark.parametrize(
    "reference_kwargs",
    [
        {"image_url": "https://example.test/source.png"},
        {"reference_image_urls": ["https://example.test/reference.png"]},
        {"reference_images": ["/tmp/reference.png"]},
    ],
)
def test_reference_inputs_are_rejected_locally_before_http(
    provider, monkeypatch, reference_kwargs
):
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("edit this", **reference_kwargs)

    assert result["success"] is False
    assert result["error_type"] == "capability_unsupported"
    post.assert_not_called()


def test_requires_only_human20_keys_environment(monkeypatch):
    monkeypatch.delenv("H20_KEYS_BASE_URL", raising=False)
    monkeypatch.delenv("H20_KEYS_API_KEY", raising=False)
    provider = provider_module.Human20KeysOpenAICodexImageGenProvider()
    assert provider.is_available() is False

    monkeypatch.setenv("H20_KEYS_BASE_URL", "http://127.0.0.1:18750/v1")
    monkeypatch.setenv("H20_KEYS_API_KEY", "customer-secret")
    assert provider.is_available() is True


def test_invalid_config_fails_without_http(provider, monkeypatch):
    monkeypatch.setenv("H20_KEYS_BASE_URL", "https://example.test/not-v1")
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("a lighthouse")

    assert result["success"] is False
    assert result["error_type"] == "invalid_configuration"
    post.assert_not_called()


def test_gateway_error_is_standard_and_does_not_fallback(provider, monkeypatch):
    post = MagicMock(return_value=_response(status_code=502, text="upstream failed"))
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("a lighthouse")

    assert result["success"] is False
    assert result["error_type"] == "api_error"
    assert result["provider"] == "human20-keys-openai-codex"
    assert post.call_count == 1


def test_timeout_is_standard_error(provider, monkeypatch):
    monkeypatch.setattr(
        provider_module.requests,
        "post",
        MagicMock(side_effect=requests.Timeout("slow")),
    )

    result = provider.generate("a lighthouse")

    assert result["success"] is False
    assert result["error_type"] == "timeout"


def test_empty_or_invalid_image_response_fails_safely(provider, monkeypatch):
    monkeypatch.setattr(
        provider_module.requests,
        "post",
        MagicMock(return_value=_response(payload={"data": []})),
    )
    result = provider.generate("a lighthouse")
    assert result["success"] is False
    assert result["error_type"] == "empty_response"


def test_registers_provider():
    ctx = MagicMock()
    provider_module.register(ctx)
    registered = ctx.register_image_gen_provider.call_args.args[0]
    assert registered.name == "human20-keys-openai-codex"


def test_provider_contains_no_direct_vendor_fallback_or_oauth_logic():
    source = Path(provider_module.__file__).read_text(encoding="utf-8").lower()
    forbidden = (
        "openai_api_key",
        "fal_key",
        "oauth",
        "refresh_token",
        "api.openai.com",
        "client.images.generate",
    )
    assert all(term not in source for term in forbidden)
