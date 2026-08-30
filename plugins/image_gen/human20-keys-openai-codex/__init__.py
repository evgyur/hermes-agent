"""Image generation and secure primary-image edits through Human20 Keys."""

from __future__ import annotations

import base64
import binascii
import datetime
import io
import json
import logging
import math
import os
import re
import stat
import struct
import tempfile
import threading
import time
import uuid
import warnings
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps
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
_MAX_INPUT_IMAGE_BYTES = 25 * 1024 * 1024
_MAX_INPUT_IMAGE_PIXELS = 12_000_000
_MAX_INPUT_IMAGE_DIMENSION = 8192
_MAX_SUCCESS_BODY_BYTES = ((_MAX_IMAGE_BYTES + 2) // 3 * 4) + 1024 * 1024
_MAX_ERROR_BODY_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ALLOWED_INPUT_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_PIL_FORMAT_MIMES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}
_SENSITIVE_PATH_PARTS = frozenset({
    ".env",
    ".ssh",
    ".aws",
    ".gnupg",
    "auth.json",
    "credentials",
    "secrets",
})
_EXPLICIT_OUTPUT_SIZE_RE = re.compile(
    r"(?:output(?:\s+size)?|size|dimensions?|format|resolution|instagram|"
    r"размер|формат|разрешение)[^\n]{0,48}?"
    r"(?<!\d)(\d{2,4})\s*[x×]\s*(\d{2,4})(?!\d)",
    re.IGNORECASE,
)

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


def _image_api_url(base_url: str, endpoint: str) -> Optional[str]:
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
    return f"{value}/images/{endpoint}"


def _generation_url(base_url: str) -> Optional[str]:
    return _image_api_url(base_url, "generations")


def _edit_url(base_url: str) -> Optional[str]:
    return _image_api_url(base_url, "edits")


