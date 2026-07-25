"""Text-only image generation through the Human20 Keys tenant boundary."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    success_response,
)

logger = logging.getLogger(__name__)

_PROVIDER = "human20-keys-openai-codex"
_API_MODEL = "gpt-image-2"
_DEFAULT_MODEL = "gpt-image-2-medium"
_TIMEOUT_SECONDS = 330

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


def _resolve_model(requested: Any = None) -> Tuple[str, Dict[str, str]]:
    if isinstance(requested, str) and requested in _MODELS:
        return requested, _MODELS[requested]

    config = _load_image_config()
    candidate = config.get("model")
    if isinstance(candidate, str) and candidate in _MODELS:
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
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        return None
    return f"{value}/images/generations"


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
        clean_prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        tier_id, metadata = _resolve_model(kwargs.get("model"))

        if (
            (isinstance(image_url, str) and image_url.strip())
            or reference_image_urls
            or kwargs.get("reference_images")
        ):
            return error_response(
                error=(
                    "Human20 Keys Codex image generation is text-only; "
                    "reference images are unsupported."
                ),
                error_type="capability_unsupported",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )

        if not clean_prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider=_PROVIDER,
                model=tier_id,
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

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
        except requests.Timeout:
            return error_response(
                error="Human20 Keys image generation timed out",
                error_type="timeout",
                provider=_PROVIDER,
                model=tier_id,
                prompt=clean_prompt,
                aspect_ratio=aspect,
            )
        except (requests.RequestException, ValueError) as exc:
            logger.debug("Human20 Keys image generation failed: %s", exc)
            return error_response(
                error="Human20 Keys image generation request failed",
                error_type="api_error",
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

        try:
            image_path = save_b64_image(
                encoded,
                prefix=f"human20_keys_{tier_id}",
                extension="png",
            )
        except Exception as exc:
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
