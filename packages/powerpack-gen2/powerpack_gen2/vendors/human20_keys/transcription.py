"""Groq speech-to-text through the scoped Human20 Keys proxy."""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import requests

from agent.transcription_provider import TranscriptionProvider


_PROVIDER = "human20-keys-groq"
_DEFAULT_MODEL = "whisper-large-v3"
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024


def _get_profile_env(key: str) -> str:
    """Resolve credentials from the active Hermes profile, then process env."""
    try:
        from hermes_cli.config import get_env_value

        return (get_env_value(key) or "").strip()
    except ImportError:
        return os.environ.get(key, "").strip()


def _stt_credential() -> str:
    return _get_profile_env("H20_KEYS_STT_API_KEY") or _get_profile_env(
        "H20_KEYS_API_KEY"
    )


def _transcription_url(base_url: str) -> Optional[str]:
    try:
        parsed = urlsplit(base_url.strip())
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
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            "/proxy/groq/openai/v1/audio/transcriptions",
            "",
            "",
        )
    )


def _bounded_response_body(response: requests.Response) -> bytes:
    body = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise ValueError("Human20 Keys transcription response is too large")
    return bytes(body)


class Human20KeysGroqTranscriptionProvider(TranscriptionProvider):
    """Transcribe audio without exposing the upstream Groq credential."""

    @property
    def name(self) -> str:
        return _PROVIDER

    @property
    def display_name(self) -> str:
        return "Human20 Keys (Groq Whisper)"

    def is_available(self) -> bool:
        return bool(
            _get_profile_env("H20_KEYS_BASE_URL")
            and _stt_credential()
        )

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": _DEFAULT_MODEL, "display": "Whisper Large v3"}]

    def default_model(self) -> Optional[str]:
        return _DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "managed",
            "tag": "Groq STT through a scoped Human20 Keys credential",
            "env_vars": [
                {"key": "H20_KEYS_BASE_URL", "prompt": "Human20 Keys base URL"},
                {"key": "H20_KEYS_STT_API_KEY", "prompt": "Scoped Human20 STT key"},
            ],
        }

    def transcribe(
        self,
        file_path: str,
        *,
        model: Optional[str] = None,
        language: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        base_url = _get_profile_env("H20_KEYS_BASE_URL")
        credential = _stt_credential()
        url = _transcription_url(base_url)
        if url is None or not credential:
            return {
                "success": False,
                "transcript": "",
                "provider": _PROVIDER,
                "error": "H20_KEYS_BASE_URL and a Human20 STT key must be configured",
            }

        path = Path(file_path)
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        if size <= 0 or size > _MAX_AUDIO_BYTES:
            return {
                "success": False,
                "transcript": "",
                "provider": _PROVIDER,
                "error": "Audio file is missing, empty, or exceeds 25 MiB",
            }

        data = {
            "model": model or _DEFAULT_MODEL,
            "response_format": "json",
        }
        if language:
            data["language"] = language
        prompt = extra.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            data["prompt"] = prompt.strip()

        response: Optional[requests.Response] = None
        try:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            with path.open("rb") as audio_file:
                response = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {credential}"},
                    files={"file": (path.name, audio_file, mime)},
                    data=data,
                    timeout=(10, 90),
                    allow_redirects=False,
                    stream=True,
                )
                raw = _bounded_response_body(response)
            if response.status_code != 200:
                return {
                    "success": False,
                    "transcript": "",
                    "provider": _PROVIDER,
                    "error": f"Human20 Keys Groq transcription failed (HTTP {response.status_code})",
                }
            payload = json.loads(raw.decode("utf-8"))
            transcript = str(payload.get("text") or "").strip()
            if not transcript:
                raise ValueError("Human20 Keys returned an empty transcript")
            return {
                "success": True,
                "transcript": transcript,
                "provider": _PROVIDER,
            }
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, requests.RequestException):
            return {
                "success": False,
                "transcript": "",
                "provider": _PROVIDER,
                "error": "Human20 Keys Groq transcription request failed",
            }
        finally:
            if response is not None:
                response.close()