def _sniff_input_mime(raw: bytes) -> Optional[str]:
    if raw.startswith(_PNG_SIGNATURE):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_input_image(
    raw: bytes, *, declared_mime: Optional[str]
) -> Tuple[str, bytes]:
    if not raw:
        raise ValueError("Primary image is empty")
    if len(raw) > _MAX_INPUT_IMAGE_BYTES:
        raise ValueError("Primary image exceeds size limit")

    magic_mime = _sniff_input_mime(raw)
    if magic_mime not in _ALLOWED_INPUT_MIMES:
        raise ValueError("Primary image has an unsupported format")
    if declared_mime is not None and declared_mime != magic_mime:
        raise ValueError("Primary image MIME does not match its bytes")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                pillow_mime = _PIL_FORMAT_MIMES.get(image.format or "")
                if pillow_mime != magic_mime:
                    raise ValueError(
                        "Primary image decoder format does not match its bytes"
                    )
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Animated primary images are not supported")
                if (
                    image.width > _MAX_INPUT_IMAGE_DIMENSION
                    or image.height > _MAX_INPUT_IMAGE_DIMENSION
                    or image.width * image.height > _MAX_INPUT_IMAGE_PIXELS
                ):
                    raise ValueError("Primary image pixel count exceeds safe limit")
                image.load()
                image = ImageOps.exif_transpose(image)
                has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
                canonical_image = image.convert("RGBA" if has_alpha else "RGB")
                canonical_image.info.clear()
                output = io.BytesIO()
                canonical_image.save(output, format="PNG", optimize=False)
                canonical_raw = output.getvalue()
    except ValueError:
        raise
    except (
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Primary image could not be decoded") from exc
    if len(canonical_raw) > _MAX_INPUT_IMAGE_BYTES:
        raise ValueError("Canonical primary image exceeds size limit")
    return "image/png", canonical_raw


def _decode_input_data_url(source: str) -> Tuple[str, bytes]:
    max_encoded = ((_MAX_INPUT_IMAGE_BYTES + 2) // 3) * 4
    if len(source) > max_encoded + 64:
        raise ValueError("Primary image data URL exceeds size limit")
    header, separator, encoded = source.partition(",")
    if not separator:
        raise ValueError("Malformed primary image data URL")
    parts = header[5:].split(";") if header.lower().startswith("data:") else []
    if len(parts) != 2 or parts[1].lower() != "base64":
        raise ValueError("Primary image data URL must be base64 encoded")
    declared_mime = parts[0].lower()
    if declared_mime not in _ALLOWED_INPUT_MIMES:
        raise ValueError("Primary image data URL has an unsupported MIME")
    try:
        encoded_bytes = encoded.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Primary image data URL is not ASCII base64") from exc
    if not encoded_bytes or len(encoded_bytes) > max_encoded:
        raise ValueError("Primary image data URL exceeds size limit")
    try:
        raw = base64.b64decode(encoded_bytes, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Primary image data URL has invalid base64") from exc
    return declared_mime, raw


def _read_local_input_image(source: str) -> bytes:
    source_path = Path(source).expanduser()
    if any(part.lower() in _SENSITIVE_PATH_PARTS for part in source_path.parts):
        raise ValueError("Primary image path is sensitive")
    file_descriptor = -1
    directory_descriptor = -1
    try:
        from agent.file_safety import get_read_block_error

        path = Path(os.path.abspath(source_path))
        if any(part.lower() in _SENSITIVE_PATH_PARTS for part in path.parts):
            raise ValueError("Primary image path is sensitive")
        try:
            blocked = get_read_block_error(str(path))
        except Exception as exc:
            raise ValueError("Primary image path could not be validated") from exc
        if blocked:
            raise ValueError("Primary image path is sensitive")

        directory_descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        components = path.parts[1:]
        if not components:
            raise ValueError("Primary image path is not a regular file")
        for component in components[:-1]:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory_descriptor,
            )
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        file_descriptor = os.open(
            components[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_descriptor,
        )
        file_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("Primary image path is not a regular file")
        if file_stat.st_size > _MAX_INPUT_IMAGE_BYTES:
            raise ValueError("Primary image file exceeds size limit")
        with os.fdopen(file_descriptor, "rb", closefd=False) as stream:
            raw = stream.read(_MAX_INPUT_IMAGE_BYTES + 1)
    except ValueError:
        raise
    except (ImportError, OSError, RuntimeError) as exc:
        raise ValueError("Primary image file could not be read") from exc
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
    if len(raw) > _MAX_INPUT_IMAGE_BYTES:
        raise ValueError("Primary image file exceeds size limit")
    return raw


def _canonicalize_primary_image(source: Any) -> str:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Primary image must be a non-empty string")
    value = source.strip()
    if value.lower().startswith("data:"):
        declared_mime, raw = _decode_input_data_url(value)
    else:
        declared_mime = None
        raw = _read_local_input_image(value)
    mime, canonical_raw = _validate_input_image(raw, declared_mime=declared_mime)
    encoded = base64.b64encode(canonical_raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _explicit_output_size(prompt: str) -> Optional[Tuple[int, int]]:
    """Return a bounded output size only when the prompt labels it as such."""
    matches = list(_EXPLICIT_OUTPUT_SIZE_RE.finditer(prompt))
    if not matches:
        return None
    width, height = (int(value) for value in matches[-1].groups())
    if not (256 <= width <= 4096 and 256 <= height <= 4096):
        return None
    if width * height > 16_777_216:
        return None
    return width, height


def _fit_png_to_explicit_size(path: Path, prompt: str) -> Optional[Tuple[int, int]]:
    """Deterministically format GPT Image output; never generate a fallback."""
    target = _explicit_output_size(prompt)
    if target is None:
        return None
    with Image.open(path) as source:
        source.load()
        if source.format != "PNG":
            raise ValueError("Generated edit is not a PNG")
        fitted = ImageOps.fit(
            source.convert("RGB"), target, method=Image.Resampling.LANCZOS
        )
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.fit-", suffix=".png", dir=path.parent
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            fitted.save(output, format="PNG", optimize=True)
            output.flush()
            os.fsync(output.fileno())
        if temp_path.stat().st_size > _MAX_IMAGE_BYTES:
            raise ValueError("Explicitly sized PNG exceeds size limit")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    with Image.open(path) as verified:
        verified.load()
        if verified.format != "PNG" or verified.size != target:
            raise ValueError("Explicit output sizing failed verification")
    return target


class _TotalDeadlineExceeded(TimeoutError):
    pass


class _ResponseTooLarge(ValueError):
    pass


def _close_response_quietly(response: requests.Response) -> None:
    """Close a response without exposing teardown exception contents."""
    try:
        response.close()
    except Exception:
        # Cleanup failures can contain transport/request details. The response
        # has already been classified, and the deadline socket timeout remains
        # the final backstop, so never replace or leak the original result.
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
        _close_response_quietly(response)

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
                read1 = getattr(response.raw, "read1", None)
                if callable(read1):
                    chunk = read1(_READ_CHUNK_BYTES, decode_content=True)
                else:
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
    detail_text = ""
    try:
        payload = _parse_json(body)
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, dict) and isinstance(detail.get("code"), str):
                code = detail["code"]
            elif isinstance(detail, str):
                detail_text = detail.lower()
            error = payload.get("error")
            if (
                not code
                and isinstance(error, dict)
                and isinstance(error.get("code"), str)
            ):
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
        if code == "capacity_rejected" or "capacity exceeded" in detail_text:
            return "capacity_rejected"
        return "rate_limited"
    if 300 <= status_code < 400:
        return "protocol_error"
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
        expected_crc = struct.unpack(
            ">I", image_bytes[offset + 8 + length : chunk_end]
        )[0]
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
    """Generate or edit gpt-image-2 PNGs with a Human20 credential."""

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
        return {"modalities": ["text", "image"], "max_reference_images": 0}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "managed",
            "tag": "gpt-image-2 generation and primary-image edits through Human20 Keys",
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

        if reference_image_urls or kwargs.get("reference_images"):
            return error_response(
                error=(
                    "Human20 Keys image edits support exactly one primary image "
                    "through image_url; additional reference images are unsupported."
                ),
                error_type="capability_unsupported",
                provider=_PROVIDER,
                prompt=prompt_for_error,
                aspect_ratio=aspect_for_error,
            )
        if isinstance(image_url, str) and image_url.strip().lower().startswith((
            "http://",
            "https://",
        )):
            return error_response(
                error=(
                    "Human20 Keys primary image edits require a local file path "
                    "or base64 data:image URL; remote HTTP(S) URLs are not allowed."
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

        is_edit = image_url is not None
        canonical_image: Optional[str] = None
        if is_edit:
            try:
                canonical_image = _canonicalize_primary_image(image_url)
            except ValueError:
                return error_response(
                    error=(
                        "Primary image must be a valid PNG, JPEG, GIF, or WebP "
                        "local file or base64 data:image URL no larger than 25 MiB"
                    ),
                    error_type="invalid_argument",
                    provider=_PROVIDER,
                    model=tier_id,
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

        url = _edit_url(base_url) if is_edit else _generation_url(base_url)
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
        if canonical_image is not None:
            payload["image"] = canonical_image

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
                allow_redirects=False,
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
                _close_response_quietly(response)

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
                "protocol_error": "Human20 Keys returned an unexpected redirect",
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
        image_path: Optional[Path] = None
        try:
            image_bytes = _decode_and_validate_png(encoded, expected_size)
            image_path = _atomic_persist_png(
                image_bytes,
                prefix=f"human20_keys_{tier_id}",
            )
            explicit_size = _fit_png_to_explicit_size(image_path, clean_prompt)
        except ValueError as exc:
            if image_path is not None:
                try:
                    image_path.unlink()
                except OSError:
                    pass
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
            if image_path is not None:
                try:
                    image_path.unlink()
                except OSError:
                    pass
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
            modality="image" if is_edit else "text",
            extra={
                "size": (
                    f"{explicit_size[0]}x{explicit_size[1]}"
                    if explicit_size is not None
                    else size
                ),
                "quality": quality,
            },
        )


def register(ctx) -> None:
    """Register the Human20 Keys image backend."""
    ctx.register_image_gen_provider(Human20KeysOpenAICodexImageGenProvider())
