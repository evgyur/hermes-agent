#!/usr/bin/env python3
"""Resolve webinar sources from Telegram message JSON without losing hidden URLs.

The input shape intentionally accepts both telegram-chip's formatted message and a
raw Telethon-like serialization. Private URL query strings are written only to an
optional mode-0600 handoff; the durable receipt is safe to retain.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
JOIN_WORDS = (
    "подключ",
    "эфир",
    "смотреть",
    "войти",
    "join",
    "watch",
    "live",
    "webinar",
    "room",
)


class SourceResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    url: str
    source_type: str
    label: str = ""
    ordinal: int = 0


@dataclass(frozen=True)
class RedirectHop:
    status: int
    domain: str
    redacted_url: str


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redact_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _utf16_slice(text: str, offset: int, length: int) -> str:
    data = text.encode("utf-16-le")
    start = max(0, offset) * 2
    end = max(0, offset + length) * 2
    return data[start:end].decode("utf-16-le", errors="ignore")


def _iter_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _iter_mappings(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            yield from _iter_mappings(nested)


def extract_candidates(message: Mapping[str, Any]) -> list[Candidate]:
    """Extract plain, entity, text-URL, and inline-button links deterministically."""
    text = str(message.get("text") or message.get("message") or "")
    found: list[Candidate] = []
    ordinal = 0

    for entity in message.get("entities") or []:
        if not isinstance(entity, Mapping):
            continue
        kind = str(entity.get("type") or entity.get("_") or type(entity).__name__)
        url = entity.get("url")
        label = str(entity.get("text") or "")
        if not label and entity.get("offset") is not None and entity.get("length") is not None:
            label = _utf16_slice(text, int(entity["offset"]), int(entity["length"]))
        if "TextUrl" in kind and url:
            found.append(Candidate(str(url), "entity_text_url", label, ordinal))
            ordinal += 1
        elif kind.endswith("Url") or kind == "MessageEntityUrl":
            entity_url = str(url or label)
            if URL_RE.fullmatch(entity_url.strip()):
                found.append(Candidate(entity_url.strip(), "entity_url", label, ordinal))
                ordinal += 1

    button_roots = []
    for key in ("buttons", "inline_buttons", "reply_markup", "inline_keyboard"):
        if message.get(key) is not None:
            button_roots.append(message[key])
    for root in button_roots:
        for item in _iter_mappings(root):
            kind = str(item.get("type") or item.get("_") or "")
            url = item.get("url")
            if not url:
                continue
            if kind and "url" not in kind.lower() and "button" not in kind.lower():
                continue
            label = str(item.get("text") or item.get("label") or "")
            found.append(Candidate(str(url), "inline_button", label, ordinal))
            ordinal += 1

    occupied = {candidate.url for candidate in found}
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,);]")
        if url not in occupied:
            found.append(Candidate(url, "plain_text", url, ordinal))
            occupied.add(url)
            ordinal += 1

    deduped: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for candidate in found:
        key = (candidate.url, candidate.source_type)
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _public_host_addresses(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SourceResolutionError(f"cannot resolve source host {host!r}: {exc}") from exc
    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses:
        raise SourceResolutionError(f"source host {host!r} has no addresses")
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if not ip.is_global:
            raise SourceResolutionError(f"source host {host!r} resolves to non-public address")
    return addresses


def validate_public_http_url(value: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise SourceResolutionError("only http/https webinar sources are allowed")
    if not parsed.hostname or parsed.username or parsed.password:
        raise SourceResolutionError("source URL must have a public host and no embedded credentials")
    _public_host_addresses(parsed.hostname)
    return parsed


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, ANN001
        return None


def resolve_redirect_chain(url: str, *, timeout: float = 12.0, max_hops: int = 8) -> tuple[str, list[RedirectHop]]:
    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    hops: list[RedirectHop] = []
    for _ in range(max_hops + 1):
        parsed = validate_public_http_url(current)
        request = urllib.request.Request(
            current,
            headers={"User-Agent": "Human20MeetingResolver/1.0"},
            method="GET",
        )
        try:
            response = opener.open(request, timeout=timeout)
            status = int(getattr(response, "status", response.getcode()))
            location = response.headers.get("Location")
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            location = exc.headers.get("Location")
            exc.close()
        hops.append(RedirectHop(status, parsed.hostname or "", _redact_url(current)))
        if status not in {301, 302, 303, 307, 308} or not location:
            return current, hops
        current = urllib.parse.urljoin(current, location)
    raise SourceResolutionError(f"redirect chain exceeded {max_hops} hops")


def _candidate_score(candidate: Candidate) -> tuple[int, int, int]:
    kind_score = {"inline_button": 40, "entity_text_url": 30, "entity_url": 20, "plain_text": 10}.get(
        candidate.source_type, 0
    )
    label = f"{candidate.label} {candidate.url}".lower()
    word_score = sum(3 for word in JOIN_WORDS if word in label)
    return kind_score + word_score, -candidate.ordinal, -len(candidate.url)


def select_candidate(candidates: Sequence[Candidate]) -> Candidate:
    if not candidates:
        raise SourceResolutionError("Telegram message contains no resolvable URL candidate")
    return max(candidates, key=_candidate_score)


def _atomic_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def build_receipts(
    message: Mapping[str, Any],
    candidates: Sequence[Candidate],
    selected: Candidate,
    final_url: str,
    hops: Sequence[RedirectHop],
) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed = urllib.parse.urlsplit(final_url)
    now = datetime.now(timezone.utc).isoformat()
    source_text = str(message.get("text") or message.get("message") or "")
    safe = {
        "schema": 1,
        "kind": "telegram_webinar_source_receipt",
        "resolved_at": now,
        "source": {
            "chat_id": message.get("chat_id"),
            "message_id": message.get("id") or message.get("message_id"),
            "date": message.get("date"),
            "sender_id": message.get("sender_id"),
            "text_sha256": _sha256_text(source_text),
        },
        "selection": {
            "source_type": selected.source_type,
            "label": selected.label,
            "source_url_redacted": _redact_url(selected.url),
            "source_url_sha256": _sha256_text(selected.url),
            "final_domain": parsed.hostname,
            "final_url_redacted": _redact_url(final_url),
            "final_url_sha256": _sha256_text(final_url),
            "redirect_chain": [asdict(hop) for hop in hops],
        },
        "candidate_count": len(candidates),
    }
    private = {
        **safe,
        "private": {
            "selected_source_url": selected.url,
            "final_url": final_url,
            "candidates": [asdict(candidate) for candidate in candidates],
        },
    }
    return safe, private


def _load_message(args: argparse.Namespace) -> dict[str, Any]:
    if args.input_json == "-":
        payload = json.load(sys.stdin)
    else:
        payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    if isinstance(payload, Mapping) and payload.get("success") is True and "data" in payload:
        payload = payload["data"]
        if isinstance(payload, str):
            payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise SourceResolutionError("input JSON must contain one Telegram message object")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", required=True, help="Message JSON path, or - for stdin")
    parser.add_argument("--receipt-output", required=True)
    parser.add_argument("--private-output")
    parser.add_argument("--no-network", action="store_true", help="Select a candidate without following redirects")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args(argv)
    try:
        message = _load_message(args)
        candidates = extract_candidates(message)
        selected = select_candidate(candidates)
        if args.no_network:
            final_url, hops = selected.url, []
        else:
            final_url, hops = resolve_redirect_chain(selected.url, timeout=args.timeout)
        safe, private = build_receipts(message, candidates, selected, final_url, hops)
        _atomic_private_json(Path(args.receipt_output), safe)
        if args.private_output:
            _atomic_private_json(Path(args.private_output), private)
        print(json.dumps({"status": "SOURCE_RESOLVED", "receipt": args.receipt_output, "final_domain": safe["selection"]["final_domain"]}))
        return 0
    except (OSError, ValueError, SourceResolutionError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "SOURCE_BLOCKED", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
