"""Read-only telegram-chip startup reconciliation helpers.

This module deliberately does not send Telegram messages.  It compares compact
recent-message records from Chip's privileged telegram-chip reader against the
local gateway message ledger so restart/drain recovery can distinguish:

* messages the gateway never saw;
* messages received but not dispatched;
* interrupted/drained messages that require alert-only handling; and
* completed messages that should be ignored.

Runtime use is optional and disabled by default.  Phase 4 wires the resulting
classifications into active-goal startup recovery.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

TELEGRAM_CHIP_RECENT_MESSAGES_CONTRACT = (
    "read-only telegram-chip endpoint: "
    "GET /chats/{chat_id}/messages?page=1&page_size={limit}; "
    "when thread_id is known the helper also passes thread_id as a query "
    "hint and still filters records locally by chat_id + thread_id"
)

DEFAULT_TELEGRAM_CHIP_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_CHIP_USER_ID = "617744661"
DEFAULT_RECENT_LIMIT = 20


@dataclass(frozen=True)
class TelegramChipHistoryConfig:
    """Optional read-only telegram-chip client configuration."""

    enabled: bool = False
    base_url: str = DEFAULT_TELEGRAM_CHIP_BASE_URL
    ssh_host: Optional[str] = None
    timeout: float = 8.0
    limit: int = DEFAULT_RECENT_LIMIT
    chip_user_id: str = DEFAULT_CHIP_USER_ID

    @classmethod
    def from_mapping(
        cls,
        config: Optional[Mapping[str, Any]] = None,
        *,
        env: Optional[Mapping[str, str]] = None,
    ) -> "TelegramChipHistoryConfig":
        """Build config from config.yaml-shaped data plus env overrides.

        Supported config shapes are intentionally narrow and off by default:

        ``gateway.chip_history_recovery`` or ``telegram.chip_history_recovery``
        with keys ``enabled``, ``base_url``, ``ssh_host``, ``timeout``,
        ``limit`` and ``chip_user_id``.
        """

        env_map = env if env is not None else os.environ
        raw: Mapping[str, Any] = {}
        if isinstance(config, Mapping):
            gateway_raw = config.get("gateway")
            telegram_raw = config.get("telegram")
            if isinstance(gateway_raw, Mapping) and isinstance(
                gateway_raw.get("chip_history_recovery"), Mapping
            ):
                raw = gateway_raw["chip_history_recovery"]
            elif isinstance(telegram_raw, Mapping) and isinstance(
                telegram_raw.get("chip_history_recovery"), Mapping
            ):
                raw = telegram_raw["chip_history_recovery"]
            elif isinstance(config.get("chip_history_recovery"), Mapping):
                raw = config["chip_history_recovery"]

        def _env_first(name: str, key: str, default: Any = None) -> Any:
            val = env_map.get(name)
            if val is not None:
                return val
            return raw.get(key, default)

        enabled_raw = _env_first("HERMES_TELEGRAM_CHIP_HISTORY_RECOVERY", "enabled", False)
        return cls(
            enabled=_truthy(enabled_raw),
            base_url=str(_env_first("TELEGRAM_CHIP_API_URL", "base_url", DEFAULT_TELEGRAM_CHIP_BASE_URL)).rstrip("/"),
            ssh_host=_blank_to_none(_env_first("TELEGRAM_CHIP_SSH_HOST", "ssh_host", None)),
            timeout=_coerce_float(_env_first("TELEGRAM_CHIP_TIMEOUT", "timeout", 8.0), 8.0),
            limit=_coerce_int(_env_first("TELEGRAM_CHIP_HISTORY_LIMIT", "limit", DEFAULT_RECENT_LIMIT), DEFAULT_RECENT_LIMIT),
            chip_user_id=str(_env_first("TELEGRAM_CHIP_USER_ID", "chip_user_id", DEFAULT_CHIP_USER_ID)),
        )


@dataclass(frozen=True)
class TelegramChipMessage:
    """Compact read-only record returned by telegram-chip history lookup."""

    message_id: str
    sender_id: Optional[str] = None
    sender: Optional[str] = None
    timestamp: Optional[str] = None
    snippet: Optional[str] = None
    thread_id: Optional[str] = None
    chat_id: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ChipHistoryClassification:
    """One deterministic classification against the gateway ledger."""

    message: TelegramChipMessage
    status: str
    action: str
    reason: str
    ledger_status: Optional[str] = None
    ledger_id: Optional[int] = None


@dataclass(frozen=True)
class ChipHistoryReconciliationResult:
    """Result of a read-only startup history reconciliation attempt."""

    enabled: bool
    chat_id: str
    thread_id: Optional[str]
    records_checked: int = 0
    classifications: Sequence[ChipHistoryClassification] = field(default_factory=tuple)
    degraded: bool = False
    warning: Optional[str] = None
    contract: str = TELEGRAM_CHIP_RECENT_MESSAGES_CONTRACT

    @property
    def missed_by_gateway(self) -> list[ChipHistoryClassification]:
        return [c for c in self.classifications if c.status == "missed_by_gateway"]

    @property
    def requeue_candidates(self) -> list[ChipHistoryClassification]:
        return [c for c in self.classifications if c.action == "auto_requeue_candidate"]

    @property
    def alert_only(self) -> list[ChipHistoryClassification]:
        return [c for c in self.classifications if c.action == "alert_only"]


class TelegramChipHistoryClient(Protocol):
    """Read-only client surface for tests and runtime implementations."""

    def fetch_recent_messages(
        self,
        *,
        chat_id: str,
        thread_id: Optional[str] = None,
        limit: int = DEFAULT_RECENT_LIMIT,
    ) -> Sequence[TelegramChipMessage]:
        ...


class HttpTelegramChipHistoryClient:
    """Tiny urllib/subprocess client for the canonical telegram-chip API.

    If ``ssh_host`` is set, the HTTP call is executed on that host against its
    localhost API.  The command only performs GET requests and never exposes or
    copies Telethon session files.
    """

    def __init__(self, config: TelegramChipHistoryConfig):
        self.config = config

    def fetch_recent_messages(
        self,
        *,
        chat_id: str,
        thread_id: Optional[str] = None,
        limit: int = DEFAULT_RECENT_LIMIT,
    ) -> Sequence[TelegramChipMessage]:
        safe_limit = _coerce_int(limit, self.config.limit)
        safe_limit = max(1, min(safe_limit, 100))
        url = _recent_messages_url(
            self.config.base_url,
            chat_id=chat_id,
            thread_id=thread_id,
            limit=safe_limit,
        )
        if self.config.ssh_host:
            payload = _fetch_url_over_ssh(url, self.config.ssh_host, timeout=self.config.timeout)
        else:
            with urllib.request.urlopen(url, timeout=self.config.timeout) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
        return parse_telegram_chip_recent_messages(
            payload,
            fallback_chat_id=str(chat_id),
            fallback_thread_id=thread_id,
        )


class DisabledTelegramChipHistoryClient:
    """Explicit disabled client, useful for callers that want a result object."""

    def fetch_recent_messages(self, **_kwargs: Any) -> Sequence[TelegramChipMessage]:
        return ()


def build_telegram_chip_history_client(
    config: Optional[Mapping[str, Any]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[HttpTelegramChipHistoryClient]:
    """Return a read-only telegram-chip client only when explicitly enabled."""

    cfg = TelegramChipHistoryConfig.from_mapping(config, env=env)
    if not cfg.enabled:
        return None
    return HttpTelegramChipHistoryClient(cfg)


def reconcile_chip_history_against_ledger(
    db: Any,
    client: Optional[TelegramChipHistoryClient],
    *,
    chat_id: Any,
    thread_id: Any = None,
    platform: str = "telegram",
    limit: int = DEFAULT_RECENT_LIMIT,
    chip_user_id: str = DEFAULT_CHIP_USER_ID,
) -> ChipHistoryReconciliationResult:
    """Fetch recent Chip-visible messages and classify them against ledger.

    This is intentionally read-only.  It returns classifications; it does not
    requeue, resume, send messages, or mutate Telegram state.
    """

    chat_id_s = str(chat_id)
    thread_id_s = _normalize_optional_id(thread_id)
    if client is None:
        return ChipHistoryReconciliationResult(
            enabled=False,
            chat_id=chat_id_s,
            thread_id=thread_id_s,
            warning="telegram-chip history recovery disabled",
        )

    try:
        recent = client.fetch_recent_messages(
            chat_id=chat_id_s,
            thread_id=thread_id_s,
            limit=limit,
        )
    except Exception as exc:
        warning = f"telegram-chip history unavailable; using ledger-only recovery: {exc}"
        logger.warning(warning)
        return ChipHistoryReconciliationResult(
            enabled=True,
            chat_id=chat_id_s,
            thread_id=thread_id_s,
            degraded=True,
            warning=warning,
        )

    classifications: list[ChipHistoryClassification] = []
    checked = 0
    for message in recent:
        if not _same_chat_scope(message, chat_id=chat_id_s, thread_id=thread_id_s):
            continue
        if not _is_chip_sender(message, chip_user_id=chip_user_id):
            continue
        checked += 1
        ledger = None
        try:
            ledger = db.find_gateway_message_ledger(
                platform=platform,
                chat_id=chat_id_s,
                thread_id=thread_id_s,
                message_id=message.message_id,
            )
        except Exception as exc:
            logger.warning("gateway ledger lookup failed during telegram-chip reconciliation: %s", exc)
            classifications.append(
                ChipHistoryClassification(
                    message=message,
                    status="ledger_unavailable",
                    action="alert_only",
                    reason="gateway ledger lookup failed; do not replay automatically",
                )
            )
            continue
        classifications.append(classify_chip_history_message(message, ledger))

    return ChipHistoryReconciliationResult(
        enabled=True,
        chat_id=chat_id_s,
        thread_id=thread_id_s,
        records_checked=checked,
        classifications=tuple(classifications),
    )


def classify_chip_history_message(
    message: TelegramChipMessage,
    ledger: Optional[Mapping[str, Any]],
) -> ChipHistoryClassification:
    """Classify one recent telegram-chip message against a ledger row."""

    if ledger is None:
        return ChipHistoryClassification(
            message=message,
            status="missed_by_gateway",
            action="alert_only",
            reason="telegram-chip saw the Chip message but gateway ledger has no row",
        )

    status = str(ledger.get("status") or "").strip().lower()
    ledger_id = _safe_int(ledger.get("id"))
    if status == "completed":
        return ChipHistoryClassification(
            message=message,
            status="completed_ignore",
            action="ignore",
            reason="gateway ledger marks message completed",
            ledger_status=status,
            ledger_id=ledger_id,
        )
    if status == "received" and not ledger.get("dispatch_started_at"):
        return ChipHistoryClassification(
            message=message,
            status="safe_auto_requeue_candidate",
            action="auto_requeue_candidate",
            reason="gateway received message but never started dispatch",
            ledger_status=status,
            ledger_id=ledger_id,
        )
    if status == "requeued" and not ledger.get("dispatch_started_at"):
        return ChipHistoryClassification(
            message=message,
            status="already_requeued",
            action="ignore",
            reason="gateway ledger already marked message as requeued",
            ledger_status=status,
            ledger_id=ledger_id,
        )
    if status in {"in_progress", "drained", "failed"}:
        return ChipHistoryClassification(
            message=message,
            status="alert_only",
            action="alert_only",
            reason=f"gateway ledger status {status!r} may involve side effects; do not replay automatically",
            ledger_status=status,
            ledger_id=ledger_id,
        )
    return ChipHistoryClassification(
        message=message,
        status="alert_only",
        action="alert_only",
        reason=f"gateway ledger status {status or 'unknown'!r} is not safe for automatic replay",
        ledger_status=status or None,
        ledger_id=ledger_id,
    )


def parse_telegram_chip_recent_messages(
    payload: Any,
    *,
    fallback_chat_id: Optional[str] = None,
    fallback_thread_id: Optional[str] = None,
) -> list[TelegramChipMessage]:
    """Parse telegram-chip recent-message responses.

    The live API may return ``data`` as either JSON-like records or a formatted
    transcript string.  Both shapes are accepted so a parser mismatch does not
    turn into a false "no messages" conclusion.
    """

    obj = _maybe_json(payload)
    data = obj.get("data") if isinstance(obj, Mapping) and "data" in obj else obj
    if isinstance(data, str):
        nested = _maybe_json(data)
        if nested is not data:
            data = nested
    if isinstance(data, Mapping):
        if isinstance(data.get("messages"), list):
            data = data["messages"]
        elif isinstance(data.get("items"), list):
            data = data["items"]
        else:
            one = _record_from_mapping(data, fallback_chat_id=fallback_chat_id, fallback_thread_id=fallback_thread_id)
            return [one] if one else []
    if isinstance(data, list):
        messages: list[TelegramChipMessage] = []
        for item in data:
            if isinstance(item, Mapping):
                parsed = _record_from_mapping(
                    item,
                    fallback_chat_id=fallback_chat_id,
                    fallback_thread_id=fallback_thread_id,
                )
                if parsed is not None:
                    messages.append(parsed)
        return messages
    if isinstance(data, str):
        return _records_from_transcript_string(
            data,
            fallback_chat_id=fallback_chat_id,
            fallback_thread_id=fallback_thread_id,
        )
    return []


def redacted_snippet(text: Any, *, limit: int = 160) -> Optional[str]:
    if text is None:
        return None
    value = re.sub(r"\s+", " ", str(text)).strip()
    if not value:
        return None
    try:
        from agent.redact import redact_sensitive_text

        value = redact_sensitive_text(value, force=True)
    except Exception:
        value = re.sub(r"\b(?:sk|fw|ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_\-]{8,}\b", "[REDACTED]", value)
    return value[: max(1, int(limit or 160))]


def _recent_messages_url(
    base_url: str,
    *,
    chat_id: str,
    thread_id: Optional[str],
    limit: int,
) -> str:
    quoted_chat = urllib.parse.quote(str(chat_id), safe="")
    query = {"page": "1", "page_size": str(limit)}
    if thread_id:
        # The current telegram-chip endpoint is chat-scoped.  Passing thread_id
        # is a harmless hint for newer servers; local filtering below remains
        # the safety rail if the server ignores it.
        query["thread_id"] = str(thread_id)
    return f"{base_url.rstrip('/')}/chats/{quoted_chat}/messages?{urllib.parse.urlencode(query)}"


def _fetch_url_over_ssh(url: str, ssh_host: str, *, timeout: float) -> str:
    script = (
        "import urllib.request\n"
        f"url = {json.dumps(url)}\n"
        f"timeout = {float(timeout)!r}\n"
        "print(urllib.request.urlopen(url, timeout=timeout).read().decode('utf-8', errors='replace'))\n"
    )
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", ssh_host, "python3", "-c", script],
        capture_output=True,
        text=True,
        timeout=max(1.0, float(timeout) + 2.0),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"ssh exited {proc.returncode}").strip()[:500])
    return proc.stdout


def _record_from_mapping(
    item: Mapping[str, Any],
    *,
    fallback_chat_id: Optional[str],
    fallback_thread_id: Optional[str],
) -> Optional[TelegramChipMessage]:
    message_id = _first_present(item, "message_id", "id", "msg_id", "mid")
    if message_id is None:
        return None
    text = _first_present(item, "message", "text", "content", "caption", "raw_text")
    sender_id = _first_present(item, "sender_id", "user_id", "from_id", "from_user_id")
    sender = _first_present(item, "sender", "from", "from_name", "username")
    timestamp = _first_present(item, "timestamp", "date", "datetime", "created_at")
    thread_id = _first_present(item, "thread_id", "topic_id", "message_thread_id")
    chat_id = _first_present(item, "chat_id", "peer_id")
    return TelegramChipMessage(
        message_id=str(message_id),
        sender_id=str(sender_id) if sender_id is not None else None,
        sender=str(sender) if sender is not None else None,
        timestamp=str(timestamp) if timestamp is not None else None,
        snippet=redacted_snippet(text),
        thread_id=_normalize_optional_id(thread_id),
        chat_id=str(chat_id) if chat_id is not None else fallback_chat_id,
        raw=dict(item),
    )


def _records_from_transcript_string(
    transcript: str,
    *,
    fallback_chat_id: Optional[str],
    fallback_thread_id: Optional[str],
) -> list[TelegramChipMessage]:
    messages: list[TelegramChipMessage] = []
    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        if not line or "ID:" not in line:
            continue
        fields: dict[str, str] = {}
        parts = line.split(" | ")
        for part in parts:
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            fields[key.strip().lower()] = value.strip()
        message_id = fields.get("id") or fields.get("message id")
        if not message_id:
            match = re.search(r"\bID:\s*([^|\s]+)", line)
            if match:
                message_id = match.group(1).strip()
        if not message_id:
            continue
        sender = fields.get("from") or fields.get("sender")
        if not sender and len(parts) > 1 and ":" not in parts[1]:
            sender = parts[1].strip()
        sender_id = fields.get("sender_id") or fields.get("user_id") or fields.get("from_id")
        text = fields.get("message") or fields.get("text") or fields.get("caption")
        thread_id = fields.get("thread") or fields.get("thread_id") or fields.get("topic")
        if not thread_id:
            reply_match = re.search(r"\breply\s+to\s+([^|\s]+)", line, flags=re.IGNORECASE)
            if reply_match:
                thread_id = reply_match.group(1)
        messages.append(
            TelegramChipMessage(
                message_id=str(message_id),
                sender_id=str(sender_id) if sender_id else None,
                sender=str(sender) if sender else None,
                timestamp=fields.get("date") or fields.get("timestamp"),
                snippet=redacted_snippet(text),
                thread_id=_normalize_optional_id(thread_id),
                chat_id=fields.get("chat_id") or fallback_chat_id,
                raw={"line": line},
            )
        )
    return messages


def _same_chat_scope(
    message: TelegramChipMessage,
    *,
    chat_id: str,
    thread_id: Optional[str],
) -> bool:
    if message.chat_id is not None and str(message.chat_id) != str(chat_id):
        return False
    message_thread = _normalize_optional_id(message.thread_id)
    requested_thread = _normalize_optional_id(thread_id)
    return message_thread == requested_thread


def _is_chip_sender(message: TelegramChipMessage, *, chip_user_id: str) -> bool:
    if not chip_user_id:
        return True
    if message.sender_id:
        return str(message.sender_id) == str(chip_user_id)
    if message.sender:
        sender = str(message.sender).lstrip("@").lower()
        return sender in {"chipcr", "evgeny", "chip"} or "chip" in sender
    # Some telegram-chip transcript shapes omit sender.  Keep the record so a
    # parser limitation does not hide a possible missed Chip message.
    return True


def _first_present(item: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return text
    if text[0] not in "[{\"":
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def _normalize_optional_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _blank_to_none(value: Any) -> Optional[str]:
    text = _normalize_optional_id(value)
    return text


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def redacted_reconciliation_example(result: ChipHistoryReconciliationResult) -> dict[str, Any]:
    """Compact debug-safe example for reports/tests; never includes raw history."""

    examples: dict[str, Any] = {}
    for classification in result.classifications:
        if classification.status in examples:
            continue
        examples[classification.status] = {
            "message_id": classification.message.message_id,
            "thread_id": classification.message.thread_id,
            "status": classification.status,
            "action": classification.action,
            "ledger_status": classification.ledger_status,
            "snippet": classification.message.snippet,
        }
    return examples
