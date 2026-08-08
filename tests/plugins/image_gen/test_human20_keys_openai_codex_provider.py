"""Contract tests for the Human20 Keys Codex image provider."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import importlib
import json
import os
from pathlib import Path
import stat
import threading
import time
from unittest.mock import ANY, MagicMock
from uuid import UUID

import pytest
import requests
from PIL import Image, PngImagePlugin
from urllib3.exceptions import ReadTimeoutError


provider_module = importlib.import_module("plugins.image_gen.human20-keys-openai-codex")


def _png_b64(size=(1024, 1024)) -> str:
    stream = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


def _image_bytes(image_format: str, size=(4, 3)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, (40, 50, 60)).save(stream, format=image_format)
    return stream.getvalue()


def _data_url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


_PNG = _png_b64()


@contextmanager
def _serve(handler_type):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type)
    server.daemon_threads = True
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def provider(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("H20_KEYS_BASE_URL", "http://127.0.0.1:18750/v1/")
    monkeypatch.setenv("H20_KEYS_API_KEY", "customer-secret")
    return provider_module.Human20KeysOpenAICodexImageGenProvider()


def _response(*, status_code=200, payload=None, text=""):
    class Raw:
        def __init__(self, content):
            self.stream = io.BytesIO(content)

        def read(self, size, decode_content=True):
            return self.stream.read(size)

    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.text = text
    response.json.return_value = payload
    response.raw = Raw(json.dumps(payload).encode() if payload is not None else b"")
    response.raise_for_status.side_effect = None
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    return response


def test_provider_metadata_advertises_primary_image_edits(provider):
    assert provider.name == "human20-keys-openai-codex"
    assert provider.capabilities() == {
        "modalities": ["text", "image"],
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
    dimensions = tuple(int(value) for value in size.split("x", 1))
    response = _response(
        payload={"created": 1, "data": [{"b64_json": _png_b64(dimensions)}]}
    )
    post = MagicMock(return_value=response)
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("  a lighthouse  ", aspect_ratio=aspect, model=tier)

    assert result["success"] is True
    assert result["provider"] == "human20-keys-openai-codex"
    assert result["model"] == tier
    assert result["quality"] == quality
    assert result["size"] == size
    assert Path(result["image"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    post.assert_called_once()
    assert post.call_args.args == ("http://127.0.0.1:18750/v1/images/generations",)
    assert post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer customer-secret",
        "Content-Type": "application/json",
        "Idempotency-Key": ANY,
    }
    assert post.call_args.kwargs["json"] == {
        "model": "gpt-image-2",
        "prompt": "a lighthouse",
        "size": size,
        "quality": quality,
        "n": 1,
    }
    assert post.call_args.kwargs["stream"] is True
    assert post.call_args.kwargs["allow_redirects"] is False
    timeout = post.call_args.kwargs["timeout"]
    assert 0 < timeout.total <= 330
    assert 0 < timeout.connect_timeout <= 30
    assert response.close.called
    idempotency_key = post.call_args.kwargs["headers"]["Idempotency-Key"]
    assert str(UUID(idempotency_key)) == idempotency_key


def test_edit_posts_exact_gateway_request_with_canonical_local_jpeg(
    provider, monkeypatch, tmp_path
):
    source_bytes = _image_bytes("JPEG")
    source = tmp_path / "source-with-wrong-extension.png"
    source.write_bytes(source_bytes)
    response = _response(payload={"data": [{"b64_json": _PNG}]})
    post = MagicMock(return_value=response)
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate(
        "  preserve the subject  ",
        aspect_ratio="square",
        model="gpt-image-2-high",
        image_url=str(source),
    )

    assert result["success"] is True
    assert result["modality"] == "image"
    assert post.call_args.args == ("http://127.0.0.1:18750/v1/images/edits",)
    assert post.call_args.kwargs["json"] == {
        "model": "gpt-image-2",
        "prompt": "preserve the subject",
        "image": provider_module._canonicalize_primary_image(str(source)),
        "size": "1024x1024",
        "quality": "high",
        "n": 1,
    }
    assert post.call_args.kwargs["stream"] is True
    assert post.call_args.kwargs["allow_redirects"] is False
    assert response.close.called
    assert str(UUID(post.call_args.kwargs["headers"]["Idempotency-Key"]))


def test_edit_accepts_and_canonicalizes_base64_data_url(provider, monkeypatch):
    source_bytes = _image_bytes("PNG")
    source = _data_url("image/png", source_bytes)
    response = _response(payload={"data": [{"b64_json": _PNG}]})
    post = MagicMock(return_value=response)
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("edit this", aspect_ratio="square", image_url=source)

    assert result["success"] is True
    assert result["modality"] == "image"
    assert post.call_args.args[0].endswith("/images/edits")
    assert post.call_args.kwargs["json"]["image"] == source


def test_explicit_instagram_output_size_is_applied_after_gpt_edit(
    provider, monkeypatch, tmp_path
):
    source = tmp_path / "source.jpg"
    source.write_bytes(_image_bytes("JPEG"))
    response = _response(payload={"data": [{"b64_json": _png_b64((1024, 1536))}]})
    monkeypatch.setattr(
        provider_module.requests, "post", MagicMock(return_value=response)
    )

    result = provider.generate(
        "Retouch only. Instagram format 4:5 — 1080 × 1350 px.",
        aspect_ratio="portrait",
        image_url=str(source),
    )

    assert result["success"] is True
    assert result["size"] == "1080x1350"
    with Image.open(result["image"]) as output:
        assert output.format == "PNG"
        assert output.size == (1080, 1350)


def test_unlabelled_dimensions_in_scene_do_not_trigger_postprocessing(
    provider, monkeypatch
):
    response = _response(payload={"data": [{"b64_json": _PNG}]})
    monkeypatch.setattr(
        provider_module.requests, "post", MagicMock(return_value=response)
    )

    result = provider.generate("A 1080x1350 sign in a room", aspect_ratio="square")

    assert result["success"] is True
    assert result["size"] == "1024x1024"
    with Image.open(result["image"]) as output:
        assert output.size == (1024, 1024)


@pytest.mark.parametrize(
    "source", ["http://example.test/a.png", "https://example.test/a.png"]
)
def test_remote_primary_image_urls_are_rejected_before_http(
    provider, monkeypatch, source
):
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("edit this", image_url=source)

    assert result["success"] is False
    assert result["error_type"] == "capability_unsupported"
    assert "local file path or base64 data:image URL" in result["error"]
    post.assert_not_called()


def test_gif_primary_is_rejected_and_png_trailer_is_removed(provider, monkeypatch):
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)
    result = provider.generate(
        "edit this", image_url=_data_url("image/gif", _image_bytes("GIF"))
    )
    assert result["success"] is False
    assert result["error_type"] == "invalid_argument"
    post.assert_not_called()

    raw = _image_bytes("PNG")
    canonical = provider_module._canonicalize_primary_image(
        _data_url("image/png", raw + b"private-trailer")
    )
    decoded = base64.b64decode(canonical.split(",", 1)[1], validate=True)
    assert canonical.startswith("data:image/png;base64,")
    assert b"private-trailer" not in decoded


def test_primary_image_preserves_palette_alpha_and_strips_metadata():
    source = Image.new("P", (2, 1))
    source.putpalette([0, 0, 0, 255, 0, 0] + [0, 0, 0] * 254)
    source.putdata([0, 1])
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-note", "must-not-survive")
    buffer = io.BytesIO()
    source.save(
        buffer,
        format="PNG",
        transparency=bytes([0, 128]),
        pnginfo=metadata,
        icc_profile=b"private-icc-sentinel",
    )

    canonical = provider_module._canonicalize_primary_image(
        _data_url("image/png", buffer.getvalue())
    )
    raw = base64.b64decode(canonical.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as canonical_image:
        canonical_image.load()
        assert canonical_image.mode == "RGBA"
        alpha = canonical_image.getchannel("A")
        assert [alpha.getpixel((x, 0)) for x in range(2)] == [0, 128]
        assert "icc_profile" not in canonical_image.info
        assert "private-note" not in canonical_image.info


def test_local_primary_read_is_fail_closed_and_rejects_symlink_components(
    provider, monkeypatch, tmp_path
):
    import agent.file_safety as file_safety

    source = tmp_path / "source.png"
    source.write_bytes(_image_bytes("PNG"))
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)
    monkeypatch.setattr(
        file_safety,
        "get_read_block_error",
        MagicMock(side_effect=RuntimeError("classifier failure")),
    )
    failed = provider.generate("edit", image_url=str(source))
    assert failed["success"] is False
    assert failed["error_type"] == "invalid_argument"
    post.assert_not_called()

    monkeypatch.undo()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("H20_KEYS_BASE_URL", "http://127.0.0.1:18750/v1/")
    monkeypatch.setenv("H20_KEYS_API_KEY", "customer-secret")
    provider = provider_module.Human20KeysOpenAICodexImageGenProvider()
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)
    alias = tmp_path / "alias.png"
    alias.symlink_to(source)
    denied = provider.generate("edit", image_url=str(alias))
    assert denied["success"] is False
    assert denied["error_type"] == "invalid_argument"
    post.assert_not_called()

    real_directory = tmp_path / "real"
    real_directory.mkdir()
    nested = real_directory / "source.png"
    nested.write_bytes(_image_bytes("PNG"))
    parent_alias = tmp_path / "linked-directory"
    parent_alias.symlink_to(real_directory, target_is_directory=True)
    denied_parent = provider.generate(
        "edit", image_url=str(parent_alias / "source.png")
    )
    assert denied_parent["success"] is False
    assert denied_parent["error_type"] == "invalid_argument"
    post.assert_not_called()


@pytest.mark.parametrize(
    "reference_kwargs",
    [
        {"reference_image_urls": ["https://example.test/reference.png"]},
        {"reference_image_urls": "https://example.test/reference.png"},
        {"reference_images": ["/tmp/reference.png"]},
        {"reference_images": [""]},
    ],
)
def test_nonempty_reference_inputs_are_rejected_before_http(
    provider, monkeypatch, tmp_path, reference_kwargs
):
    source = tmp_path / "source.png"
    source.write_bytes(_image_bytes("PNG"))
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("edit this", image_url=str(source), **reference_kwargs)

    assert result["success"] is False
    assert result["error_type"] == "capability_unsupported"
    assert "exactly one primary image" in result["error"]
    post.assert_not_called()


def test_empty_reference_fields_do_not_change_text_generation(provider, monkeypatch):
    response = _response(payload={"data": [{"b64_json": _PNG}]})
    post = MagicMock(return_value=response)
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate(
        "a lighthouse",
        aspect_ratio="square",
        reference_image_urls=[],
        reference_images=[],
    )

    assert result["success"] is True
    assert result["modality"] == "text"
    assert post.call_args.args[0].endswith("/images/generations")
    assert "image" not in post.call_args.kwargs["json"]


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


def test_base_url_with_userinfo_is_rejected(provider, monkeypatch):
    monkeypatch.setenv(
        "H20_KEYS_BASE_URL", "http://username:password@127.0.0.1:18750/v1"
    )
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("a lighthouse")

    assert result["error_type"] == "invalid_configuration"
    post.assert_not_called()


def test_gateway_error_is_standard_and_does_not_fallback(provider, monkeypatch):
    post = MagicMock(return_value=_response(status_code=502, text="upstream failed"))
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("a lighthouse")

    assert result["success"] is False
    assert result["error_type"] == "upstream_error"
    assert result["provider"] == "human20-keys-openai-codex"
    assert post.call_count == 1


def test_edit_server_error_does_not_leak_secret_or_source(
    provider, monkeypatch, tmp_path, caplog
):
    source = tmp_path / "private-source.png"
    source.write_bytes(_image_bytes("PNG"))
    secret = "server-secret-that-must-not-leak"
    response = _response(
        status_code=502,
        payload={"detail": secret, "source": str(source)},
    )
    monkeypatch.setattr(
        provider_module.requests, "post", MagicMock(return_value=response)
    )

    result = provider.generate("edit", image_url=str(source))

    assert result["error_type"] == "upstream_error"
    assert secret not in json.dumps(result)
    assert str(source) not in json.dumps(result)
    assert secret not in caplog.text
    assert str(source) not in caplog.text


def test_timeout_is_standard_error(provider, monkeypatch):
    monkeypatch.setattr(
        provider_module.requests,
        "post",
        MagicMock(side_effect=requests.Timeout("slow")),
    )

    result = provider.generate("a lighthouse")

    assert result["success"] is False
    assert result["error_type"] == "timeout"


@pytest.mark.parametrize(
    ("status_code", "code", "error_type"),
    [
        (400, "invalid_request", "invalid_request"),
        (401, "invalid_api_key", "auth_required"),
        (402, "insufficient_credits", "insufficient_credits"),
        (403, "provider_scope_denied", "forbidden"),
        (409, "already_completed", "already_completed"),
        (409, "request_in_flight", "request_in_flight"),
        (429, "rate_limit_exceeded", "rate_limited"),
        (429, "capacity_rejected", "capacity_rejected"),
        (502, "upstream_failure", "upstream_error"),
        (503, "upstream_unavailable", "upstream_unavailable"),
        (504, "upstream_timeout", "timeout"),
    ],
)
def test_gateway_status_and_code_mapping_is_sanitized(
    provider, monkeypatch, status_code, code, error_type
):
    if status_code == 409:
        detail = {"code": code, "request_id": "req-original"}
    elif status_code == 429 and code == "capacity_rejected":
        detail = "image generation capacity exceeded"
    else:
        detail = "contains customer-secret"
    body = json.dumps({"detail": detail}).encode()

    class ErrorHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with _serve(ErrorHandler) as root:
        monkeypatch.setenv("H20_KEYS_BASE_URL", f"{root}/v1")
        result = provider.generate("a lighthouse")

    assert result["success"] is False
    assert result["error_type"] == error_type
    assert "customer-secret" not in result["error"]


def test_fastapi_capacity_detail_maps_to_capacity_rejected(provider, monkeypatch):
    body = json.dumps({"detail": "image generation capacity exceeded"}).encode()

    class CapacityHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with _serve(CapacityHandler) as root:
        monkeypatch.setenv("H20_KEYS_BASE_URL", f"{root}/v1")
        result = provider.generate("a lighthouse")
    assert result["error_type"] == "capacity_rejected"


def test_empty_or_invalid_image_response_fails_safely(provider, monkeypatch):
    monkeypatch.setattr(
        provider_module.requests,
        "post",
        MagicMock(return_value=_response(payload={"data": []})),
    )
    result = provider.generate("a lighthouse")
    assert result["success"] is False
    assert result["error_type"] == "empty_response"


@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"b64_json": "not valid base64%%%"}]},
        {"data": [{"b64_json": base64.b64encode(b"not a png").decode()}]},
        {"data": [{"b64_json": _PNG[:-4] + "AAAA"}]},
        {"data": [{"b64_json": _png_b64((1, 1))}]},
    ],
)
def test_corrupt_or_wrong_dimension_png_is_never_persisted(
    provider, monkeypatch, tmp_path, payload
):
    monkeypatch.setattr(
        provider_module.requests,
        "post",
        MagicMock(return_value=_response(payload=payload)),
    )

    result = provider.generate("a lighthouse", aspect_ratio="square")

    assert result["success"] is False
    assert result["error_type"] == "invalid_response"
    assert list(tmp_path.rglob("*.png")) == []


def test_decoded_image_size_is_bounded(provider, monkeypatch, tmp_path):
    monkeypatch.setattr(provider_module, "_MAX_IMAGE_BYTES", 16)
    monkeypatch.setattr(
        provider_module.requests,
        "post",
        MagicMock(return_value=_response(payload={"data": [{"b64_json": _PNG}]})),
    )

    result = provider.generate("a lighthouse", aspect_ratio="square")

    assert result["error_type"] == "invalid_response"
    assert list(tmp_path.rglob("*.png")) == []


def test_persisted_png_and_cache_are_private_and_atomic(provider, monkeypatch):
    monkeypatch.setattr(
        provider_module.requests,
        "post",
        MagicMock(return_value=_response(payload={"data": [{"b64_json": _PNG}]})),
    )

    result = provider.generate("a lighthouse", aspect_ratio="square")

    path = Path(result["image"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert not list(path.parent.glob(".*.tmp"))


@pytest.mark.parametrize("prompt", [None, 1, b"bytes", [], {}])
def test_prompt_type_is_validated_without_exception(provider, monkeypatch, prompt):
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)
    result = provider.generate(prompt)  # type: ignore[arg-type]
    assert result["error_type"] == "invalid_argument"
    post.assert_not_called()


def test_prompt_utf8_byte_limit_is_enforced(provider, monkeypatch):
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)
    result = provider.generate("é" * 8193)
    assert result["error_type"] == "invalid_argument"
    post.assert_not_called()


def test_prompt_must_be_valid_utf8(provider, monkeypatch):
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)
    result = provider.generate("\ud800")
    assert result["error_type"] == "invalid_argument"
    post.assert_not_called()


@pytest.mark.parametrize(
    "image_url",
    [
        "",
        "   ",
        1,
        "data:image/png;base64,%%%",
        "data:image/png,AAAA",
        "data:text/plain;base64,AAAA",
        "data:image/tiff;base64,AAAA",
        "/definitely/missing/source.png",
    ],
)
def test_malformed_primary_image_is_rejected_before_http(
    provider, monkeypatch, image_url
):
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)
    result = provider.generate("edit", image_url=image_url)
    assert result["error_type"] == "invalid_argument"
    post.assert_not_called()


def test_data_url_declared_mime_must_match_magic(provider, monkeypatch):
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)
    mismatched = _data_url("image/png", _image_bytes("JPEG"))

    result = provider.generate("edit", image_url=mismatched)

    assert result["error_type"] == "invalid_argument"
    post.assert_not_called()


@pytest.mark.parametrize("mode", ["local", "data"])
def test_primary_image_size_is_bounded_before_http(
    provider, monkeypatch, tmp_path, mode
):
    raw = _image_bytes("PNG")
    monkeypatch.setattr(provider_module, "_MAX_INPUT_IMAGE_BYTES", len(raw) - 1)
    source = tmp_path / "source.png"
    source.write_bytes(raw)
    image_url = str(source) if mode == "local" else _data_url("image/png", raw)
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("edit", image_url=image_url)

    assert result["error_type"] == "invalid_argument"
    post.assert_not_called()


@pytest.mark.parametrize("mode", ["local", "data"])
def test_magic_bytes_must_also_decode_with_pillow(
    provider, monkeypatch, tmp_path, mode
):
    raw = b"\xff\xd8\xffcorrupt-jpeg"
    source = tmp_path / "source.jpg"
    source.write_bytes(raw)
    image_url = str(source) if mode == "local" else _data_url("image/jpeg", raw)
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)

    result = provider.generate("edit", image_url=image_url)

    assert result["error_type"] == "invalid_argument"
    post.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"aspect_ratio": 1},
        {"aspect_ratio": "wide"},
        {"model": 1},
        {"model": "gpt-image-3-high"},
    ],
)
def test_tier_and_aspect_are_strictly_validated(provider, monkeypatch, kwargs):
    post = MagicMock()
    monkeypatch.setattr(provider_module.requests, "post", post)
    result = provider.generate("a lighthouse", **kwargs)
    assert result["error_type"] == "invalid_argument"
    post.assert_not_called()


def test_bounded_reader_enforces_total_deadline_during_trickle(monkeypatch):
    class Raw:
        def __init__(self):
            self.calls = 0

        def read(self, _size, decode_content=True):
            self.calls += 1
            return b"x" if self.calls < 10 else b""

    response = MagicMock()
    response.raw = Raw()
    times = iter([0.0, 0.0, 100.0, 200.0, 329.9, 330.1])
    monkeypatch.setattr(provider_module.time, "monotonic", lambda: next(times))

    with pytest.raises(provider_module._TotalDeadlineExceeded):
        provider_module._read_bounded_body(response, deadline=330.0, max_bytes=100)


def test_bounded_reader_interrupts_blocked_read_at_total_deadline():
    closed = threading.Event()

    class Raw:
        def read(self, _size, decode_content=True):
            closed.wait(timeout=1.0)
            return b""

    response = MagicMock()
    response.raw = Raw()
    response.close.side_effect = closed.set
    started = time.monotonic()

    with pytest.raises(provider_module._TotalDeadlineExceeded):
        provider_module._read_bounded_body(
            response,
            deadline=provider_module.time.monotonic() + 0.05,
            max_bytes=100,
        )

    assert time.monotonic() - started < 0.5
    assert response.close.called


def test_real_http_steady_drip_obeys_total_deadline(provider, monkeypatch):
    disconnected = threading.Event()

    class DripHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for byte in b'{"data":[{"b64_json":"' + (b"A" * 1000):
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()

    monkeypatch.setattr(provider_module, "_TIMEOUT_SECONDS", 0.25)
    before_non_daemon = {
        thread.ident for thread in threading.enumerate() if not thread.daemon
    }
    with _serve(DripHandler) as root:
        monkeypatch.setenv("H20_KEYS_BASE_URL", f"{root}/v1")
        started = time.monotonic()
        result = provider.generate("a lighthouse")
        elapsed = time.monotonic() - started

    assert result["error_type"] == "timeout"
    assert elapsed < 1.0
    assert disconnected.wait(timeout=1.0)
    after_non_daemon = {
        thread.ident for thread in threading.enumerate() if not thread.daemon
    }
    assert after_non_daemon == before_non_daemon


def test_real_http_large_bounded_body_streams_successfully(provider, monkeypatch):
    body = json.dumps({
        "created": 1,
        "data": [{"b64_json": _PNG}],
        "padding": "x" * (1024 * 1024),
    }).encode()

    class LargeHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with _serve(LargeHandler) as root:
        monkeypatch.setenv("H20_KEYS_BASE_URL", f"{root}/v1")
        result = provider.generate("a lighthouse", aspect_ratio="square")

    assert result["success"] is True


@pytest.mark.parametrize("redirect_status", [302, 307, 308])
def test_redirect_is_not_followed_or_replayed(provider, monkeypatch, redirect_status):
    target_contacted = threading.Event()
    replayed_bodies = []

    class TargetHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _contacted(self):
            length = int(self.headers.get("Content-Length", "0"))
            replayed_bodies.append(self.rfile.read(length))
            target_contacted.set()
            self.send_response(502)
            self.end_headers()

        do_GET = _contacted
        do_POST = _contacted

    with _serve(TargetHandler) as target_root:

        class RedirectHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                self.send_response(redirect_status)
                self.send_header("Location", f"{target_root}/v1/images/generations")
                self.send_header("Content-Length", "0")
                self.end_headers()

        with _serve(RedirectHandler) as redirect_root:
            monkeypatch.setenv("H20_KEYS_BASE_URL", f"{redirect_root}/v1")
            result = provider.generate("never replay this prompt")

    assert result["error_type"] == "protocol_error"
    assert not target_contacted.is_set()
    assert replayed_bodies == []


def test_bounded_reader_rejects_oversize_before_buffering():
    class Raw:
        def __init__(self):
            self.calls = 0

        def read(self, _size, decode_content=True):
            self.calls += 1
            return b"12345" if self.calls == 1 else b""

    response = MagicMock()
    response.raw = Raw()
    with pytest.raises(provider_module._ResponseTooLarge):
        provider_module._read_bounded_body(response, deadline=float("inf"), max_bytes=4)


def test_stream_read_timeout_is_mapped_to_timeout(provider, monkeypatch):
    response = _response(payload={"data": []})
    response.raw.read = MagicMock(
        side_effect=ReadTimeoutError(None, None, "read timed out")
    )
    monkeypatch.setattr(
        provider_module.requests, "post", MagicMock(return_value=response)
    )

    result = provider.generate("a lighthouse")

    assert result["error_type"] == "timeout"
    assert response.close.called


def test_close_error_does_not_override_success(provider, monkeypatch):
    response = _response(payload={"data": [{"b64_json": _PNG}]})
    response.close.side_effect = RuntimeError("secret-bearing close failure")
    monkeypatch.setattr(
        provider_module.requests, "post", MagicMock(return_value=response)
    )

    result = provider.generate("a lighthouse", aspect_ratio="square")

    assert result["success"] is True


def test_close_error_does_not_override_mapped_http_error(provider, monkeypatch):
    response = _response(
        status_code=402,
        payload={"detail": "insufficient credits"},
    )
    response.close.side_effect = RuntimeError("secret-bearing close failure")
    monkeypatch.setattr(
        provider_module.requests, "post", MagicMock(return_value=response)
    )

    result = provider.generate("a lighthouse")

    assert result["error_type"] == "insufficient_credits"


def test_close_error_does_not_override_read_timeout(provider, monkeypatch):
    response = _response(payload={"data": []})
    response.raw.read = MagicMock(
        side_effect=ReadTimeoutError(None, None, "read timed out")
    )
    response.close.side_effect = RuntimeError("secret-bearing close failure")
    monkeypatch.setattr(
        provider_module.requests, "post", MagicMock(return_value=response)
    )

    result = provider.generate("a lighthouse")

    assert result["error_type"] == "timeout"


def test_concurrent_watchdog_double_close_preserves_timeout_without_secret_log(
    provider, monkeypatch, caplog
):
    closed = threading.Event()
    close_calls = 0

    class BlockingRaw:
        def read1(self, _size, decode_content=True):
            closed.wait(timeout=1.0)
            return b""

    response = _response(payload={"data": []})
    response.raw = BlockingRaw()

    def close():
        nonlocal close_calls
        close_calls += 1
        closed.set()
        if close_calls > 1:
            raise RuntimeError("secret-bearing close failure")

    response.close.side_effect = close
    monkeypatch.setattr(provider_module, "_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(
        provider_module.requests, "post", MagicMock(return_value=response)
    )

    result = provider.generate("a lighthouse")

    assert result["error_type"] == "timeout"
    assert close_calls >= 2
    assert "secret-bearing close failure" not in caplog.text


def test_atomic_persistence_cleans_temp_file_on_replace_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        provider_module.os,
        "replace",
        MagicMock(side_effect=OSError("replace failed")),
    )

    with pytest.raises(OSError):
        provider_module._atomic_persist_png(b"complete-png", prefix="test")

    image_dir = tmp_path / "cache" / "images"
    assert list(image_dir.iterdir()) == []


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
