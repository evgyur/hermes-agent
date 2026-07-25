"""Text-only image generation through the Human20 Keys tenant boundary."""

from __future__ import annotations

import base64
import binascii
import datetime
import io
import json
import logging
import math
import os
import struct
import tempfile
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests
from PIL import Image
from urllib3.exceptions import HTTPError as Urllib3HTTPError
from urllib3.exceptions import ReadTimeoutError
from urllib3.util import Timeout

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    success_response,
)

logger = logging.getLogger(__name__)

_PROVIDER = "human20-keys-openai-codex"
_API_MODEL = "gpt-image-2"
_DEFAULT_MODEL = "gpt-image-2-medium"
_TIMEOUT_SECONDS = 330
_MAX_IMAGE_BYTES = 25 * 1024 * 1024
_MAX_SUCCESS_BODY_BYTES = ((_MAX_IMAGE_BYTES + 2) // 3 * 4) + 1024 * 1024
_MAX_ERROR_BODY_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_MODELS: Dict[str, Dict[str, str]] = {
    "gpt-image-2-low": {
        "display": "GPT Image 2 (Low)",
        "quality": "low",
        "speed": "~15s",
        "strengths": "Fast iteration",
    },
    "gpt-image-2-medium": {
        "display": "GPT Image 2 (Medium)",
        "quality": "medium",
        "speed": "~40s",
        "strengths": "Balanced",
    },
    "gpt-image-2-high": {
        "display": "GPT Image 2 (High)",
        "quality": "high",
        "speed": "~2min",
        "strengths": "Highest fidelity",
    },
}

_SIZES = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}


