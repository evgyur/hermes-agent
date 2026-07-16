"""Privacy-safe operational metadata for the Human20 Hermes gateway."""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
from typing import Any


LOG_HMAC_KEY_ENV = "HUMAN20_HERMES_LOG_HMAC_KEY"
_HMAC_DOMAIN = "human20-hermes:v1:"
_SAFE_EVENT_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")


class _SafeLogFields(dict[str, Any]):
    """Mapping that renders as stable key=value operational telemetry."""

    def __str__(self) -> str:
        return " ".join(f"{name}={value}" for name, value in self.items())


def safe_log_event(
    event: str,
    *,
    actor: object | None = None,
    text: object | None = None,
    body: object | None = None,
    status: object | None = None,
    error_class: object | None = None,
    http_status: object | None = None,
    duration: object | None = None,
) -> dict[str, Any]:
    """Return allowlisted metadata and an optional keyed-HMAC subject.

    ``text`` and ``body`` are deliberate privacy sinks: callers may hand raw
    provider or message values to this boundary, but those values are never
    copied into the result.  Identity is emitted only as a per-service HMAC
    when ``HUMAN20_HERMES_LOG_HMAC_KEY`` is non-blank; otherwise it is omitted.
    """

    del text, body
    result: dict[str, Any] = _SafeLogFields()

    if type(event) is str and _SAFE_EVENT_RE.fullmatch(event):
        result["event"] = event
    for name, value in (("status", status), ("error_class", error_class)):
        if type(value) is str and _SAFE_TOKEN_RE.fullmatch(value):
            result[name] = value
    if type(http_status) is int and 100 <= http_status <= 599:
        result["http_status"] = http_status
    if type(duration) in {int, float} and math.isfinite(duration):
        result["duration"] = duration

    key = os.getenv(LOG_HMAC_KEY_ENV, "").strip()
    if type(actor) is str:
        raw_actor = actor.strip()
    elif type(actor) is int and not isinstance(actor, bool):
        raw_actor = str(actor)
    else:
        raw_actor = ""
    if key and raw_actor:
        digest = hmac.new(
            key.encode("utf-8"),
            f"{_HMAC_DOMAIN}{raw_actor}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        result["subject"] = f"hmac256:{digest}"

    return result