def _load_image_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        config = load_config()
        section = config.get("image_gen") if isinstance(config, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load image_gen config: %s", exc)
        return {}


_UNSET = object()


def _resolve_model(requested: Any = _UNSET) -> Tuple[str, Dict[str, str]]:
    if requested is not _UNSET:
        if not isinstance(requested, str) or requested not in _MODELS:
            raise ValueError("Unsupported image quality tier")
        return requested, _MODELS[requested]

    config = _load_image_config()
    candidate = config.get("model")
    if candidate is not None:
        if not isinstance(candidate, str) or candidate not in _MODELS:
            raise ValueError("Unsupported configured image quality tier")
        return candidate, _MODELS[candidate]

    return _DEFAULT_MODEL, _MODELS[_DEFAULT_MODEL]


def _generation_url(base_url: str) -> Optional[str]:
    value = base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        return None
    return f"{value}/images/generations"


class _TotalDeadlineExceeded(TimeoutError):
    pass


class _ResponseTooLarge(ValueError):
    pass


def _set_raw_read_timeout(response: requests.Response, seconds: float) -> None:
    """Best-effort reduction of the live socket timeout to the remaining budget."""
    try:
        sock = response.raw._fp.fp.raw._sock  # type: ignore[attr-defined]
        sock.settimeout(max(0.001, seconds))
    except (AttributeError, OSError):
        # The initial urllib3 total timeout still protects real transports;
        # test doubles and already-buffered responses need no socket update.
        return


def _read_bounded_body(
    response: requests.Response,
    *,
    deadline: float,
    max_bytes: int,
) -> bytes:
    """Read a response incrementally under one wall-clock deadline and cap."""
    initial_remaining = deadline - time.monotonic()
    if initial_remaining <= 0:
        raise _TotalDeadlineExceeded
    deadline_fired = threading.Event()

    def abort_at_deadline() -> None:
        deadline_fired.set()
        try:
            response.close()
        except Exception:
            pass

    watchdog: Optional[threading.Timer] = None
    if math.isfinite(initial_remaining):
        watchdog = threading.Timer(initial_remaining, abort_at_deadline)
        watchdog.daemon = True
        watchdog.start()
    chunks: List[bytes] = []
    total = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or deadline_fired.is_set():
                raise _TotalDeadlineExceeded
            _set_raw_read_timeout(response, remaining)
            try:
                chunk = response.raw.read(_READ_CHUNK_BYTES, decode_content=True)
            except Exception as exc:
                if deadline_fired.is_set() or time.monotonic() >= deadline:
                    raise _TotalDeadlineExceeded from exc
                raise
            if deadline_fired.is_set() or time.monotonic() >= deadline:
                raise _TotalDeadlineExceeded
            if not chunk:
                return b"".join(chunks)
            if not isinstance(chunk, bytes):
                raise ValueError("Response stream returned non-bytes data")
            total += len(chunk)
            if total > max_bytes:
                raise _ResponseTooLarge
            chunks.append(chunk)
    finally:
        if watchdog is not None:
            watchdog.cancel()


def _parse_json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"))


def _gateway_error_type(status_code: int, body: bytes) -> str:
    code = ""
    try:
        payload = _parse_json(body)
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            code = error["code"]
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    if status_code == 400:
        return "invalid_request"
    if status_code == 401:
        return "auth_required"
    if status_code == 402:
        return "insufficient_credits"
    if status_code == 403:
        return "forbidden"
    if status_code == 409:
        if code in {"already_completed", "request_in_flight"}:
            return code
        return "conflict"
    if status_code == 429:
        return "capacity_rejected" if code == "capacity_rejected" else "rate_limited"
    if status_code == 502:
        return "upstream_error"
    if status_code == 503:
        return "upstream_unavailable"
    if status_code == 504:
        return "timeout"
    return "api_error"


def _decode_and_validate_png(encoded: str, expected_size: Tuple[int, int]) -> bytes:
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Missing image payload")
    try:
        encoded_bytes = encoded.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Image payload is not ASCII base64") from exc
    if len(encoded_bytes) > ((_MAX_IMAGE_BYTES + 2) // 3 * 4):
        raise ValueError("Decoded image exceeds size limit")
    try:
        image_bytes = base64.b64decode(encoded_bytes, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 image payload") from exc
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise ValueError("Decoded image exceeds size limit")
    if not image_bytes.startswith(_PNG_SIGNATURE):
        raise ValueError("Image payload is not PNG")

    offset = len(_PNG_SIGNATURE)
    seen_ihdr = False
    seen_idat = False
    seen_iend = False
    dimensions: Optional[Tuple[int, int]] = None
    while offset < len(image_bytes):
        if len(image_bytes) - offset < 12:
            raise ValueError("Truncated PNG chunk")
        length = struct.unpack(">I", image_bytes[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(image_bytes):
            raise ValueError("Truncated PNG chunk data")
        kind = image_bytes[offset + 4 : offset + 8]
        data = image_bytes[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", image_bytes[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError("PNG CRC mismatch")
        if not seen_ihdr:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG must begin with IHDR")
            dimensions = struct.unpack(">II", data[:8])
            seen_ihdr = True
        elif kind == b"IHDR":
            raise ValueError("PNG has duplicate IHDR")
        if kind == b"IDAT":
            seen_idat = True
        if kind == b"IEND":
            if length != 0:
                raise ValueError("Invalid PNG IEND")
            seen_iend = True
            offset = chunk_end
            break
        offset = chunk_end

    if not (seen_ihdr and seen_idat and seen_iend) or offset != len(image_bytes):
        raise ValueError("Incomplete or trailing PNG framing")
    if dimensions != expected_size:
        raise ValueError("PNG dimensions do not match request")

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.format != "PNG" or image.size != expected_size:
                raise ValueError("Decoded PNG metadata does not match request")
            image.verify()
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
    except Exception as exc:
        raise ValueError("PNG decode failed") from exc
    return image_bytes


def _atomic_persist_png(image_bytes: bytes, *, prefix: str) -> Path:
    from hermes_constants import get_hermes_home

    cache_dir = get_hermes_home() / "cache"
    images_dir = cache_dir / "images"
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    images_dir.mkdir(exist_ok=True, mode=0o700)
    os.chmod(cache_dir, 0o700)
    os.chmod(images_dir, 0o700)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    final_path = images_dir / f"{prefix}_{timestamp}_{uuid.uuid4().hex[:8]}.png"
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{prefix}_", suffix=".tmp", dir=images_dir
    )
    temp_path = Path(temp_name)
    replaced = False
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(image_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, final_path)
        replaced = True
        os.chmod(final_path, 0o600)
        directory_fd = os.open(images_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return final_path
    except Exception:
        if replaced:
            try:
                final_path.unlink()
            except OSError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


class Human20KeysOpenAICodexImageGenProvider(ImageGenProvider):
    """Generate gpt-image-2 PNGs using a Human20 customer credential."""

    @property
    def name(self) -> str:
        return _PROVIDER

    @property
    def display_name(self) -> str:
        return "Human20 Keys (GPT Image 2)"

    def is_available(self) -> bool:
        return bool(
            os.environ.get("H20_KEYS_BASE_URL", "").strip()
            and os.environ.get("H20_KEYS_API_KEY", "").strip()
        )

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": metadata["display"],
                "speed": metadata["speed"],
                "strengths": metadata["strengths"],
            }
            for model_id, metadata in _MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return _DEFAULT_MODEL

    def capabilities(self) -> Dict[str, Any]:
        return {"modalities": ["text"], "max_reference_images": 0}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "managed",
            "tag": "Text-only gpt-image-2 through Human20 Keys",
            "env_vars": [
                {"key": "H20_KEYS_BASE_URL", "prompt": "Human20 Keys base URL"},
                {"key": "H20_KEYS_API_KEY", "prompt": "Human20 customer key"},
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        aspect_for_error = (
            aspect_ratio if isinstance(aspect_ratio, str) else DEFAULT_ASPECT_RATIO
        )
        prompt_for_error = prompt if isinstance(prompt, str) else ""

        if (
            image_url is not None
            or reference_image_urls is not None
            or (
                "reference_images" in kwargs
                and kwargs.get("reference_images") is not None
            )
        ):
            return error_response(
                error=(
                    "Human20 Keys Codex image generation is text-only; "
                    "reference images are unsupported."
                ),
                error_type="capability_unsupported",
                provider=_PROVIDER,
                prompt=prompt_for_error,
                aspect_ratio=aspect_for_error,
            )

        if not isinstance(prompt, str):
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider=_PROVIDER,
                aspect_ratio=aspect_for_error,
            )
        clean_prompt = prompt.strip()
        try:
            prompt_bytes = clean_prompt.encode("utf-8")
        except UnicodeEncodeError:
            prompt_bytes = b""
        if not clean_prompt or not prompt_bytes or len(prompt_bytes) > 16_384:
            return error_response(
                error="Prompt must be non-empty and at most 16,384 UTF-8 bytes",
                error_type="invalid_argument",
                provider=_PROVIDER,
                prompt=clean_prompt,
                aspect_ratio=aspect_for_error,
            )
        if not isinstance(aspect_ratio, str) or aspect_ratio not in _SIZES:
            return error_response(
                error="Aspect ratio must be landscape, square, or portrait",
                error_type="invalid_argument",
                provider=_PROVIDER,
                prompt=clean_prompt,
                aspect_ratio=aspect_for_error,
            )
        aspect = aspect_ratio
        try:
            requested_model = kwargs["model"] if "model" in kwargs else _UNSET
            tier_id, metadata = _resolve_model(requested_model)
        except ValueError:
            return error_response(
                error="Model must be a supported gpt-image-2 quality tier",
                error_type="invalid_argument",
                provider=_PROVIDER,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )

        base_url = os.environ.get("H20_KEYS_BASE_URL", "")
        api_key = os.environ.get("H20_KEYS_API_KEY", "").strip()
        if not base_url.strip() or not api_key:
            return error_response(
                error="H20_KEYS_BASE_URL and H20_KEYS_API_KEY must be configured",
                error_type="auth_required",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )

        url = _generation_url(base_url)
        if url is None:
            return error_response(
                error="H20_KEYS_BASE_URL must be an HTTP(S) URL ending in /v1",
                error_type="invalid_configuration",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )

        quality = metadata["quality"]
        size = _SIZES[aspect]
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        }
        payload = {
            "model": _API_MODEL,
            "prompt": clean_prompt,
            "size": size,
            "quality": quality,
            "n": 1,
        }

        deadline = time.monotonic() + _TIMEOUT_SECONDS
        response: Optional[requests.Response] = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _TotalDeadlineExceeded
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=Timeout(
                    total=remaining,
                    connect=min(30.0, remaining),
                    read=remaining,
                ),
                stream=True,
            )
            max_body = (
                _MAX_SUCCESS_BODY_BYTES
                if response.status_code == 200
                else _MAX_ERROR_BODY_BYTES
            )
            raw_body = _read_bounded_body(
                response,
                deadline=deadline,
                max_bytes=max_body,
            )
        except (requests.Timeout, ReadTimeoutError, _TotalDeadlineExceeded):
            return error_response(
                error="Human20 Keys image generation timed out",
                error_type="timeout",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )
        except _ResponseTooLarge:
            return error_response(
                error="Human20 Keys response exceeded the permitted size",
                error_type="invalid_response",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )
        except (
            requests.RequestException,
            Urllib3HTTPError,
            OSError,
            ValueError,
        ) as exc:
            logger.debug("Human20 Keys image generation failed: %s", exc)
            return error_response(
                error="Human20 Keys image generation request failed",
                error_type="api_error",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )
        finally:
            if response is not None:
                response.close()

        if response.status_code != 200:
            error_type = _gateway_error_type(response.status_code, raw_body)
            messages = {
                "invalid_request": "Human20 Keys rejected the image request",
                "auth_required": "Human20 Keys authentication failed",
                "insufficient_credits": "Human20 image credits are insufficient",
                "forbidden": "Human20 key lacks image provider or model access",
                "already_completed": "This image request was already completed",
                "request_in_flight": "This image request is already in flight",
                "conflict": "Human20 Keys reported an image request conflict",
                "rate_limited": "Human20 Keys image rate limit reached",
                "capacity_rejected": "Human20 Keys image capacity is busy",
                "upstream_error": "Image generation upstream failed",
                "upstream_unavailable": "Image generation upstream is unavailable",
                "timeout": "Image generation upstream timed out",
                "api_error": "Human20 Keys image generation request failed",
            }
            return error_response(
                error=messages[error_type],
                error_type=error_type,
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )

        try:
            body = _parse_json(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return error_response(
                error="Human20 Keys returned invalid JSON",
                error_type="invalid_response",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )

        data = body.get("data") if isinstance(body, dict) else None
        first = data[0] if isinstance(data, list) and data else None
        encoded = first.get("b64_json") if isinstance(first, dict) else None
        if not isinstance(encoded, str) or not encoded:
            return error_response(
                error="Human20 Keys returned no image data",
                error_type="empty_response",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )

        expected_size = tuple(int(value) for value in size.split("x", 1))
        try:
            image_bytes = _decode_and_validate_png(encoded, expected_size)
            image_path = _atomic_persist_png(
                image_bytes,
                prefix=f"human20_keys_{tier_id}",
            )
        except ValueError as exc:
            logger.debug("Human20 Keys returned invalid PNG: %s", exc)
            return error_response(
                error="Human20 Keys returned an invalid PNG",
                error_type="invalid_response",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )
        except (OSError, RuntimeError) as exc:
            logger.debug("Could not persist Human20 Keys image: %s", exc)
            return error_response(
                error="Could not save image to cache",
                error_type="io_error",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )

        return success_response(
            image=str(image_path),
            model=tier_id,
            prompt=clean_prompt,
            aspect_ratio=aspect,
            provider=_PROVIDER,
            extra={"size": size, "quality": quality},
        )


def register(ctx) -> None:
    """Register the Human20 Keys image backend."""
    ctx.register_image_gen_provider(Human20KeysOpenAICodexImageGenProvider())
