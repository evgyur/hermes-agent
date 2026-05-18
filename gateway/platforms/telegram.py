"""
Telegram platform adapter.

Uses python-telegram-bot library for:
- Receiving messages from users/groups
- Sending responses back
- Handling media and commands
"""

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import html as _html
import re
import time
from collections import deque
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

try:
    from telegram import Update, Bot, Message, InlineKeyboardButton, InlineKeyboardMarkup
    try:
        from telegram import LinkPreviewOptions
    except ImportError:
        LinkPreviewOptions = None
    from telegram.ext import (
        Application,
        CommandHandler,
        CallbackQueryHandler,
        MessageHandler as TelegramMessageHandler,
        BusinessConnectionHandler,
        BusinessMessagesDeletedHandler,
        ContextTypes,
        filters,
    )
    from telegram.constants import ParseMode, ChatType
    from telegram.request import HTTPXRequest
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = Any
    Bot = Any
    Message = Any
    InlineKeyboardButton = Any
    InlineKeyboardMarkup = Any
    LinkPreviewOptions = None
    Application = Any
    CommandHandler = Any
    CallbackQueryHandler = Any
    TelegramMessageHandler = Any
    BusinessConnectionHandler = None
    BusinessMessagesDeletedHandler = None
    HTTPXRequest = Any
    filters = None
    ParseMode = None
    ChatType = None

    # Mock ContextTypes so type annotations using ContextTypes.DEFAULT_TYPE
    # don't crash during class definition when the library isn't installed.
    class _MockContextTypes:
        DEFAULT_TYPE = Any
    ContextTypes = _MockContextTypes

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
    cache_image_from_bytes,
    cache_audio_from_bytes,
    cache_video_from_bytes,
    cache_document_from_bytes,
    resolve_proxy_url,
    SUPPORTED_VIDEO_TYPES,
    SUPPORTED_DOCUMENT_TYPES,
    TEXT_DOCUMENT_EXTENSIONS,
    utf16_len,
    _prefix_within_utf16_limit,
)
from gateway.platforms.telegram_network import (
    TelegramFallbackTransport,
    discover_fallback_ips,
    parse_fallback_ip_env,
)
from utils import atomic_replace

_TELEGRAM_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_TELEGRAM_IMAGE_MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_TELEGRAM_IMAGE_EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def check_telegram_requirements() -> bool:
    """Check if Telegram dependencies are available."""
    return TELEGRAM_AVAILABLE


# Matches every character that MarkdownV2 requires to be backslash-escaped
# when it appears outside a code span or fenced code block.
_MDV2_ESCAPE_RE = re.compile(r'([_*\[\]()~`>#\+\-=|{}.!\\])')


def _escape_mdv2(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters with a preceding backslash."""
    return _MDV2_ESCAPE_RE.sub(r'\\\1', text)


def _strip_mdv2(text: str) -> str:
    """Strip MarkdownV2 escape backslashes to produce clean plain text.

    Also removes MarkdownV2 formatting markers so the fallback
    doesn't show stray syntax characters from format_message conversion.
    """
    # Remove escape backslashes before special characters
    cleaned = re.sub(r'\\([_*\[\]()~`>#\+\-=|{}.!\\])', r'\1', text)
    # Remove MarkdownV2 bold markers that format_message converted from **bold**
    cleaned = re.sub(r'\*([^*]+)\*', r'\1', cleaned)
    # Remove MarkdownV2 italic markers that format_message converted from *italic*
    # Use word boundary (\b) to avoid breaking snake_case like my_variable_name
    cleaned = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', cleaned)
    # Remove MarkdownV2 strikethrough markers (~text~ → text)
    cleaned = re.sub(r'~([^~]+)~', r'\1', cleaned)
    # Remove MarkdownV2 spoiler markers (||text|| → text)
    cleaned = re.sub(r'\|\|([^|]+)\|\|', r'\1', cleaned)
    return cleaned


_INLINE_TG_PREVIEW_BLOCKER = (
    "превью заблокировано: Hermes-бот попытался отправить TG-пост inline. "
    "Нужный путь — telegram-chip / ChipCR с exact-message verify-gate."
)
_CHIP_TG_PREVIEW_GUARD_CHAT_IDS = {"-1003437858232", "-1003712304136"}
_TG_PREVIEW_PENDING_ECHO_FILE = "/tmp/tg_preview_echo_pending.jsonl"
_HUMAN20_CTA_TEXT = "перейти в @human20"
_HUMAN20_CTA_URL = "https://t.me/human20"
_INLINE_TG_OPERATOR_PREFIX_RE = re.compile(
    r"(?i)^\s*(готово|сделал|отправил|принял|да|нет|ок|ошибка|блокер|превью\s+не|smoke|proof)\b"
)


def _looks_like_inline_tg_preview(text: str) -> bool:
    """Return True for finished Telegram-post drafts that must not be bot-sent.

    In Chip's `/tg` chats, any finished TG preview or edited post must be
    delivered by the human account (ChipCR via telegram-chip), not by the
    Hermes bot. This detector intentionally catches both HTML captions and
    plain edited TG drafts while avoiding compact operator reports.
    """
    if not text or not text.strip():
        return False
    stripped = text.strip()
    if _INLINE_TG_OPERATOR_PREFIX_RE.search(stripped):
        return False
    if "➊" in stripped or "┈" in stripped:
        return False

    first_line = stripped.splitlines()[0].strip()
    has_html_heading = first_line.startswith("<b>") and "</b>" in first_line
    has_tg_block_separator = "⠀" in stripped  # U+2800 braille blank used by tg posts
    has_source_label = bool(re.search(r"(?im)^\s*(источник|source)\s*:", stripped))
    has_markdown_source = bool(re.search(r"(?im)^\s*(источник|source)\s*:\s*\[[^\]]+\]\(https?://", stripped))
    has_post_body = len([ln for ln in stripped.splitlines() if ln.strip()]) >= 4
    if has_html_heading and has_post_body and (has_tg_block_separator or has_source_label):
        return True

    # Follow-up edits often produce a plain Telegram draft, not raw HTML:
    #   Title
    #   ⠀
    #   body...
    #   ⠀
    #   Источник: [name](url)
    # Those are still finished `/tg` artifacts and must be routed through
    # ChipCR, never emitted by the Hermes bot as a final response.
    short_title = 3 <= len(first_line) <= 120 and not first_line.startswith(("/", "#"))
    has_multiple_tg_blocks = has_tg_block_separator and has_post_body
    return bool(short_title and has_multiple_tg_blocks and (has_source_label or has_markdown_source))


def _tg_preview_visible_text(text: str) -> str:
    """Normalize a TG preview caption/body for echo fingerprinting.

    `send-preview.sh` writes HTML, but Telegram delivers the same ChipCR
    message back to the bot as visible plain text plus entities. Hash the
    visible form so the pre-send pending marker and incoming update match.
    """
    if not text:
        return ""
    visible = re.sub(r"<[^>]+>", "", text)
    visible = _html.unescape(visible)
    return visible.strip()


def _tg_preview_echo_fingerprint(text: str) -> str:
    return hashlib.sha256(_tg_preview_visible_text(text).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Markdown table → Telegram-friendly row groups
# ---------------------------------------------------------------------------
# Telegram's MarkdownV2 has no table syntax — '|' is just an escaped literal,
# so pipe tables render as noisy backslash-pipe text with no alignment.
# Reformating each row into a bold heading plus bullet list keeps the content
# readable on mobile clients while preserving the source data.

# Matches a GFM table delimiter row: optional outer pipes, cells containing
# only dashes (with optional leading/trailing colons for alignment) separated
# by '|'.  Requires at least one internal '|' so lone '---' horizontal rules
# are NOT matched.
_TABLE_SEPARATOR_RE = re.compile(
    r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$'
)


def _is_table_row(line: str) -> bool:
    """Return True if *line* could plausibly be a table data row."""
    stripped = line.strip()
    return bool(stripped) and '|' in stripped


def _split_markdown_table_row(line: str) -> list[str]:
    """Split a simple GFM table row into stripped cell values."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _render_table_block_for_telegram(table_block: list[str]) -> str:
    """Render a detected GFM table as Telegram-friendly row groups."""
    if len(table_block) < 3:
        return "\n".join(table_block)

    headers = _split_markdown_table_row(table_block[0])
    if len(headers) < 2:
        return "\n".join(table_block)

    # Detect row-label column: present when data rows have one more cell
    # than the header row (the row-label column carries no header).
    first_data_row = _split_markdown_table_row(table_block[2]) if len(table_block) > 2 else []
    has_row_label_col = len(first_data_row) == len(headers) + 1

    rendered_rows: list[str] = []
    for index, row in enumerate(table_block[2:], start=1):
        cells = _split_markdown_table_row(row)
        if has_row_label_col:
            # First cell is the row-label (heading); remaining cells align with headers.
            heading = cells[0] if cells and cells[0] else f"Row {index}"
            data_cells = cells[1:]
        else:
            # No row-label column: use first non-empty cell as heading.
            heading = next((cell for cell in cells if cell), f"Row {index}")
            data_cells = cells

        # Pad or trim data_cells to match headers length.
        if len(data_cells) < len(headers):
            data_cells.extend([""] * (len(headers) - len(data_cells)))
        elif len(data_cells) > len(headers):
            data_cells = data_cells[: len(headers)]

        rendered_rows.append(f"**{heading}**")
        rendered_rows.extend(
            f"• {header}: {value}" for header, value in zip(headers, data_cells)
        )

    return "\n\n".join(rendered_rows)


def _wrap_markdown_tables(text: str) -> str:
    """Rewrite GFM-style pipe tables into Telegram-friendly bullet groups.

    Detected by a row containing '|' immediately followed by a delimiter
    row matching :data:`_TABLE_SEPARATOR_RE`.  Subsequent pipe-containing
    non-blank lines are consumed as the table body and rewritten as
    per-row bullet groups. Tables inside existing fenced code blocks are left
    alone.
    """
    if '|' not in text or '-' not in text:
        return text

    lines = text.split('\n')
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        # Track existing fenced code blocks — never touch content inside.
        if stripped.startswith('```'):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        # Look for a header row (contains '|') immediately followed by a
        # delimiter row.
        if (
            '|' in line
            and i + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[i + 1])
        ):
            table_block = [line, lines[i + 1]]
            j = i + 2
            while j < len(lines) and _is_table_row(lines[j]):
                table_block.append(lines[j])
                j += 1
            out.append(_render_table_block_for_telegram(table_block))
            i = j
            continue

        out.append(line)
        i += 1

    return '\n'.join(out)


class TelegramAdapter(BasePlatformAdapter):
    """
    Telegram bot adapter.

    Handles:
    - Receiving messages from users and groups
    - Sending responses with Telegram markdown
    - Forum topics (thread_id support)
    - Media messages
    """

    # Telegram message limits
    MAX_MESSAGE_LENGTH = 4096
    # Threshold for detecting Telegram client-side message splits.
    # When a chunk is near this limit, a continuation is almost certain.
    _SPLIT_THRESHOLD = 4000
    MEDIA_GROUP_WAIT_SECONDS = 0.8
    _GENERAL_TOPIC_THREAD_ID = "1"

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.TELEGRAM)
        self._app: Optional[Application] = None
        self._bot: Optional[Bot] = None
        self._webhook_mode: bool = False
        self._mention_patterns = self._compile_mention_patterns()
        self._reply_to_mode: str = getattr(config, 'reply_to_mode', 'first') or 'first'
        self._disable_link_previews: bool = self._coerce_bool_extra("disable_link_previews", False)
        # Buffer rapid/album photo updates so Telegram image bursts are handled
        # as a single MessageEvent instead of self-interrupting multiple turns.
        self._media_batch_delay_seconds = float(os.getenv("HERMES_TELEGRAM_MEDIA_BATCH_DELAY_SECONDS", "0.8"))
        self._pending_photo_batches: Dict[str, MessageEvent] = {}
        self._pending_photo_batch_tasks: Dict[str, asyncio.Task] = {}
        self._media_group_events: Dict[str, MessageEvent] = {}
        self._media_group_tasks: Dict[str, asyncio.Task] = {}
        # Buffer rapid text messages so Telegram client-side splits of long
        # messages are aggregated into a single MessageEvent.
        self._text_batch_delay_seconds = float(os.getenv("HERMES_TELEGRAM_TEXT_BATCH_DELAY_SECONDS", "0.6"))
        self._text_batch_split_delay_seconds = float(os.getenv("HERMES_TELEGRAM_TEXT_BATCH_SPLIT_DELAY_SECONDS", "2.0"))
        self._pending_text_batches: Dict[str, MessageEvent] = {}
        self._pending_text_batch_tasks: Dict[str, asyncio.Task] = {}
        self._polling_error_task: Optional[asyncio.Task] = None
        self._polling_conflict_count: int = 0
        self._polling_network_error_count: int = 0
        self._polling_error_callback_ref = None
        # DM Topics: map of topic_name -> message_thread_id (populated at startup)
        self._dm_topics: Dict[str, int] = {}
        # DM Topics config from extra.dm_topics
        self._dm_topics_config: List[Dict[str, Any]] = self.config.extra.get("dm_topics", [])
        # Auto skill routes: declarative per-chat skill modes (e.g. route URLs/media to /tg).
        self._auto_skill_routes: List[Dict[str, Any]] = self._load_auto_skill_routes()
        # Inline preview guard: fail closed when a configured chat would receive
        # a finished `/tg` preview from the Hermes bot instead of the required
        # human-account sender path.
        self._inline_preview_guard: Dict[str, Any] = self._load_inline_preview_guard()
        # Interactive model picker state per chat
        self._model_picker_state: Dict[str, dict] = {}
        # Approval button state: message_id → session_key
        self._approval_state: Dict[int, str] = {}
        # Slash-confirm button state: confirm_id → session_key (for /reload-mcp
        # and any other slash-confirm prompts; see GatewayRunner._request_slash_confirm).
        self._slash_confirm_state: Dict[str, str] = {}
        # Notification mode for message sends.
        # "important" — only final responses, approvals, and slash confirmations
        #               trigger notifications; tool progress, streaming, status
        #               messages are delivered silently via disable_notification.
        #               This is the default — Telegram users found per-tool-call
        #               push notifications too noisy.
        # "all"       — every message triggers a push notification (legacy
        #               behavior; opt-in via display.platforms.telegram.notifications).
        self._notifications_mode: str = "important"
        # Cache of observed Telegram chat types so outbound replies can add
        # DM-only affordances without extra Bot API lookups on every send.
        self._chat_type_cache: Dict[str, str] = {}
        # Ephemeral, in-memory cache of recent user-visible Telegram messages
        # keyed by chat/topic lane. Telegram Bot API cannot fetch arbitrary
        # history, so we keep the updates the gateway has actually observed and
        # inject a compact per-turn snapshot before the model asks avoidable
        # clarifying questions. This is never persisted to memory/transcripts.
        self._recent_visible_messages: Dict[str, deque[dict[str, str]]] = {}
        self._recent_visible_limit: int = int(os.getenv("HERMES_TELEGRAM_RECENT_CONTEXT_LIMIT", "20"))
        self._recent_visible_store_limit: int = max(20, self._recent_visible_limit * 2)

    def _recent_context_key_for_source(self, source) -> str:
        """Return an isolation key for recent visible Telegram context."""
        chat_id = str(getattr(source, "chat_id", "") or "")
        thread_id = getattr(source, "thread_id", None)
        if thread_id is not None:
            return f"{chat_id}:thread:{thread_id}"
        return chat_id

    @staticmethod
    def _recent_context_snippet(text: str, *, limit: int = 240) -> str:
        snippet = " ".join(str(text or "").split())
        if len(snippet) <= limit:
            return snippet
        return snippet[: limit - 1].rstrip() + "…"

    def _format_recent_visible_context(self, event: MessageEvent) -> Optional[str]:
        """Format recent same-chat/topic Telegram messages for prompt injection."""
        key = self._recent_context_key_for_source(event.source)
        recent = list(self._recent_visible_messages.get(key) or [])
        if not recent:
            return None
        limit = max(1, self._recent_visible_limit)
        lines = [
            "## Recent visible Telegram context",
            "",
            "Same chat/topic, newest last. Ephemeral: do not save this to memory. Use it before asking clarifying questions.",
        ]
        for item in recent[-limit:]:
            msg_id = item.get("message_id") or "?"
            sender = item.get("sender") or "Telegram user"
            text = item.get("text") or ""
            if not text:
                continue
            lines.append(f"- ID {msg_id} | {sender}: {text}")
        return "\n".join(lines) if len(lines) > 3 else None

    def _attach_recent_visible_context(self, event: MessageEvent) -> None:
        event.recent_context = self._format_recent_visible_context(event)

    def _record_recent_visible_message(self, event: MessageEvent) -> None:
        """Record the incoming Telegram message for later same-lane context."""
        key = self._recent_context_key_for_source(event.source)
        if not key:
            return
        raw = getattr(event, "raw_message", None)
        visible_text = (
            getattr(raw, "text", None)
            or getattr(raw, "caption", None)
            or event.text
            or ""
        )
        if not visible_text:
            return
        source = event.source
        sender = getattr(source, "user_name", None) or getattr(source, "user_id", None) or "Telegram user"
        entry = {
            "message_id": str(getattr(event, "message_id", "") or "?"),
            "sender": str(sender),
            "text": self._recent_context_snippet(visible_text),
        }
        bucket = self._recent_visible_messages.get(key)
        if bucket is None:
            bucket = deque(maxlen=self._recent_visible_store_limit)
            self._recent_visible_messages[key] = bucket
        bucket.append(entry)

    async def handle_message(self, event: MessageEvent) -> None:
        """Attach same-chat/topic recent context before normal dispatch."""
        self._attach_recent_visible_context(event)
        self._record_recent_visible_message(event)
        await super().handle_message(event)

    def _notification_kwargs(
        self, metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Return disable_notification kwargs when the adapter is in silent mode.

        In "important" mode, all message sends are silently delivered
        (disable_notification=True) unless the caller explicitly requests a
        notification by setting ``metadata["notify"] = True``.
        """
        if getattr(self, "_notifications_mode", "important") != "important":
            return {}
        if (metadata or {}).get("notify"):
            return {}
        return {"disable_notification": True}

    def _is_callback_user_authorized(
        self,
        user_id: str,
        *,
        chat_id: Optional[str] = None,
        chat_type: Optional[str] = None,
        thread_id: Optional[str] = None,
        user_name: Optional[str] = None,
    ) -> bool:
        """Return whether a Telegram inline-button caller may perform gated actions."""
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return False

        runner = getattr(getattr(self, "_message_handler", None), "__self__", None)
        auth_fn = getattr(runner, "_is_user_authorized", None)
        if callable(auth_fn):
            try:
                from gateway.session import SessionSource

                normalized_chat_type = str(chat_type or "dm").strip().lower() or "dm"
                if normalized_chat_type == "private":
                    normalized_chat_type = "dm"
                elif normalized_chat_type == "supergroup":
                    normalized_chat_type = "forum" if thread_id is not None else "group"

                source = SessionSource(
                    platform=Platform.TELEGRAM,
                    chat_id=str(chat_id or normalized_user_id),
                    chat_type=normalized_chat_type,
                    user_id=normalized_user_id,
                    user_name=str(user_name).strip() if user_name else None,
                    thread_id=str(thread_id) if thread_id is not None else None,
                )
                return bool(auth_fn(source))
            except Exception:
                logger.debug(
                    "[Telegram] Falling back to env-only callback auth for user %s",
                    normalized_user_id,
                    exc_info=True,
                )

        allowed_csv = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
        if not allowed_csv:
            return True
        allowed_ids = {uid.strip() for uid in allowed_csv.split(",") if uid.strip()}
        return "*" in allowed_ids or normalized_user_id in allowed_ids

    @classmethod
    def _metadata_thread_id(cls, metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        if not metadata:
            return None
        thread_id = metadata.get("thread_id") or metadata.get("message_thread_id")
        return str(thread_id) if thread_id is not None else None

    @classmethod
    def _business_connection_id_from_metadata(cls, metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        if not metadata:
            return None
        value = metadata.get("business_connection_id") or metadata.get("telegram_business_connection_id")
        return str(value) if value else None

    @classmethod
    def _business_kwargs(cls, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        business_connection_id = cls._business_connection_id_from_metadata(metadata)
        return {"business_connection_id": business_connection_id} if business_connection_id else {}

    @classmethod
    def _metadata_direct_messages_topic_id(cls, metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        if not metadata:
            return None
        topic_id = metadata.get("direct_messages_topic_id") or metadata.get("telegram_direct_messages_topic_id")
        return str(topic_id) if topic_id is not None else None

    @classmethod
    def _metadata_reply_to_message_id(cls, metadata: Optional[Dict[str, Any]]) -> Optional[int]:
        if not metadata:
            return None
        reply_to = metadata.get("telegram_reply_to_message_id")
        return int(reply_to) if reply_to is not None else None

    @classmethod
    def _reply_to_message_id_for_send(
        cls,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        if reply_to:
            return int(reply_to)
        if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
            return cls._metadata_reply_to_message_id(metadata)
        return None

    @classmethod
    def _thread_kwargs_for_send(
        cls,
        chat_id: str,
        thread_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Return Telegram send kwargs for forum and direct-message topic routing.

        Supergroup/forum topics use ``message_thread_id``. True Bot API Direct
        Messages topics can opt in with explicit ``direct_messages_topic_id``
        metadata. Hermes-created private-chat topic lanes are marked with
        ``telegram_dm_topic_reply_fallback`` and must send the private topic
        thread id together with a reply anchor. Live testing showed that either
        parameter alone can render outside the visible lane.
        """
        if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
            if reply_to_message_id is None:
                reply_to_message_id = cls._metadata_reply_to_message_id(metadata)
            if reply_to_message_id is None:
                return {}
            return {"message_thread_id": cls._message_thread_id_for_send(thread_id)}
        direct_topic_id = cls._metadata_direct_messages_topic_id(metadata)
        if direct_topic_id is not None:
            return {
                "message_thread_id": None,
                "direct_messages_topic_id": int(direct_topic_id),
            }
        return {"message_thread_id": cls._message_thread_id_for_send(thread_id)}

    @classmethod
    def _message_thread_id_for_send(cls, thread_id: Optional[str]) -> Optional[int]:
        if not thread_id or str(thread_id) == cls._GENERAL_TOPIC_THREAD_ID:
            return None
        return int(thread_id)

    @classmethod
    def _message_thread_id_for_typing(cls, thread_id: Optional[str]) -> Optional[int]:
        # Asymmetric with _message_thread_id_for_send on purpose. Telegram's
        # sendMessage and sendChatAction treat thread id "1" (the forum General
        # topic) differently: sends reject message_thread_id=1 and must omit it,
        # but sendChatAction needs message_thread_id=1 to place the typing
        # bubble in the General topic (omitting it hides the bubble entirely
        # from the client's view of that topic). Preserve the real id here —
        # sends still map "1" → None via _message_thread_id_for_send.
        if not thread_id:
            return None
        return int(thread_id)

    @staticmethod
    def _is_thread_not_found_error(error: Exception) -> bool:
        return "thread not found" in str(error).lower()

    @staticmethod
    def _is_bad_request_error(error: Exception) -> bool:
        name = error.__class__.__name__.lower()
        if name == "badrequest" or name.endswith("badrequest"):
            return True
        try:
            from telegram.error import BadRequest
            return isinstance(error, BadRequest)
        except ImportError:
            return False

    @classmethod
    def _should_retry_without_dm_topic_reply_anchor(
        cls,
        error: Exception,
        metadata: Optional[Dict[str, Any]],
        reply_to_message_id: Optional[int],
    ) -> bool:
        return (
            bool(metadata and metadata.get("telegram_dm_topic_reply_fallback"))
            and reply_to_message_id is not None
            and cls._is_bad_request_error(error)
            and "message to be replied not found" in str(error).lower()
        )

    async def _send_with_dm_topic_reply_anchor_retry(
        self,
        send_fn: Any,
        send_kwargs: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
        reply_to_message_id: Optional[int],
        media_label: str,
        reset_media: Optional[Any] = None,
    ) -> Any:
        """Retry stale private-topic media replies once without the topic anchor."""
        try:
            return await send_fn(**send_kwargs)
        except Exception as send_err:
            if not self._should_retry_without_dm_topic_reply_anchor(
                send_err,
                metadata,
                reply_to_message_id,
            ):
                raise
            logger.warning(
                "[%s] Reply target deleted for Telegram %s, "
                "retrying without reply/topic anchor: %s",
                self.name,
                media_label,
                send_err,
            )
            if reset_media is not None:
                reset_media()
            retry_kwargs = dict(send_kwargs)
            retry_kwargs["reply_to_message_id"] = None
            retry_kwargs.pop("message_thread_id", None)
            retry_kwargs.pop("direct_messages_topic_id", None)
            return await send_fn(**retry_kwargs)

    def _fallback_ips(self) -> list[str]:
        """Return validated fallback IPs from config (populated by _apply_env_overrides)."""
        configured = self.config.extra.get("fallback_ips", []) if getattr(self.config, "extra", None) else []
        if isinstance(configured, str):
            configured = configured.split(",")
        return parse_fallback_ip_env(",".join(str(v) for v in configured) if configured else None)

    @staticmethod
    def _looks_like_polling_conflict(error: Exception) -> bool:
        text = str(error).lower()
        return (
            error.__class__.__name__.lower() == "conflict"
            or "terminated by other getupdates request" in text
            or "another bot instance is running" in text
        )

    @staticmethod
    def _looks_like_network_error(error: Exception) -> bool:
        """Return True for transient network errors that warrant a reconnect attempt."""
        name = error.__class__.__name__.lower()
        if name in ("networkerror", "timedout", "connectionerror"):
            return True
        try:
            from telegram.error import NetworkError, TimedOut
            if isinstance(error, (NetworkError, TimedOut)):
                return True
        except ImportError:
            pass
        return isinstance(error, OSError)

    def _coerce_bool_extra(self, key: str, default: bool = False) -> bool:
        value = self.config.extra.get(key) if getattr(self.config, "extra", None) else None
        if value is None:
            return default
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off"):
                return False
            return default
        return bool(value)

    def _link_preview_kwargs(self) -> Dict[str, Any]:
        if not getattr(self, "_disable_link_previews", False):
            return {}
        if LinkPreviewOptions is not None:
            return {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
        return {"disable_web_page_preview": True}

    async def _drain_polling_connections(self) -> None:
        """Reset the httpx connection pool used for getUpdates polling.

        Network errors (especially through proxies like sing-box) can leave
        httpx connections in a half-closed state that still occupy pool slots.
        After enough reconnect cycles the pool fills up entirely, causing
        ``Pool timeout: All connections in the connection pool are occupied.``

        We reset ONLY ``_request[0]`` (the getUpdates request) — the general
        request (``_request[1]``) is left untouched so concurrent
        ``send_message`` / ``edit_message`` calls are never interrupted.

        Implementation note: accesses ``Bot._request[0]`` which is the
        get-updates ``BaseRequest`` in the PTB 22.x internal tuple
        ``(get_updates_request, general_request)``.  There is no public
        accessor for the polling request; review if upgrading to PTB 23+.
        """
        if not (self._app and self._app.bot):
            return
        try:
            # PTB 22.x: _request is a (get_updates, general) tuple;
            # no public accessor exists for the polling request.
            polling_req = self._app.bot._request[0]  # noqa: SLF001
        except Exception:
            return
        try:
            await polling_req.shutdown()
        except Exception:
            logger.debug(
                "[%s] Polling request shutdown failed (non-fatal)",
                self.name, exc_info=True,
            )
        try:
            await polling_req.initialize()
            logger.debug(
                "[%s] Polling request pool drained before reconnect", self.name
            )
        except Exception:
            logger.debug(
                "[%s] Polling request re-initialize failed (non-fatal)",
                self.name, exc_info=True,
            )

    async def _handle_polling_network_error(self, error: Exception) -> None:
        """Reconnect polling after a transient network interruption.

        Triggered by NetworkError/TimedOut in the polling error callback, which
        happen when the host loses connectivity (Mac sleep, WiFi switch, VPN
        reconnect, etc.).  The gateway process stays alive but the long-poll
        connection silently dies; without this handler the bot never recovers.

        Strategy: exponential back-off (5s, 10s, 20s, 40s, 60s cap) up to
        MAX_NETWORK_RETRIES attempts, then mark the adapter retryable-fatal so
        the supervisor restarts the gateway process.
        """
        if self.has_fatal_error:
            return

        MAX_NETWORK_RETRIES = 10
        BASE_DELAY = 5
        MAX_DELAY = 60

        self._polling_network_error_count += 1
        attempt = self._polling_network_error_count

        if attempt > MAX_NETWORK_RETRIES:
            message = (
                "Telegram polling could not reconnect after %d network error retries. "
                "Restarting gateway." % MAX_NETWORK_RETRIES
            )
            logger.error("[%s] %s Last error: %s", self.name, message, error)
            self._set_fatal_error("telegram_network_error", message, retryable=True)
            await self._notify_fatal_error()
            return

        delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
        logger.warning(
            "[%s] Telegram network error (attempt %d/%d), reconnecting in %ds. Error: %s",
            self.name, attempt, MAX_NETWORK_RETRIES, delay, error,
        )
        await asyncio.sleep(delay)

        try:
            if self._app and self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
        except Exception:
            pass

        await self._drain_polling_connections()

        try:
            await self._app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
                error_callback=self._polling_error_callback_ref,
            )
            logger.info(
                "[%s] Telegram polling resumed after network error (attempt %d)",
                self.name, attempt,
            )
            self._polling_network_error_count = 0
            # start_polling() returning is necessary but not sufficient:
            # PTB's Updater can be left in a state where `running` is True
            # but the underlying long-poll task is wedged on a stale httpx
            # connection and never makes progress. No error_callback fires
            # in that state, so the reconnect ladder won't advance on its
            # own. Schedule a deferred probe to detect the wedge and
            # re-enter the ladder if needed.
            if not self.has_fatal_error:
                probe = asyncio.ensure_future(self._verify_polling_after_reconnect())
                self._background_tasks.add(probe)
                probe.add_done_callback(self._background_tasks.discard)
        except Exception as retry_err:
            logger.warning("[%s] Telegram polling reconnect failed: %s", self.name, retry_err)
            # start_polling failed — polling is dead and no further error
            # callbacks will fire, so schedule the next retry ourselves.
            if not self.has_fatal_error:
                task = asyncio.ensure_future(
                    self._handle_polling_network_error(retry_err)
                )
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

    async def _verify_polling_after_reconnect(self) -> None:
        """Heartbeat probe scheduled after a successful reconnect.

        PTB's Updater can survive a botched stop()+start_polling() cycle
        with `running=True` but a wedged consumer task. No error callback
        fires, so the reconnect ladder doesn't advance on its own. This
        probe detects the wedge by:

        1. Sleeping HEARTBEAT_PROBE_DELAY so a healthy long-poll has time
           to complete at least one cycle.
        2. Verifying `Updater.running` is still True.
        3. Probing the bot endpoint with a tight asyncio timeout. A
           wedged httpx pool fails this probe; a healthy one returns
           well under the timeout.

        On any failure, re-enter the reconnect ladder so the existing
        MAX_NETWORK_RETRIES path can ultimately escalate to fatal-error.
        """
        HEARTBEAT_PROBE_DELAY = 60
        PROBE_TIMEOUT = 10

        await asyncio.sleep(HEARTBEAT_PROBE_DELAY)

        if self.has_fatal_error:
            return
        if not (self._app and self._app.updater and self._app.updater.running):
            logger.warning(
                "[%s] Updater not running %ds after reconnect — treating as wedged",
                self.name, HEARTBEAT_PROBE_DELAY,
            )
            await self._handle_polling_network_error(
                RuntimeError("Updater not running after reconnect heartbeat")
            )
            return

        try:
            await asyncio.wait_for(self._app.bot.get_me(), PROBE_TIMEOUT)
        except Exception as probe_err:
            logger.warning(
                "[%s] Polling heartbeat probe failed %ds after reconnect: %s",
                self.name, HEARTBEAT_PROBE_DELAY, probe_err,
            )
            await self._handle_polling_network_error(probe_err)

    async def _handle_polling_conflict(self, error: Exception) -> None:
        if self.has_fatal_error and self.fatal_error_code == "telegram_polling_conflict":
            return
        # Track consecutive conflicts — transient 409s can occur when a
        # previous gateway instance hasn't fully released its long-poll
        # session on Telegram's server (e.g. during --replace handoffs or
        # systemd Restart=on-failure respawns).  Retry a few times before
        # giving up, so the old session has time to expire.
        self._polling_conflict_count += 1

        MAX_CONFLICT_RETRIES = 3
        RETRY_DELAY = 10  # seconds

        if self._polling_conflict_count <= MAX_CONFLICT_RETRIES:
            logger.warning(
                "[%s] Telegram polling conflict (%d/%d), will retry in %ds. Error: %s",
                self.name, self._polling_conflict_count, MAX_CONFLICT_RETRIES,
                RETRY_DELAY, error,
            )
            try:
                if self._app and self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
            except Exception:
                pass
            await asyncio.sleep(RETRY_DELAY)
            await self._drain_polling_connections()
            try:
                await self._app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                    error_callback=self._polling_error_callback_ref,
                )
                logger.info("[%s] Telegram polling resumed after conflict retry %d", self.name, self._polling_conflict_count)
                self._polling_conflict_count = 0  # reset on success
                return
            except Exception as retry_err:
                logger.warning("[%s] Telegram polling retry failed: %s", self.name, retry_err)
                # Don't fall through to fatal yet — wait for the next conflict
                # to trigger another retry attempt (up to MAX_CONFLICT_RETRIES).
                return

        # Exhausted retries — fatal
        message = (
            "Another process is already polling this Telegram bot token "
            "(possibly OpenClaw or another Hermes instance). "
            "Hermes stopped Telegram polling after %d retries. "
            "Only one poller can run per token — stop the other process "
            "and restart with 'hermes start'."
            % MAX_CONFLICT_RETRIES
        )
        logger.error("[%s] %s Original error: %s", self.name, message, error)
        self._set_fatal_error("telegram_polling_conflict", message, retryable=False)
        try:
            if self._app and self._app.updater:
                await self._app.updater.stop()
        except Exception as stop_error:
            logger.warning("[%s] Failed stopping Telegram polling after conflict: %s", self.name, stop_error, exc_info=True)
        await self._notify_fatal_error()

    async def _create_dm_topic(
        self,
        chat_id: int,
        name: str,
        icon_color: Optional[int] = None,
        icon_custom_emoji_id: Optional[str] = None,
    ) -> Optional[int]:
        """Create a forum topic in a private (DM) chat.

        Uses Bot API 9.4's createForumTopic which now works for 1-on-1 chats.
        Returns the message_thread_id on success, None on failure.
        """
        if not self._bot:
            return None
        try:
            kwargs: Dict[str, Any] = {"chat_id": chat_id, "name": name}
            if icon_color is not None:
                kwargs["icon_color"] = icon_color
            if icon_custom_emoji_id:
                kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id

            topic = await self._bot.create_forum_topic(**kwargs)
            thread_id = topic.message_thread_id
            logger.info(
                "[%s] Created DM topic '%s' in chat %s -> thread_id=%s",
                self.name, name, chat_id, thread_id,
            )
            return thread_id
        except Exception as e:
            error_text = str(e).lower()
            # If topic already exists, try to find it via getForumTopicIconStickers
            # or we just log and skip — Telegram doesn't provide a "list topics" API
            if "topic_name_duplicate" in error_text or "already" in error_text:
                logger.info(
                    "[%s] DM topic '%s' already exists in chat %s (will be mapped from incoming messages)",
                    self.name, name, chat_id,
                )
            elif "not a forum" in error_text or "forums_disabled" in error_text:
                logger.warning(
                    "[%s] Cannot create DM topic '%s' in chat %s: Topics mode is not enabled. "
                    "The user must open the DM with this bot in Telegram, tap the bot name "
                    "at the top, and enable 'Topics' in chat settings before topics can be created.",
                    self.name, name, chat_id,
                )
            else:
                logger.warning(
                    "[%s] Failed to create DM topic '%s' in chat %s: %s",
                    self.name, name, chat_id, e,
                )
            return None

    async def create_handoff_thread(
        self,
        parent_chat_id: str,
        name: str,
    ) -> Optional[str]:
        """Create a forum topic for a session handoff.

        Works for DM topics (Bot API 9.4+, requires user to enable Topics
        in their chat with the bot) and forum supergroups. Returns the
        ``message_thread_id`` as a string, or ``None`` on failure.
        """
        try:
            chat_id_int = int(parent_chat_id)
        except (TypeError, ValueError):
            return None
        thread_id = await self._create_dm_topic(chat_id_int, name=name)
        return str(thread_id) if thread_id else None

    async def rename_dm_topic(
        self,
        chat_id: int,
        thread_id: int,
        name: str,
    ) -> None:
        """Rename a forum topic in a private (DM) chat."""
        if not self._bot:
            return
        try:
            chat_id_arg = int(chat_id)
        except (TypeError, ValueError):
            chat_id_arg = chat_id
        await self._bot.edit_forum_topic(
            chat_id=chat_id_arg,
            message_thread_id=int(thread_id),
            name=name,
        )
        logger.info(
            "[%s] Renamed DM topic in chat %s thread_id=%s -> '%s'",
            self.name, chat_id, thread_id, name,
        )

    def _persist_dm_topic_thread_id(self, chat_id: int, topic_name: str, thread_id: int) -> None:
        """Save a newly created thread_id back into config.yaml so it persists across restarts."""
        try:
            from hermes_constants import get_hermes_home
            config_path = get_hermes_home() / "config.yaml"
            if not config_path.exists():
                logger.warning("[%s] Config file not found at %s, cannot persist thread_id", self.name, config_path)
                return

            import yaml as _yaml
            with open(config_path, "r", encoding="utf-8") as f:
                config = _yaml.safe_load(f) or {}

            # Navigate to platforms.telegram.extra.dm_topics
            dm_topics = (
                config.get("platforms", {})
                .get("telegram", {})
                .get("extra", {})
                .get("dm_topics", [])
            )
            if not dm_topics:
                return

            changed = False
            for chat_entry in dm_topics:
                if int(chat_entry.get("chat_id", 0)) != int(chat_id):
                    continue
                for t in chat_entry.get("topics", []):
                    if t.get("name") == topic_name and not t.get("thread_id"):
                        t["thread_id"] = thread_id
                        changed = True
                        break

            if changed:
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(config_path.parent),
                    suffix=".tmp",
                    prefix=".config_",
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        _yaml.dump(config, f, default_flow_style=False, sort_keys=False)
                        f.flush()
                        os.fsync(f.fileno())
                    atomic_replace(tmp_path, config_path)
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
                logger.info(
                    "[%s] Persisted thread_id=%s for topic '%s' in config.yaml",
                    self.name, thread_id, topic_name,
                )
        except Exception as e:
            logger.warning("[%s] Failed to persist thread_id to config: %s", self.name, e, exc_info=True)

    async def _setup_dm_topics(self) -> None:
        """Load or create configured DM topics for specified chats.

        Reads config.extra['dm_topics'] — a list of dicts:
        [
            {
                "chat_id": 123456789,
                "topics": [
                    {"name": "General", "icon_color": 7322096, "thread_id": 100},
                    {"name": "Accessibility Auditor", "icon_color": 9367192, "skill": "accessibility-auditor"}
                ]
            }
        ]

        If a topic already has a thread_id in the config (persisted from a previous
        creation), it is loaded into the cache without calling createForumTopic.
        Only topics without a thread_id are created via the API, and their thread_id
        is then saved back to config.yaml for future restarts.
        """
        if not self._dm_topics_config:
            return

        for chat_entry in self._dm_topics_config:
            chat_id = chat_entry.get("chat_id")
            topics = chat_entry.get("topics", [])
            if not chat_id or not topics:
                continue

            logger.info(
                "[%s] Setting up %d DM topic(s) for chat %s",
                self.name, len(topics), chat_id,
            )

            for topic_conf in topics:
                topic_name = topic_conf.get("name")
                if not topic_name:
                    continue

                cache_key = f"{chat_id}:{topic_name}"

                # If thread_id is already persisted in config, just load into cache
                existing_thread_id = topic_conf.get("thread_id")
                if existing_thread_id:
                    self._dm_topics[cache_key] = int(existing_thread_id)
                    logger.info(
                        "[%s] DM topic loaded from config: %s -> thread_id=%s",
                        self.name, cache_key, existing_thread_id,
                    )
                    continue

                # No persisted thread_id — create the topic via API
                icon_color = topic_conf.get("icon_color")
                icon_emoji = topic_conf.get("icon_custom_emoji_id")

                thread_id = await self._create_dm_topic(
                    chat_id=int(chat_id),
                    name=topic_name,
                    icon_color=icon_color,
                    icon_custom_emoji_id=icon_emoji,
                )

                if thread_id:
                    self._dm_topics[cache_key] = thread_id
                    logger.info(
                        "[%s] DM topic cached: %s -> thread_id=%s",
                        self.name, cache_key, thread_id,
                    )
                    # Persist thread_id to config so we don't recreate on next restart
                    self._persist_dm_topic_thread_id(int(chat_id), topic_name, thread_id)

                    # Send a seed message so the topic is visible in Telegram's client.
                    # Empty topics are hidden by the client UI until they contain a message.
                    try:
                        await self._bot.send_message(
                            chat_id=int(chat_id),
                            message_thread_id=thread_id,
                            text=f"\U0001f4cc {topic_name}",
                        )
                    except Exception as seed_err:
                        logger.debug(
                            "[%s] Could not send seed message to topic '%s': %s",
                            self.name, topic_name, seed_err,
                        )

    async def connect(self) -> bool:
        """Connect to Telegram via polling or webhook.

        By default, uses long polling (outbound connection to Telegram).
        If ``TELEGRAM_WEBHOOK_URL`` is set, starts an HTTP webhook server
        instead.  Webhook mode is useful for cloud deployments (Fly.io,
        Railway) where inbound HTTP can wake a suspended machine.

        Env vars for webhook mode::

            TELEGRAM_WEBHOOK_URL    Public HTTPS URL (e.g. https://app.fly.dev/telegram)
            TELEGRAM_WEBHOOK_PORT   Local listen port (default 8443)
            TELEGRAM_WEBHOOK_SECRET Secret token for update verification
        """
        if not TELEGRAM_AVAILABLE:
            logger.error(
                "[%s] python-telegram-bot not installed. Run: pip install python-telegram-bot",
                self.name,
            )
            return False
        
        if not self.config.token:
            logger.error("[%s] No bot token configured", self.name)
            return False
        
        try:
            if not self._acquire_platform_lock('telegram-bot-token', self.config.token, 'Telegram bot token'):
                return False

            # Build the application
            builder = Application.builder().token(self.config.token)
            custom_base_url = self.config.extra.get("base_url")
            if custom_base_url:
                builder = builder.base_url(custom_base_url)
                builder = builder.base_file_url(
                    self.config.extra.get("base_file_url", custom_base_url)
                )
                logger.info(
                    "[%s] Using custom Telegram base_url: %s",
                    self.name, custom_base_url,
                )

            # PTB defaults (pool_timeout=1s) are too aggressive on flaky networks and
            # can trigger "Pool timeout: All connections in the connection pool are occupied"
            # during reconnect/bootstrap. Use safer defaults and allow env overrides.
            def _env_int(name: str, default: int) -> int:
                try:
                    return int(os.getenv(name, str(default)))
                except (TypeError, ValueError):
                    return default

            def _env_float(name: str, default: float) -> float:
                try:
                    return float(os.getenv(name, str(default)))
                except (TypeError, ValueError):
                    return default

            request_kwargs = {
                "connection_pool_size": _env_int("HERMES_TELEGRAM_HTTP_POOL_SIZE", 512),
                "pool_timeout": _env_float("HERMES_TELEGRAM_HTTP_POOL_TIMEOUT", 8.0),
                "connect_timeout": _env_float("HERMES_TELEGRAM_HTTP_CONNECT_TIMEOUT", 10.0),
                "read_timeout": _env_float("HERMES_TELEGRAM_HTTP_READ_TIMEOUT", 20.0),
                "write_timeout": _env_float("HERMES_TELEGRAM_HTTP_WRITE_TIMEOUT", 20.0),
            }

            disable_fallback = (os.getenv("HERMES_TELEGRAM_DISABLE_FALLBACK_IPS", "").strip().lower() in ("1", "true", "yes", "on"))
            fallback_ips = self._fallback_ips()
            if not fallback_ips:
                fallback_ips = await discover_fallback_ips()
                logger.info(
                    "[%s] Auto-discovered Telegram fallback IPs: %s",
                    self.name,
                    ", ".join(fallback_ips),
                )

            proxy_targets = ["api.telegram.org", *fallback_ips]
            proxy_url = resolve_proxy_url("TELEGRAM_PROXY", target_hosts=proxy_targets)
            if fallback_ips and not proxy_url and not disable_fallback:
                logger.info(
                    "[%s] Telegram fallback IPs active: %s",
                    self.name,
                    ", ".join(fallback_ips),
                )
                # Keep request/update pools separate to reduce contention during
                # polling reconnect + bot API bootstrap/delete_webhook calls.
                request = HTTPXRequest(
                    **request_kwargs,
                    httpx_kwargs={"transport": TelegramFallbackTransport(fallback_ips)},
                )
                get_updates_request = HTTPXRequest(
                    **request_kwargs,
                    httpx_kwargs={"transport": TelegramFallbackTransport(fallback_ips)},
                )
            elif proxy_url:
                logger.info("[%s] Proxy detected; passing explicitly to HTTPXRequest: %s", self.name, proxy_url)
                request = HTTPXRequest(**request_kwargs, proxy=proxy_url)
                get_updates_request = HTTPXRequest(**request_kwargs, proxy=proxy_url)
            else:
                if disable_fallback:
                    logger.info("[%s] Telegram fallback-IP transport disabled via env", self.name)
                request = HTTPXRequest(**request_kwargs)
                get_updates_request = HTTPXRequest(**request_kwargs)

            builder = builder.request(request).get_updates_request(get_updates_request)
            self._app = builder.build()
            self._bot = self._app.bot
            
            # Register handlers
            if getattr(filters.UpdateType, "BUSINESS_MESSAGE", None) is not None:
                self._app.add_handler(TelegramMessageHandler(
                    filters.UpdateType.BUSINESS_MESSAGE,
                    self._handle_business_message,
                ))
            if BusinessConnectionHandler is not None:
                self._app.add_handler(BusinessConnectionHandler(self._handle_business_connection))
            if BusinessMessagesDeletedHandler is not None:
                self._app.add_handler(BusinessMessagesDeletedHandler(self._handle_business_messages_deleted))
            self._app.add_handler(TelegramMessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._handle_text_message
            ))
            self._app.add_handler(TelegramMessageHandler(
                filters.COMMAND,
                self._handle_command
            ))
            self._app.add_handler(TelegramMessageHandler(
                filters.LOCATION | getattr(filters, "VENUE", filters.LOCATION),
                self._handle_location_message
            ))
            self._app.add_handler(TelegramMessageHandler(
                filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL | filters.Sticker.ALL,
                self._handle_media_message
            ))
            # Handle inline keyboard button callbacks (update prompts)
            self._app.add_handler(CallbackQueryHandler(self._handle_callback_query))
            
            # Start polling — retry initialize() for transient TLS resets
            try:
                from telegram.error import NetworkError, TimedOut
            except ImportError:
                NetworkError = TimedOut = OSError  # type: ignore[misc,assignment]
            _max_connect = 8
            for _attempt in range(_max_connect):
                try:
                    await self._app.initialize()
                    break
                except (NetworkError, TimedOut, OSError) as init_err:
                    if _attempt < _max_connect - 1:
                        wait = min(2 ** _attempt, 15)
                        logger.warning(
                            "[%s] Connect attempt %d/%d failed: %s — retrying in %ds",
                            self.name, _attempt + 1, _max_connect, init_err, wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        raise
            await self._app.start()

            # Decide between webhook and polling mode
            webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()

            if webhook_url:
                # ── Webhook mode ─────────────────────────────────────
                # Telegram pushes updates to our HTTP endpoint.  This
                # enables cloud platforms (Fly.io, Railway) to auto-wake
                # suspended machines on inbound HTTP traffic.
                #
                # SECURITY: TELEGRAM_WEBHOOK_SECRET is REQUIRED. Without it,
                # python-telegram-bot passes secret_token=None and the
                # webhook endpoint accepts any HTTP POST — attackers can
                # inject forged updates as if from Telegram. Refuse to
                # start rather than silently run in fail-open mode.
                # See GHSA-3vpc-7q5r-276h.
                webhook_port = int(os.getenv("TELEGRAM_WEBHOOK_PORT", "8443"))
                webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
                if not webhook_secret:
                    raise RuntimeError(
                        "TELEGRAM_WEBHOOK_SECRET is required when "
                        "TELEGRAM_WEBHOOK_URL is set. Without it, the "
                        "webhook endpoint accepts forged updates from "
                        "anyone who can reach it — see "
                        "https://github.com/NousResearch/hermes-agent/"
                        "security/advisories/GHSA-3vpc-7q5r-276h.\n\n"
                        "Generate a secret and set it in your .env:\n"
                        "  export TELEGRAM_WEBHOOK_SECRET=\"$(openssl rand -hex 32)\"\n\n"
                        "Then register it with Telegram when setting the "
                        "webhook via setWebhook's secret_token parameter."
                    )
                from urllib.parse import urlparse
                webhook_path = urlparse(webhook_url).path or "/telegram"

                await self._app.updater.start_webhook(
                    listen="0.0.0.0",
                    port=webhook_port,
                    url_path=webhook_path,
                    webhook_url=webhook_url,
                    secret_token=webhook_secret,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                )
                self._webhook_mode = True
                logger.info(
                    "[%s] Webhook server listening on 0.0.0.0:%d%s",
                    self.name, webhook_port, webhook_path,
                )
            else:
                # ── Polling mode (default) ───────────────────────────
                # Clear any stale webhook first so polling doesn't inherit a
                # previous webhook registration and silently stop receiving updates.
                delete_webhook = getattr(self._bot, "delete_webhook", None)
                if callable(delete_webhook):
                    await delete_webhook(drop_pending_updates=False)

                loop = asyncio.get_running_loop()

                def _polling_error_callback(error: Exception) -> None:
                    if self._polling_error_task and not self._polling_error_task.done():
                        return
                    if self._looks_like_polling_conflict(error):
                        self._polling_error_task = loop.create_task(self._handle_polling_conflict(error))
                    elif self._looks_like_network_error(error):
                        logger.warning("[%s] Telegram network error, scheduling reconnect: %s", self.name, error)
                        self._polling_error_task = loop.create_task(self._handle_polling_network_error(error))
                    else:
                        logger.error("[%s] Telegram polling error: %s", self.name, error, exc_info=True)

                # Store reference for retry use in _handle_polling_conflict
                self._polling_error_callback_ref = _polling_error_callback

                await self._app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                    error_callback=_polling_error_callback,
                )
            
            # Register bot commands so Telegram shows a hint menu when users type /
            # List is derived from the central COMMAND_REGISTRY — adding a new
            # gateway command there automatically adds it to the Telegram menu.
            try:
                from telegram import BotCommand
                from hermes_cli.commands import telegram_menu_commands
                # Telegram allows up to 100 commands but has an undocumented
                # payload size limit.  Skill descriptions are truncated to 40
                # chars in telegram_menu_commands() to fit 100 commands safely.
                menu_commands, hidden_count = telegram_menu_commands(max_commands=100)
                await self._bot.set_my_commands([
                    BotCommand(name, desc) for name, desc in menu_commands
                ])
                if hidden_count:
                    logger.info(
                        "[%s] Telegram menu: %d commands registered, %d hidden (over 100 limit). Use /commands for full list.",
                        self.name, len(menu_commands), hidden_count,
                    )
            except Exception as e:
                logger.warning(
                    "[%s] Could not register Telegram command menu: %s",
                    self.name,
                    e,
                    exc_info=True,
                )
            
            self._mark_connected()
            mode = "webhook" if self._webhook_mode else "polling"
            logger.info("[%s] Connected to Telegram (%s mode)", self.name, mode)

            # Set up DM topics (Bot API 9.4 — Private Chat Topics)
            # Runs after connection is established so the bot can call createForumTopic.
            # Failures here are non-fatal — the bot works fine without topics.
            try:
                await self._setup_dm_topics()
            except Exception as topics_err:
                logger.warning(
                    "[%s] DM topics setup failed (non-fatal): %s",
                    self.name, topics_err, exc_info=True,
                )

            return True
            
        except Exception as e:
            self._release_platform_lock()
            message = f"Telegram startup failed: {e}"
            self._set_fatal_error("telegram_connect_error", message, retryable=True)
            logger.error("[%s] Failed to connect to Telegram: %s", self.name, e, exc_info=True)
            return False

    async def disconnect(self) -> None:
        """Stop polling/webhook, cancel pending album flushes, and disconnect."""
        pending_media_group_tasks = list(self._media_group_tasks.values())
        for task in pending_media_group_tasks:
            task.cancel()
        if pending_media_group_tasks:
            await asyncio.gather(*pending_media_group_tasks, return_exceptions=True)
        self._media_group_tasks.clear()
        self._media_group_events.clear()

        if self._app:
            try:
                # Only stop the updater if it's running
                if self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
                if self._app.running:
                    await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                logger.warning("[%s] Error during Telegram disconnect: %s", self.name, e, exc_info=True)
        self._release_platform_lock()

        for task in self._pending_photo_batch_tasks.values():
            if task and not task.done():
                task.cancel()
        self._pending_photo_batch_tasks.clear()
        self._pending_photo_batches.clear()

        self._mark_disconnected()
        self._app = None
        self._bot = None
        logger.info("[%s] Disconnected from Telegram", self.name)

    def _should_thread_reply(self, reply_to: Optional[str], chunk_index: int) -> bool:
        """Determine if this message chunk should thread to the original message.

        Args:
            reply_to: The original message ID to reply to
            chunk_index: Index of this chunk (0 = first chunk)

        Returns:
            True if this chunk should be threaded to the original message
        """
        if not reply_to:
            return False
        mode = self._reply_to_mode
        if mode == "off":
            return False
        elif mode == "all":
            return True
        else:  # "first" (default)
            return chunk_index == 0

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SendResult:
        """Send a message to a Telegram chat."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        
        # Skip whitespace-only text to prevent Telegram 400 empty-text errors.
        if not content or not content.strip():
            return SendResult(success=True, message_id=None)
        
        try:
            guard_result = await self._inline_preview_guard_send_result(chat_id, content, metadata)
            if guard_result is not None:
                return guard_result
            guard_replacement = self._inline_preview_guard_replacement(chat_id, content, metadata)
            if guard_replacement:
                content = guard_replacement

            # Format and split message if needed
            formatted = self.format_message(content)
            chunks = self.truncate_message(
                formatted, self.MAX_MESSAGE_LENGTH, len_fn=utf16_len,
            )
            if len(chunks) > 1:
                # truncate_message appends a raw " (1/2)" suffix. Escape the
                # MarkdownV2-special parentheses so Telegram doesn't reject the
                # chunk and fall back to plain text.
                chunks = [
                    re.sub(r" \((\d+)/(\d+)\)$", r" \\(\1/\2\\)", chunk)
                    for chunk in chunks
                ]
            
            message_ids = []
            thread_id = self._metadata_thread_id(metadata)
            
            try:
                from telegram.error import NetworkError as _NetErr
            except ImportError:
                _NetErr = OSError  # type: ignore[misc,assignment]

            try:
                from telegram.error import BadRequest as _BadReq
            except ImportError:
                _BadReq = None  # type: ignore[assignment,misc]

            try:
                from telegram.error import TimedOut as _TimedOut
            except (ImportError, AttributeError):
                _TimedOut = None  # type: ignore[assignment,misc]

            for i, chunk in enumerate(chunks):
                metadata_reply_to = self._metadata_reply_to_message_id(metadata)
                reply_to_source = reply_to or (
                    str(metadata_reply_to)
                    if metadata and metadata.get("telegram_dm_topic_reply_fallback") and metadata_reply_to is not None else None
                )
                if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
                    should_thread = reply_to_source is not None
                else:
                    should_thread = self._should_thread_reply(reply_to_source, i)
                reply_to_id = int(reply_to_source) if should_thread and reply_to_source else None
                human20_reply_markup = self._human20_inline_markup(chat_id, metadata) if i == len(chunks) - 1 else None
                thread_kwargs = self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                )
                effective_thread_id = thread_kwargs.get("message_thread_id")

                msg = None
                for _send_attempt in range(3):
                    try:
                        # Try Markdown first, fall back to plain text if it fails
                        try:
                            msg = await self._bot.send_message(
                                chat_id=int(chat_id),
                                text=chunk,
                                parse_mode=ParseMode.MARKDOWN_V2,
                                reply_to_message_id=reply_to_id,
                                **thread_kwargs,
                                **self._business_kwargs(metadata),
                                **self._link_preview_kwargs(),
                                reply_markup=human20_reply_markup,
                                **self._notification_kwargs(metadata),
                            )
                        except Exception as md_error:
                            # Markdown parsing failed, try plain text
                            if "parse" in str(md_error).lower() or "markdown" in str(md_error).lower():
                                logger.warning("[%s] MarkdownV2 parse failed, falling back to plain text: %s", self.name, md_error)
                                plain_chunk = _strip_mdv2(chunk)
                                msg = await self._bot.send_message(
                                    chat_id=int(chat_id),
                                    text=plain_chunk,
                                    parse_mode=None,
                                    reply_to_message_id=reply_to_id,
                                    **thread_kwargs,
                                    **self._business_kwargs(metadata),
                                    **self._link_preview_kwargs(),
                                    reply_markup=human20_reply_markup,
                                    **self._notification_kwargs(metadata),
                                )
                            else:
                                raise
                        break  # success
                    except _NetErr as send_err:
                        # BadRequest is a subclass of NetworkError in
                        # python-telegram-bot but represents permanent errors
                        # (not transient network issues). Detect and handle
                        # specific cases instead of blindly retrying.
                        if _BadReq and isinstance(send_err, _BadReq):
                            if self._is_thread_not_found_error(send_err) and effective_thread_id is not None:
                                # Thread doesn't exist — retry without
                                # message_thread_id so the message still
                                # reaches the chat.
                                logger.warning(
                                    "[%s] Thread %s not found, retrying without message_thread_id",
                                    self.name, effective_thread_id,
                                )
                                effective_thread_id = None
                                thread_kwargs = {"message_thread_id": None}
                                continue
                            err_lower = str(send_err).lower()
                            if "message to be replied not found" in err_lower and reply_to_id is not None:
                                # Original message was deleted before we
                                # could reply. For private-topic fallback
                                # sends, message_thread_id is only valid with
                                # the reply anchor, so drop both together.
                                logger.warning(
                                    "[%s] Reply target deleted, retrying without reply_to: %s",
                                    self.name, send_err,
                                )
                                reply_to_id = None
                                if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
                                    thread_kwargs = {}
                                    effective_thread_id = None
                                else:
                                    thread_kwargs = self._thread_kwargs_for_send(
                                        chat_id,
                                        thread_id,
                                        metadata,
                                        reply_to_message_id=reply_to_id,
                                    )
                                    effective_thread_id = thread_kwargs.get("message_thread_id")
                                continue
                            # Other BadRequest errors are permanent — don't retry
                            raise
                        # TimedOut is also a subclass of NetworkError but
                        # indicates the request may have reached the server —
                        # retrying risks duplicate message delivery.
                        if _TimedOut and isinstance(send_err, _TimedOut):
                            raise
                        if _send_attempt < 2:
                            wait = 2 ** _send_attempt
                            logger.warning("[%s] Network error on send (attempt %d/3), retrying in %ds: %s",
                                           self.name, _send_attempt + 1, wait, send_err)
                            await asyncio.sleep(wait)
                        else:
                            raise
                    except Exception as send_err:
                        retry_after = getattr(send_err, "retry_after", None)
                        if retry_after is not None or "retry after" in str(send_err).lower():
                            if _send_attempt < 2:
                                wait = float(retry_after) if retry_after is not None else 1.0
                                logger.warning(
                                    "[%s] Telegram flood control on send (attempt %d/3), retrying in %.1fs: %s",
                                    self.name,
                                    _send_attempt + 1,
                                    wait,
                                    send_err,
                                )
                                await asyncio.sleep(wait)
                                continue
                        raise
                message_ids.append(str(msg.message_id))
            
            return SendResult(
                success=True,
                message_id=message_ids[0] if message_ids else None,
                raw_response={"message_ids": message_ids}
            )
            
        except Exception as e:
            logger.error("[%s] Failed to send Telegram message: %s", self.name, e, exc_info=True)
            # TimedOut means the request may have reached Telegram —
            # mark as non-retryable so _send_with_retry() doesn't re-send.
            _to = locals().get("_TimedOut")
            err_str = str(e).lower()
            is_timeout = (_to and isinstance(e, _to)) or "timed out" in err_str
            return SendResult(success=False, error=str(e), retryable=not is_timeout)

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        """Edit a previously sent Telegram message."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        try:
            if not finalize:
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=content,
                )
                return SendResult(success=True, message_id=message_id)

            formatted = self.format_message(content)
            try:
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=formatted,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            except Exception as fmt_err:
                # "Message is not modified" is a no-op, not an error
                if "not modified" in str(fmt_err).lower():
                    return SendResult(success=True, message_id=message_id)
                # Fallback: retry without markdown formatting
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=content,
                )
            return SendResult(success=True, message_id=message_id)
        except Exception as e:
            err_str = str(e).lower()
            # "Message is not modified" — content identical, treat as success
            if "not modified" in err_str:
                return SendResult(success=True, message_id=message_id)
            # Message too long — content exceeded 4096 chars (e.g. during
            # streaming).  Truncate and succeed so the stream consumer can
            # split the overflow into a new message instead of dying.
            if "message_too_long" in err_str or "too long" in err_str:
                truncated = _prefix_within_utf16_limit(
                    content, self.MAX_MESSAGE_LENGTH - 20
                ) + "…"
                try:
                    await self._bot.edit_message_text(
                        chat_id=int(chat_id),
                        message_id=int(message_id),
                        text=truncated,
                    )
                except Exception:
                    pass  # best-effort truncation
                return SendResult(success=True, message_id=message_id)
            # Flood control / RetryAfter — short waits are retried inline,
            # long waits return a failure immediately so streaming can fall back
            # to a normal final send instead of leaving a truncated partial.
            retry_after = getattr(e, "retry_after", None)
            if retry_after is not None or "retry after" in err_str:
                wait = retry_after if retry_after else 1.0
                logger.warning(
                    "[%s] Telegram flood control, waiting %.1fs",
                    self.name, wait,
                )
                if wait > 5.0:
                    return SendResult(success=False, error=f"flood_control:{wait}")
                await asyncio.sleep(wait)
                try:
                    await self._bot.edit_message_text(
                        chat_id=int(chat_id),
                        message_id=int(message_id),
                        text=content,
                    )
                    return SendResult(success=True, message_id=message_id)
                except Exception as retry_err:
                    logger.error(
                        "[%s] Edit retry failed after flood wait: %s",
                        self.name, retry_err,
                    )
                    return SendResult(success=False, error=str(retry_err))
            logger.error(
                "[%s] Failed to edit Telegram message %s: %s",
                self.name,
                message_id,
                e,
                exc_info=True,
            )
            return SendResult(success=False, error=str(e))

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Delete a previously sent Telegram message.

        Used by the stream consumer's fresh-final cleanup path (ported
        from openclaw/openclaw#72038) to remove long-lived preview
        messages after sending the completed reply as a fresh message.
        Telegram's Bot API ``deleteMessage`` works for bot-posted
        messages in the last 48 hours.  Failures are non-fatal — the
        caller leaves the preview in place and logs at debug level.
        """
        if not self._bot:
            return False
        try:
            await self._bot.delete_message(
                chat_id=int(chat_id),
                message_id=int(message_id),
            )
            return True
        except Exception as e:
            logger.debug(
                "[%s] Failed to delete Telegram message %s: %s",
                self.name, message_id, e,
            )
            return False

    async def send_update_prompt(
        self, chat_id: str, prompt: str, default: str = "",
        session_key: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an inline-keyboard update prompt (Yes / No buttons).

        Used by the gateway ``/update`` watcher when ``hermes update --gateway``
        needs user input (stash restore, config migration).
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        try:
            default_hint = f" (default: {default})" if default else ""
            text = f"⚕ *Update needs your input:*\n\n{prompt}{default_hint}"
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✓ Yes", callback_data="update_prompt:y"),
                    InlineKeyboardButton("✗ No", callback_data="update_prompt:n"),
                ]
            ])
            thread_id = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(None, metadata)
            msg = await self._bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
                reply_to_message_id=reply_to_id,
                **self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                ),
                **self._link_preview_kwargs(),
            )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_update_prompt failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_exec_approval(
        self, chat_id: str, command: str, session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an inline-keyboard approval prompt with interactive buttons.

        The buttons call ``resolve_gateway_approval()`` to unblock the waiting
        agent thread — same mechanism as the text ``/approve`` flow.
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            cmd_preview = command[:3800] + "..." if len(command) > 3800 else command
            text = (
                f"⚠️ <b>Command Approval Required</b>\n\n"
                f"<pre>{_html.escape(cmd_preview)}</pre>\n\n"
                f"Reason: {_html.escape(description)}"
            )

            # Resolve thread context for thread replies
            thread_id = self._metadata_thread_id(metadata)

            # We'll use the message_id as part of callback_data to look up session_key
            # Send a placeholder first, then update — or use a counter.
            # Simpler: use a monotonic counter to generate short IDs.
            import itertools
            if not hasattr(self, "_approval_counter"):
                self._approval_counter = itertools.count(1)
            approval_id = next(self._approval_counter)

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Allow Once", callback_data=f"ea:once:{approval_id}"),
                    InlineKeyboardButton("✅ Session", callback_data=f"ea:session:{approval_id}"),
                ],
                [
                    InlineKeyboardButton("✅ Always", callback_data=f"ea:always:{approval_id}"),
                    InlineKeyboardButton("❌ Deny", callback_data=f"ea:deny:{approval_id}"),
                ],
            ])

            kwargs: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "text": text,
                "parse_mode": ParseMode.HTML,
                "reply_markup": keyboard,
                **self._link_preview_kwargs(),
            }
            reply_to_id = self._reply_to_message_id_for_send(None, metadata)
            kwargs["reply_to_message_id"] = reply_to_id
            kwargs.update(
                self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                )
            )

            msg = await self._bot.send_message(**kwargs)

            # Store session_key keyed by approval_id for the callback handler
            self._approval_state[approval_id] = session_key

            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_exec_approval failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_slash_confirm(
        self, chat_id: str, title: str, message: str, session_key: str,
        confirm_id: str, metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Render a three-button slash-command confirmation prompt."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            # Message body: render as plain text (message already contains
            # markdown formatting from the gateway primitive).
            preview = message if len(message) <= 3800 else message[:3800] + "..."

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve Once", callback_data=f"sc:once:{confirm_id}"),
                    InlineKeyboardButton("🔒 Always Approve", callback_data=f"sc:always:{confirm_id}"),
                ],
                [
                    InlineKeyboardButton("❌ Cancel", callback_data=f"sc:cancel:{confirm_id}"),
                ],
            ])

            thread_id = self._metadata_thread_id(metadata)
            kwargs: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "text": preview,
                "parse_mode": ParseMode.MARKDOWN,
                "reply_markup": keyboard,
                **self._link_preview_kwargs(),
            }
            reply_to_id = self._reply_to_message_id_for_send(None, metadata)
            kwargs["reply_to_message_id"] = reply_to_id
            kwargs.update(
                self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                )
            )

            msg = await self._bot.send_message(**kwargs)
            self._slash_confirm_state[confirm_id] = session_key
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_slash_confirm failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_model_picker(
        self,
        chat_id: str,
        providers: list,
        current_model: str,
        current_provider: str,
        session_key: str,
        on_model_selected,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an interactive inline-keyboard model picker.

        Two-step drill-down: provider selection → model selection.
        Edits the same message in-place as the user navigates.
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            from hermes_cli.providers import get_label
        except ImportError:
            def get_label(slug):
                return slug

        try:
            # Build provider buttons — 2 per row
            buttons: list = []
            for p in providers:
                count = p.get("total_models", len(p.get("models", [])))
                label = f"{p['name']} ({count})"
                if p.get("is_current"):
                    label = f"✓ {label}"
                # Compact callback data: mp:<slug>  (max 64 bytes)
                buttons.append(
                    InlineKeyboardButton(label, callback_data=f"mp:{p['slug']}")
                )

            rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
            rows.append([InlineKeyboardButton("✗ Cancel", callback_data="mx")])
            keyboard = InlineKeyboardMarkup(rows)

            provider_label = get_label(current_provider)
            text = (
                f"⚙ *Model Configuration*\n\n"
                f"Current model: `{current_model or 'unknown'}`\n"
                f"Provider: {provider_label}\n\n"
                f"Select a provider:"
            )

            thread_id = metadata.get("thread_id") if metadata else None
            reply_to_id = self._reply_to_message_id_for_send(None, metadata)
            msg = await self._bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
                reply_to_message_id=reply_to_id,
                **self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                ),
                **self._link_preview_kwargs(),
            )

            # Store picker state keyed by chat_id
            self._model_picker_state[str(chat_id)] = {
                "msg_id": msg.message_id,
                "providers": providers,
                "session_key": session_key,
                "on_model_selected": on_model_selected,
                "current_model": current_model,
                "current_provider": current_provider,
            }

            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_model_picker failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    _MODEL_PAGE_SIZE = 8

    def _build_model_keyboard(self, models: list, page: int) -> tuple:
        """Build paginated model buttons. Returns (keyboard, page_info_text)."""
        page_size = self._MODEL_PAGE_SIZE
        total = len(models)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))

        start = page * page_size
        end = min(start + page_size, total)
        page_models = models[start:end]

        buttons: list = []
        for i, model_id in enumerate(page_models):
            abs_idx = start + i
            short = model_id.split("/")[-1] if "/" in model_id else model_id
            if len(short) > 38:
                short = short[:35] + "..."
            buttons.append(
                InlineKeyboardButton(short, callback_data=f"mm:{abs_idx}")
            )

        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]

        # Pagination row (if needed)
        if total_pages > 1:
            nav: list = []
            if page > 0:
                nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"mg:{page - 1}"))
            nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="mx:noop"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton("Next ▶", callback_data=f"mg:{page + 1}"))
            rows.append(nav)

        rows.append([
            InlineKeyboardButton("◀ Back", callback_data="mb"),
            InlineKeyboardButton("✗ Cancel", callback_data="mx"),
        ])

        page_info = f" ({start + 1}–{end} of {total})" if total_pages > 1 else ""
        return InlineKeyboardMarkup(rows), page_info

    async def _handle_model_picker_callback(
        self, query, data: str, chat_id: str
    ) -> None:
        """Handle model picker inline keyboard callbacks (mp:/mm:/mb:/mx:/mg:)."""
        state = self._model_picker_state.get(chat_id)
        if not state:
            await query.answer(text="Picker expired — use /model again.")
            return

        try:
            from hermes_cli.providers import get_label
        except ImportError:
            def get_label(slug):
                return slug

        if data.startswith("mp:"):
            # --- Provider selected: show model buttons (page 0) ---
            provider_slug = data[3:]
            provider = next(
                (p for p in state["providers"] if p["slug"] == provider_slug),
                None,
            )
            if not provider:
                await query.answer(text="Provider not found.")
                return

            models = provider.get("models", [])
            state["selected_provider"] = provider_slug
            state["selected_provider_name"] = provider.get("name", provider_slug)
            state["model_list"] = models
            state["model_page"] = 0

            keyboard, page_info = self._build_model_keyboard(models, 0)

            pname = provider.get("name", provider_slug)
            total = provider.get("total_models", len(models))
            shown = len(models)
            extra = f"\n_{total - shown} more available — type `/model <name>` directly_" if total > shown else ""

            await query.edit_message_text(
                text=(
                    f"⚙ *Model Configuration*\n\n"
                    f"Provider: *{pname}*{page_info}\n"
                    f"Select a model:{extra}"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data.startswith("mg:"):
            # --- Page navigation ---
            try:
                page = int(data[3:])
            except ValueError:
                await query.answer(text="Invalid page.")
                return

            models = state.get("model_list", [])
            state["model_page"] = page

            keyboard, page_info = self._build_model_keyboard(models, page)

            pname = state.get("selected_provider_name", "")
            provider_slug = state.get("selected_provider", "")
            provider = next(
                (p for p in state["providers"] if p["slug"] == provider_slug),
                None,
            )
            total = provider.get("total_models", len(models)) if provider else len(models)
            shown = len(models)
            extra = f"\n_{total - shown} more available — type `/model <name>` directly_" if total > shown else ""

            await query.edit_message_text(
                text=(
                    f"⚙ *Model Configuration*\n\n"
                    f"Provider: *{pname}*{page_info}\n"
                    f"Select a model:{extra}"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data.startswith("mm:"):
            # --- Model selected: perform the switch ---
            try:
                idx = int(data[3:])
            except ValueError:
                await query.answer(text="Invalid selection.")
                return

            model_list = state.get("model_list", [])
            if idx < 0 or idx >= len(model_list):
                await query.answer(text="Invalid model index.")
                return

            model_id = model_list[idx]
            provider_slug = state.get("selected_provider", "")
            callback = state.get("on_model_selected")

            if not callback:
                await query.answer(text="Picker expired.")
                return

            try:
                result_text = await callback(chat_id, model_id, provider_slug)
            except Exception as exc:
                logger.error("Model picker switch failed: %s", exc)
                result_text = f"Error switching model: {exc}"

            # Edit message to show confirmation, remove buttons
            try:
                await query.edit_message_text(
                    text=result_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=None,
                )
            except Exception:
                # Markdown parse failure — retry as plain text
                try:
                    await query.edit_message_text(
                        text=result_text,
                        parse_mode=None,
                        reply_markup=None,
                    )
                except Exception:
                    pass
            await query.answer(text="Model switched!")

            # Clean up state
            self._model_picker_state.pop(chat_id, None)

        elif data == "mb":
            # --- Back to provider list ---
            buttons = []
            for p in state["providers"]:
                count = p.get("total_models", len(p.get("models", [])))
                label = f"{p['name']} ({count})"
                if p.get("is_current"):
                    label = f"✓ {label}"
                buttons.append(
                    InlineKeyboardButton(label, callback_data=f"mp:{p['slug']}")
                )

            rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
            rows.append([InlineKeyboardButton("✗ Cancel", callback_data="mx")])
            keyboard = InlineKeyboardMarkup(rows)

            try:
                provider_label = get_label(state["current_provider"])
            except Exception:
                provider_label = state["current_provider"]

            await query.edit_message_text(
                text=(
                    f"⚙ *Model Configuration*\n\n"
                    f"Current model: `{state['current_model'] or 'unknown'}`\n"
                    f"Provider: {provider_label}\n\n"
                    f"Select a provider:"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data == "mx":
            # --- Cancel ---
            self._model_picker_state.pop(chat_id, None)
            await query.edit_message_text(
                text="Model selection cancelled.",
                reply_markup=None,
            )
            await query.answer()

        else:
            # Catch-all (e.g. page counter button "mx:noop")
            await query.answer()

    async def _handle_gptprof_callback(self, query, data: str) -> None:
        """Handle Chip's /gptprof inline keyboard callbacks."""
        auth_path = "/home/hermes/.hermes/auth.json"
        config_path = "/home/hermes/.hermes/config.yaml"
        hcp_dir = "/home/hermes/.hermes/skills/chip/hcp"
        send_script = "/home/hermes/.hermes/skills/chip/gptprof/send_buttons.py"
        python_bin = "/opt/hermes-agent/venv/bin/python3"
        pending_auth_path = "/tmp/gptprof_pending_auth.json"

        def _load_json(path: str, default: Any) -> Any:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return default

        def _write_json(path: str, value: Any) -> None:
            directory = os.path.dirname(path) or "."
            fd, tmp = tempfile.mkstemp(prefix=".gptprof-", suffix=".json", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(value, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                atomic_replace(tmp, path)
            finally:
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except OSError:
                    pass

        def _write_yaml(path: str, value: Any) -> None:
            import yaml as _yaml
            directory = os.path.dirname(path) or "."
            fd, tmp = tempfile.mkstemp(prefix=".gptprof-", suffix=".yaml", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    _yaml.safe_dump(value, f, allow_unicode=True, sort_keys=False)
                atomic_replace(tmp, path)
            finally:
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except OSError:
                    pass

        async def _send_fresh_card(answer: str, *, clear_cache: bool = False) -> None:
            await query.answer(text=answer)
            if clear_cache:
                try:
                    _write_json("/tmp/gptprof_usage_cache.json", {})
                except Exception:
                    pass
            try:
                subprocess.Popen(
                    [python_bin, send_script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception as exc:
                logger.error("gptprof refresh failed: %s", exc, exc_info=True)

        def _usage_score(slug: str) -> tuple[int, int]:
            cache = _load_json("/tmp/gptprof_usage_cache.json", {})
            payload = cache.get(slug, {}) if isinstance(cache, dict) else {}
            rl = (payload.get("rate_limit") or {}) if isinstance(payload, dict) else {}
            primary = rl.get("primary_window") or {}
            secondary = rl.get("secondary_window") or {}
            def left(window: dict) -> int:
                used = window.get("used_percent") if isinstance(window, dict) else None
                return max(0, 100 - int(used)) if isinstance(used, (int, float)) else -1
            return (left(secondary), left(primary))

        def _active_slug() -> str:
            auth = _load_json(auth_path, {})
            if isinstance(auth, dict):
                codex = auth.get("codex")
                if isinstance(codex, dict) and codex.get("profile"):
                    return str(codex["profile"])
            return "gptinvest23"

        def _model_for_slug(slug: str) -> str:
            return {
                "gptinvest23": "gpt-5.5",
                "markov495": "gpt-5.4",
                "mintsage": "gpt-5.4-mini",
                "omnifocusme": "gpt-5.4-mini",
            }.get(slug, "gpt-5.5")

        def _persist_new_auth(slug: str, access_token: str, refresh_token: str) -> None:
            profile_path = os.path.join(hcp_dir, f"{slug}.json")
            profile = _load_json(profile_path, {})
            if not isinstance(profile, dict):
                profile = {}
            profile.setdefault("profile", slug)
            profile.setdefault("email", f"{slug}@gmail.com")
            profile.setdefault("plan", "Codex")
            profile["access_token"] = access_token
            profile["refresh_token"] = refresh_token
            _write_json(profile_path, profile)

            auth = _load_json(auth_path, {})
            if not isinstance(auth, dict):
                auth = {}
            auth["codex"] = {
                "profile": slug,
                "plan": profile.get("plan"),
                "email": profile.get("email"),
                "access_token": access_token,
                "refresh_token": refresh_token,
            }
            pool_root = auth.setdefault("credential_pool", {})
            if isinstance(pool_root, dict):
                pool = pool_root.get("openai-codex")
                if not isinstance(pool, list):
                    pool = []
                selected_source = f"gptprof:{slug}"
                existing = [c for c in pool if isinstance(c, dict) and c.get("source") != selected_source]
                selected = {
                    "id": f"gptprof-{slug}",
                    "label": profile.get("email") or slug,
                    "auth_type": "oauth",
                    "priority": 0,
                    "source": selected_source,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "base_url": "https://chatgpt.com/backend-api/codex",
                    "last_status": "ok",
                    "last_status_at": time.time(),
                    "request_count": 0,
                }
                for idx, item in enumerate(existing, start=1):
                    if isinstance(item, dict):
                        item["priority"] = idx
                pool_root["openai-codex"] = [selected, *existing]
            _write_json(auth_path, auth)

            try:
                import yaml as _yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = _yaml.safe_load(f) or {}
                model_cfg = cfg.get("model")
                if not isinstance(model_cfg, dict):
                    model_cfg = {}
                model_cfg["provider"] = "openai-codex"
                model_cfg["default"] = _model_for_slug(slug)
                model_cfg["base_url"] = "https://chatgpt.com/backend-api/codex"
                model_cfg["api_mode"] = "codex_responses"
                cfg["model"] = model_cfg
                _write_yaml(config_path, cfg)
            except Exception as exc:
                logger.error("gptprof new auth config update failed: %s", exc, exc_info=True)

        async def _start_new_auth() -> None:
            slug = _active_slug()
            issuer = "https://auth.openai.com"
            client_id = "app_EMoamEEZ73f0CkXaXp7hrann"
            token_url = "https://auth.openai.com/oauth/token"
            message = getattr(query, "message", None)
            try:
                import httpx
                async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                    resp = await client.post(
                        f"{issuer}/api/accounts/deviceauth/usercode",
                        json={"client_id": client_id},
                        headers={"Content-Type": "application/json"},
                    )
                if resp.status_code != 200:
                    await query.answer(text=f"New auth failed: {resp.status_code}")
                    return
                device_data = resp.json()
                user_code = str(device_data.get("user_code") or "")
                device_auth_id = str(device_data.get("device_auth_id") or "")
                poll_interval = max(3, int(device_data.get("interval") or 5))
                if not user_code or not device_auth_id:
                    await query.answer(text="New auth failed: empty device code.")
                    return
            except Exception as exc:
                logger.error("gptprof new auth start failed: %s", exc, exc_info=True)
                await query.answer(text="New auth failed to start.")
                return

            _write_json(pending_auth_path, {
                "slug": slug,
                "issuer": issuer,
                "client_id": client_id,
                "token_url": token_url,
                "device_auth_id": device_auth_id,
                "user_code": user_code,
                "poll_interval": poll_interval,
                "created_at": time.time(),
                "expires_at": time.time() + 15 * 60,
            })

            await query.answer(text="New auth started.")
            if message is not None:
                try:
                    await message.reply_text(
                        "🔑 New Codex auth\n\n"
                        f"Profile: {slug}\n"
                        f"Open: {issuer}/codex/device\n"
                        f"Code: {user_code}\n\n"
                        "After sign-in, press Check auth.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅ Check auth", callback_data="gptprof:check_auth"),
                        ]]),
                        disable_web_page_preview=True,
                    )
                except Exception:
                    pass


        async def _check_pending_auth() -> None:
            pending = _load_json(pending_auth_path, {})
            if not isinstance(pending, dict) or not pending.get("device_auth_id"):
                await query.answer(text="No pending auth. Press New auth first.")
                return
            if time.time() > float(pending.get("expires_at") or 0):
                try:
                    os.unlink(pending_auth_path)
                except OSError:
                    pass
                await query.answer(text="Auth code expired. Press New auth again.")
                return

            slug = str(pending.get("slug") or _active_slug())
            issuer = str(pending.get("issuer") or "https://auth.openai.com")
            client_id = str(pending.get("client_id") or "app_EMoamEEZ73f0CkXaXp7hrann")
            token_url = str(pending.get("token_url") or "https://auth.openai.com/oauth/token")
            device_auth_id = str(pending.get("device_auth_id") or "")
            user_code = str(pending.get("user_code") or "")
            message = getattr(query, "message", None)

            try:
                import httpx
                async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                    poll_resp = await client.post(
                        f"{issuer}/api/accounts/deviceauth/token",
                        json={"device_auth_id": device_auth_id, "user_code": user_code},
                        headers={"Content-Type": "application/json"},
                    )
                if poll_resp.status_code in (403, 404):
                    await query.answer(text="Auth is not completed yet.")
                    return
                if poll_resp.status_code != 200:
                    await query.answer(text=f"Auth check failed: {poll_resp.status_code}")
                    return
                code_resp = poll_resp.json()
                authorization_code = str(code_resp.get("authorization_code") or "")
                code_verifier = str(code_resp.get("code_verifier") or "")
                if not authorization_code or not code_verifier:
                    await query.answer(text="Auth check returned incomplete data.")
                    return

                async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                    token_resp = await client.post(
                        token_url,
                        data={
                            "grant_type": "authorization_code",
                            "code": authorization_code,
                            "redirect_uri": f"{issuer}/deviceauth/callback",
                            "client_id": client_id,
                            "code_verifier": code_verifier,
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                if token_resp.status_code != 200:
                    await query.answer(text=f"Token exchange failed: {token_resp.status_code}")
                    return
                tokens = token_resp.json()
                access_token = str(tokens.get("access_token") or "")
                refresh_token = str(tokens.get("refresh_token") or "")
                if not access_token or not refresh_token:
                    await query.answer(text="Token exchange returned incomplete tokens.")
                    return

                _persist_new_auth(slug, access_token, refresh_token)
                try:
                    os.unlink(pending_auth_path)
                except OSError:
                    pass
                try:
                    cache = _load_json("/tmp/gptprof_usage_cache.json", {})
                    if isinstance(cache, dict):
                        cache.pop(slug, None)
                        _write_json("/tmp/gptprof_usage_cache.json", cache)
                except Exception:
                    pass
                await query.answer(text=f"Auth saved: {slug}")
                if message is not None:
                    try:
                        await message.reply_text(f"✅ New auth saved for {slug}. Refreshing usage…")
                    except Exception:
                        pass
                try:
                    subprocess.Popen(
                        [python_bin, send_script],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except Exception:
                    pass
            except Exception as exc:
                logger.error("gptprof auth check failed: %s", exc, exc_info=True)
                await query.answer(text="Auth check failed.")

        async def _switch(slug: str, model: str, *, autoswitch: bool = False) -> None:
            safe_slugs = {"gptinvest23", "markov495", "mintsage", "omnifocusme"}
            if slug not in safe_slugs:
                await query.answer(text="Unknown profile.")
                return
            profile_path = os.path.join(hcp_dir, f"{slug}.json")
            profile = _load_json(profile_path, {})
            if not isinstance(profile, dict) or not profile.get("access_token"):
                await query.answer(text="Profile token not found.")
                return

            auth = _load_json(auth_path, {})
            if not isinstance(auth, dict):
                auth = {}
            codex_entry = {
                "profile": slug,
                "plan": profile.get("plan"),
                "email": profile.get("email"),
                "access_token": profile.get("access_token"),
                "refresh_token": profile.get("refresh_token"),
            }
            auth["codex"] = codex_entry

            pool_root = auth.setdefault("credential_pool", {})
            if isinstance(pool_root, dict):
                pool = pool_root.get("openai-codex")
                if not isinstance(pool, list):
                    pool = []
                # Put the selected profile first so Codex inference uses it immediately.
                selected_source = f"gptprof:{slug}"
                existing = [c for c in pool if isinstance(c, dict) and c.get("source") != selected_source]
                selected = {
                    "id": f"gptprof-{slug}",
                    "label": profile.get("email") or slug,
                    "auth_type": "oauth",
                    "priority": 0,
                    "source": selected_source,
                    "access_token": profile.get("access_token"),
                    "refresh_token": profile.get("refresh_token"),
                    "base_url": "https://chatgpt.com/backend-api/codex",
                    "last_status": "ok",
                    "last_status_at": time.time(),
                    "request_count": 0,
                }
                for idx, item in enumerate(existing, start=1):
                    if isinstance(item, dict):
                        item["priority"] = idx
                pool_root["openai-codex"] = [selected, *existing]

            _write_json(auth_path, auth)

            try:
                import yaml as _yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = _yaml.safe_load(f) or {}
                model_cfg = cfg.get("model")
                if not isinstance(model_cfg, dict):
                    model_cfg = {"default": model}
                model_cfg["provider"] = "openai-codex"
                model_cfg["default"] = model
                model_cfg["base_url"] = "https://chatgpt.com/backend-api/codex"
                model_cfg["api_mode"] = "codex_responses"
                cfg["model"] = model_cfg
                _write_yaml(config_path, cfg)
            except Exception as exc:
                logger.error("gptprof config update failed: %s", exc, exc_info=True)
                await query.answer(text="Profile switched, config update failed.")
                return

            label = "Autoswitched" if autoswitch else "Switched"
            await query.answer(text=f"{label}: {slug}")
            try:
                await query.edit_message_text(
                    text=f"✅ {label} to {slug}\n🧠 Model: openai-codex/{model}\n\nUse /new for a fresh session.",
                    reply_markup=None,
                    parse_mode=None,
                )
            except Exception:
                pass
            try:
                subprocess.Popen(
                    [python_bin, send_script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                pass

        if data == "gptprof:close":
            await query.answer(text="Closed.")
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            return
        if data in {"gptprof:add", "gptprof:new_auth"}:
            await _start_new_auth()
            return
        if data == "gptprof:pi_route":
            await query.answer(text="Pi route stays available as fallback.")
            return
        if data == "gptprof:refresh":
            await _send_fresh_card("Usage refreshed.", clear_cache=True)
            return
        if data == "gptprof:check_auth":
            await _check_pending_auth()
            return
        if data == "gptprof:autoswitch":
            model_by_slug = {
                "gptinvest23": "gpt-5.5",
                "markov495": "gpt-5.4",
                "mintsage": "gpt-5.4-mini",
                "omnifocusme": "gpt-5.4-mini",
            }
            slug = max(model_by_slug, key=_usage_score)
            await _switch(slug, model_by_slug[slug], autoswitch=True)
            return

        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer(text="Invalid gptprof action.")
            return
        _, slug, model = parts
        await _switch(slug, model)

    async def _handle_callback_query(
        self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"
    ) -> None:
        """Handle inline keyboard button clicks."""
        query = update.callback_query
        if not query or not query.data:
            return
        data = query.data
        query_message = getattr(query, "message", None)
        query_chat_id = getattr(query_message, "chat_id", None)
        query_chat = getattr(query_message, "chat", None)
        query_chat_type = getattr(query_chat, "type", None)
        query_thread_id = getattr(query_message, "message_thread_id", None)
        query_user_name = getattr(query.from_user, "first_name", None)

        # --- GPT profile callbacks ---
        if data.startswith("gptprof:"):
            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="⛔ You are not authorized to switch GPT profiles.")
                return
            await self._handle_gptprof_callback(query, data)
            return

        # --- Model picker callbacks ---
        if data.startswith(("mp:", "mm:", "mb", "mx", "mg:")):
            chat_id = str(query.message.chat_id) if query.message else None
            if chat_id:
                await self._handle_model_picker_callback(query, data, chat_id)
            return

        # --- Exec approval callbacks (ea:choice:id) ---
        if data.startswith("ea:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                choice = parts[1]  # once, session, always, deny
                try:
                    approval_id = int(parts[2])
                except (ValueError, IndexError):
                    await query.answer(text="Invalid approval data.")
                    return

                # Only authorized users may click approval buttons.
                caller_id = str(getattr(query.from_user, "id", ""))
                if not self._is_callback_user_authorized(
                    caller_id,
                    chat_id=query_chat_id,
                    chat_type=str(query_chat_type) if query_chat_type is not None else None,
                    thread_id=str(query_thread_id) if query_thread_id is not None else None,
                    user_name=query_user_name,
                ):
                    await query.answer(text="⛔ You are not authorized to approve commands.")
                    return

                session_key = self._approval_state.pop(approval_id, None)
                if not session_key:
                    await query.answer(text="This approval has already been resolved.")
                    return

                # Map choice to human-readable label
                label_map = {
                    "once": "✅ Approved once",
                    "session": "✅ Approved for session",
                    "always": "✅ Approved permanently",
                    "deny": "❌ Denied",
                }
                user_display = getattr(query.from_user, "first_name", "User")
                label = label_map.get(choice, "Resolved")

                await query.answer(text=label)

                # Edit message to show decision, remove buttons
                try:
                    await query.edit_message_text(
                        text=f"{label} by {user_display}",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=None,
                    )
                except Exception:
                    pass  # non-fatal if edit fails

                # Resolve the approval — unblocks the agent thread
                try:
                    from tools.approval import resolve_gateway_approval
                    count = resolve_gateway_approval(session_key, choice)
                    logger.info(
                        "Telegram button resolved %d approval(s) for session %s (choice=%s, user=%s)",
                        count, session_key, choice, user_display,
                    )
                except Exception as exc:
                    logger.error("Failed to resolve gateway approval from Telegram button: %s", exc)
            return

        # --- Slash-confirm callbacks (sc:choice:confirm_id) ---
        if data.startswith("sc:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                choice = parts[1]  # once, always, cancel
                confirm_id = parts[2]

                caller_id = str(getattr(query.from_user, "id", ""))
                if not self._is_callback_user_authorized(
                    caller_id,
                    chat_id=query_chat_id,
                    chat_type=str(query_chat_type) if query_chat_type is not None else None,
                    thread_id=str(query_thread_id) if query_thread_id is not None else None,
                    user_name=query_user_name,
                ):
                    await query.answer(text="⛔ You are not authorized to answer this prompt.")
                    return

                session_key = self._slash_confirm_state.pop(confirm_id, None)
                if not session_key:
                    await query.answer(text="This prompt has already been resolved.")
                    return

                label_map = {
                    "once": "✅ Approved once",
                    "always": "🔒 Always approve",
                    "cancel": "❌ Cancelled",
                }
                user_display = getattr(query.from_user, "first_name", "User")
                label = label_map.get(choice, "Resolved")

                await query.answer(text=label)

                try:
                    await query.edit_message_text(
                        text=f"{label} by {user_display}",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=None,
                    )
                except Exception:
                    pass

                # Resolve via the module-level primitive.  The runner stored
                # a handler keyed by session_key; we run it on the event
                # loop and (if it returns a string) send it as a follow-up
                # message in the same chat.
                try:
                    from tools import slash_confirm as _slash_confirm_mod
                    result_text = await _slash_confirm_mod.resolve(
                        session_key, confirm_id, choice,
                    )
                    if result_text and query.message:
                        # Inherit the prompt message's topic. Supergroup forums
                        # use message_thread_id; Telegram private DM-topic lanes
                        # need both the private topic id and the prompt reply anchor.
                        thread_id = getattr(query.message, "message_thread_id", None)
                        chat = getattr(query.message, "chat", None)
                        chat_type = getattr(chat, "type", None)
                        prompt_message_id = getattr(query.message, "message_id", None)
                        send_kwargs: Dict[str, Any] = {
                            "chat_id": int(query.message.chat_id),
                            "text": result_text,
                            "parse_mode": ParseMode.MARKDOWN,
                            **self._link_preview_kwargs(),
                        }
                        chat_type_value = getattr(chat_type, "value", chat_type)
                        is_private_chat = str(chat_type_value).lower() in {
                            "private",
                            str(ChatType.PRIVATE).lower(),
                            str(getattr(ChatType.PRIVATE, "value", ChatType.PRIVATE)).lower(),
                        }
                        if thread_id is not None and is_private_chat and prompt_message_id is not None:
                            reply_to_id = int(prompt_message_id)
                            send_kwargs["reply_to_message_id"] = reply_to_id
                            send_kwargs.update(
                                self._thread_kwargs_for_send(
                                    str(query.message.chat_id),
                                    str(thread_id),
                                    {
                                        "thread_id": str(thread_id),
                                        "telegram_dm_topic_reply_fallback": True,
                                    },
                                    reply_to_message_id=reply_to_id,
                                )
                            )
                        elif thread_id is not None:
                            send_kwargs.update(
                                self._thread_kwargs_for_send(
                                    str(query.message.chat_id),
                                    str(thread_id),
                                    {"thread_id": str(thread_id)},
                                )
                            )
                        await self._bot.send_message(**send_kwargs)
                except Exception as exc:
                    logger.error("[%s] slash-confirm callback failed: %s", self.name, exc, exc_info=True)
            return

        # --- Update prompt callbacks ---
        if not data.startswith("update_prompt:"):
            return
        answer = data.split(":", 1)[1]  # "y" or "n"
        caller_id = str(getattr(query.from_user, "id", ""))
        if not self._is_callback_user_authorized(
            caller_id,
            chat_id=query_chat_id,
            chat_type=str(query_chat_type) if query_chat_type is not None else None,
            thread_id=str(query_thread_id) if query_thread_id is not None else None,
            user_name=query_user_name,
        ):
            await query.answer(text="⛔ You are not authorized to answer update prompts.")
            return
        await query.answer(text=f"Sent '{answer}' to the update process.")
        # Edit the message to show the choice and remove buttons
        label = "Yes" if answer == "y" else "No"
        try:
            await query.edit_message_text(
                text=f"⚕ Update prompt answered: *{label}*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=None,
            )
        except Exception:
            pass  # non-fatal if edit fails
        # Write the response file
        try:
            from hermes_constants import get_hermes_home
            home = get_hermes_home()
            response_path = home / ".update_response"
            tmp = response_path.with_suffix(".tmp")
            tmp.write_text(answer)
            tmp.replace(response_path)
            logger.info("Telegram update prompt answered '%s' by user %s",
                        answer, getattr(query.from_user, "id", "unknown"))
        except Exception as exc:
            logger.error("Failed to write update response from callback: %s", exc)

    def _missing_media_path_error(self, label: str, path: str) -> str:
        """Build an actionable file-not-found error for gateway MEDIA delivery.

        Paths like /workspace/... or /output/... often only exist inside the
        Docker sandbox, while the gateway process runs on the host.
        """
        error = f"{label} file not found: {path}"
        if path.startswith(("/workspace/", "/output/", "/outputs/")):
            error += (
                " (path may only exist inside the Docker sandbox. "
                "Bind-mount a host directory and emit the host-visible "
                "path in MEDIA: for gateway file delivery.)"
            )
        return error

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send audio as a native Telegram voice message or audio file."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        
        try:
            if not os.path.exists(audio_path):
                return SendResult(success=False, error=self._missing_media_path_error("Audio", audio_path))
            
            with open(audio_path, "rb") as audio_file:
                ext = os.path.splitext(audio_path)[1].lower()
                # .ogg / .opus files -> send as voice (round playable bubble)
                if ext in (".ogg", ".opus"):
                    _voice_thread = self._metadata_thread_id(metadata)
                    reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata)
                    voice_thread_kwargs = self._thread_kwargs_for_send(
                        chat_id,
                        _voice_thread,
                        metadata,
                        reply_to_message_id=reply_to_id,
                    )
                    msg = await self._send_with_dm_topic_reply_anchor_retry(
                        self._bot.send_voice,
                        {
                            "chat_id": int(chat_id),
                            "voice": audio_file,
                            "caption": caption[:1024] if caption else None,
                            "reply_to_message_id": reply_to_id,
                            "reply_markup": self._human20_inline_markup(chat_id, metadata),
                            **voice_thread_kwargs,
                            **self._notification_kwargs(metadata),
                        },
                        metadata,
                        reply_to_id,
                        "voice",
                        reset_media=lambda: audio_file.seek(0),
                    )
                elif ext in (".mp3", ".m4a"):
                    # Telegram's Bot API sendAudio only accepts MP3 / M4A.
                    _audio_thread = self._metadata_thread_id(metadata)
                    reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata)
                    audio_thread_kwargs = self._thread_kwargs_for_send(
                        chat_id,
                        _audio_thread,
                        metadata,
                        reply_to_message_id=reply_to_id,
                    )
                    msg = await self._send_with_dm_topic_reply_anchor_retry(
                        self._bot.send_audio,
                        {
                            "chat_id": int(chat_id),
                            "audio": audio_file,
                            "caption": caption[:1024] if caption else None,
                            "reply_to_message_id": reply_to_id,
                            "reply_markup": self._human20_inline_markup(chat_id, metadata),
                            **audio_thread_kwargs,
                            **self._notification_kwargs(metadata),
                        },
                        metadata,
                        reply_to_id,
                        "audio",
                        reset_media=lambda: audio_file.seek(0),
                    )
                else:
                    # Formats Telegram can't play natively (.wav, .flac, ...)
                    # — fall back to document delivery instead of raising.
                    return await self.send_document(
                        chat_id=chat_id,
                        file_path=audio_path,
                        caption=caption,
                        reply_to=reply_to,
                        metadata=metadata,
                    )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.error(
                "[%s] Failed to send Telegram voice/audio, falling back to base adapter: %s",
                self.name,
                e,
                exc_info=True,
            )
            return await super().send_voice(chat_id, audio_path, caption, reply_to, metadata=metadata)

    async def send_multiple_images(
        self,
        chat_id: str,
        images: List[tuple],
        metadata: Optional[Dict[str, Any]] = None,
        human_delay: float = 0.0,
    ) -> None:
        """Send a batch of images natively via Telegram's media group API.

        Telegram's ``send_media_group`` bundles up to 10 photos/videos into
        a single album. Larger batches are chunked. Animated GIFs cannot
        go into a media group (they require ``send_animation``), so they
        are peeled off and sent individually via the base default path.

        URL-based photos go into the group directly; local files are
        opened as byte streams. On failure the whole batch falls back to
        the base adapter's per-image loop.
        """
        if not self._bot:
            return
        if not images:
            return

        try:
            from telegram import InputMediaPhoto
        except Exception as exc:  # pragma: no cover - missing SDK
            logger.warning(
                "[%s] InputMediaPhoto unavailable, falling back to per-image send: %s",
                self.name, exc,
            )
            await super().send_multiple_images(chat_id, images, metadata, human_delay)
            return

        # Peel off animations — they need send_animation, not send_media_group
        animations: List[tuple] = []
        photos: List[tuple] = []
        for image_url, alt_text in images:
            if not image_url.startswith("file://") and self._is_animation_url(image_url):
                animations.append((image_url, alt_text))
            else:
                photos.append((image_url, alt_text))

        # Animations: route through the base default (per-image send_animation)
        if animations:
            await super().send_multiple_images(
                chat_id, animations, metadata, human_delay=human_delay,
            )

        if not photos:
            return

        from urllib.parse import unquote as _unquote
        _thread = self._metadata_thread_id(metadata)

        # Chunk into groups of 10 (Telegram's album limit)
        CHUNK = 10
        chunks = [photos[i:i + CHUNK] for i in range(0, len(photos), CHUNK)]

        for chunk_idx, chunk in enumerate(chunks):
            if human_delay > 0 and chunk_idx > 0:
                await asyncio.sleep(human_delay)

            media: List[Any] = []
            opened_files: List[Any] = []
            try:
                for image_url, alt_text in chunk:
                    caption = alt_text[:1024] if alt_text else None
                    if image_url.startswith("file://"):
                        local_path = _unquote(image_url[7:])
                        if not os.path.exists(local_path):
                            logger.warning(
                                "[%s] Skipping missing image in media group: %s",
                                self.name, local_path,
                            )
                            continue
                        fh = open(local_path, "rb")
                        opened_files.append(fh)
                        media.append(InputMediaPhoto(media=fh, caption=caption))
                    else:
                        media.append(InputMediaPhoto(media=image_url, caption=caption))

                if not media:
                    continue

                logger.info(
                    "[%s] Sending media group of %d photo(s) (chunk %d/%d)",
                    self.name, len(media), chunk_idx + 1, len(chunks),
                )
                reply_to_id = self._reply_to_message_id_for_send(None, metadata)
                thread_kwargs = self._thread_kwargs_for_send(
                    chat_id,
                    _thread,
                    metadata,
                    reply_to_message_id=reply_to_id,
                )

                def _reset_opened_files() -> None:
                    for fh in opened_files:
                        try:
                            fh.seek(0)
                        except Exception:
                            pass

                await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_media_group,
                    {
                        "chat_id": int(chat_id),
                        "media": media,
                        "reply_to_message_id": reply_to_id,
                        **thread_kwargs,
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "media group",
                    reset_media=_reset_opened_files,
                )
            except Exception as e:
                logger.warning(
                    "[%s] send_media_group failed (chunk %d/%d), falling back to per-image: %s",
                    self.name, chunk_idx + 1, len(chunks), e,
                    exc_info=True,
                )
                # Fallback: send each photo in this chunk individually
                await super().send_multiple_images(
                    chat_id, chunk, metadata, human_delay=human_delay,
                )
            finally:
                for fh in opened_files:
                    try:
                        fh.close()
                    except Exception:
                        pass

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a local image file natively as a Telegram photo."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            if not os.path.exists(image_path):
                return SendResult(success=False, error=self._missing_media_path_error("Image", image_path))

            _thread = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata)
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _thread,
                metadata,
                reply_to_message_id=reply_to_id,
            )
            with open(image_path, "rb") as image_file:
                msg = await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_photo,
                    {
                        "chat_id": int(chat_id),
                        "photo": image_file,
                        "caption": caption[:1024] if caption else None,
                        "reply_to_message_id": reply_to_id,
                        **thread_kwargs,
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "photo",
                    reset_media=lambda: image_file.seek(0),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            error_str = str(e)
            # Dimension-related errors are the expected case for valid image
            # files that Telegram just refuses as photos (screenshots, extreme
            # aspect ratios). Log at INFO because the document fallback is
            # the correct path. Any other send_photo failure also falls back
            # to document (rate limits, corrupt file markers, format edge
            # cases), but at WARNING because it's unexpected and worth
            # surfacing in logs.
            is_dim_error = (
                "Photo_invalid_dimensions" in error_str
                or "PHOTO_INVALID_DIMENSIONS" in error_str
            )
            if is_dim_error:
                logger.info(
                    "[%s] Image dimensions exceed Telegram photo limits, "
                    "sending as document: %s",
                    self.name,
                    image_path,
                )
            else:
                logger.warning(
                    "[%s] Failed to send Telegram local image as photo, "
                    "trying document fallback: %s",
                    self.name,
                    e,
                    exc_info=True,
                )
            # Fallback to sending as document (file) — no dimension limit,
            # only 50MB size limit. If even that fails, fall back to the
            # base adapter's text-only "Image: /path" rendering.
            try:
                return await self.send_document(
                    chat_id=chat_id,
                    file_path=image_path,
                    caption=caption,
                    file_name=os.path.basename(image_path),
                    reply_to=reply_to,
                    metadata=metadata,
                )
            except Exception as doc_err:
                logger.error(
                    "[%s] Failed to send Telegram local image as document, "
                    "falling back to base adapter: %s",
                    self.name,
                    doc_err,
                    exc_info=True,
                )
                return await super().send_image_file(chat_id, image_path, caption, reply_to, metadata=metadata)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a document/file natively as a Telegram file attachment."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            if not os.path.exists(file_path):
                return SendResult(success=False, error=self._missing_media_path_error("File", file_path))

            display_name = file_name or os.path.basename(file_path)
            _thread = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata)
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _thread,
                metadata,
                reply_to_message_id=reply_to_id,
            )

            with open(file_path, "rb") as f:
                msg = await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_document,
                    {
                        "chat_id": int(chat_id),
                        "document": f,
                        "filename": display_name,
                        "caption": caption[:1024] if caption else None,
                        "reply_to_message_id": reply_to_id,
                        "reply_markup": self._human20_inline_markup(chat_id, metadata),
                        **thread_kwargs,
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "document",
                    reset_media=lambda: f.seek(0),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            print(f"[{self.name}] Failed to send document: {e}")
            return await super().send_document(chat_id, file_path, caption, file_name, reply_to, metadata=metadata)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a video natively as a Telegram video message."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            if not os.path.exists(video_path):
                return SendResult(success=False, error=self._missing_media_path_error("Video", video_path))

            _thread = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata)
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _thread,
                metadata,
                reply_to_message_id=reply_to_id,
            )
            with open(video_path, "rb") as f:
                msg = await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_video,
                    {
                        "chat_id": int(chat_id),
                        "video": f,
                        "caption": caption[:1024] if caption else None,
                        "reply_to_message_id": reply_to_id,
                        "reply_markup": self._human20_inline_markup(chat_id, metadata),
                        **thread_kwargs,
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "video",
                    reset_media=lambda: f.seek(0),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            print(f"[{self.name}] Failed to send video: {e}")
            return await super().send_video(chat_id, video_path, caption, reply_to, metadata=metadata)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an image natively as a Telegram photo.
        
        Tries URL-based send first (fast, works for <5MB images).
        Falls back to downloading and uploading as file (supports up to 10MB).
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        from tools.url_safety import is_safe_url
        if not is_safe_url(image_url):
            logger.warning("[%s] Blocked unsafe image URL (SSRF protection)", self.name)
            return await super().send_image(chat_id, image_url, caption, reply_to, metadata=metadata)

        try:
            # Telegram can send photos directly from URLs (up to ~5MB)
            _photo_thread = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata)
            photo_thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _photo_thread,
                metadata,
                reply_to_message_id=reply_to_id,
            )
            msg = await self._send_with_dm_topic_reply_anchor_retry(
                self._bot.send_photo,
                {
                    "chat_id": int(chat_id),
                    "photo": image_url,
                    "caption": caption[:1024] if caption else None,
                    "reply_to_message_id": reply_to_id,
                    "reply_markup": self._human20_inline_markup(chat_id, metadata),
                    **photo_thread_kwargs,
                    **self._notification_kwargs(metadata),
                },
                metadata,
                reply_to_id,
                "URL photo",
            )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning(
                "[%s] URL-based send_photo failed, trying file upload: %s",
                self.name,
                e,
                exc_info=True,
            )
            # Fallback: download and upload as file (supports up to 10MB)
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(image_url)
                    resp.raise_for_status()
                    image_data = resp.content

                upload_thread_kwargs = self._thread_kwargs_for_send(
                    chat_id,
                    _photo_thread,
                    metadata,
                    reply_to_message_id=reply_to_id,
                )
                msg = await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_photo,
                    {
                        "chat_id": int(chat_id),
                        "photo": image_data,
                        "caption": caption[:1024] if caption else None,
                        "reply_to_message_id": reply_to_id,
                        "reply_markup": self._human20_inline_markup(chat_id, metadata),
                        **upload_thread_kwargs,
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "uploaded photo",
                )
                return SendResult(success=True, message_id=str(msg.message_id))
            except Exception as e2:
                logger.error(
                    "[%s] File upload send_photo also failed: %s",
                    self.name,
                    e2,
                    exc_info=True,
                )
                # Final fallback: send URL as text
                return await super().send_image(chat_id, image_url, caption, reply_to, metadata=metadata)

    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an animated GIF natively as a Telegram animation (auto-plays inline)."""
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        
        try:
            _anim_thread = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata)
            animation_thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _anim_thread,
                metadata,
                reply_to_message_id=reply_to_id,
            )
            msg = await self._send_with_dm_topic_reply_anchor_retry(
                self._bot.send_animation,
                {
                    "chat_id": int(chat_id),
                    "animation": animation_url,
                    "caption": caption[:1024] if caption else None,
                    "reply_to_message_id": reply_to_id,
                    "reply_markup": self._human20_inline_markup(chat_id, metadata),
                    **animation_thread_kwargs,
                    **self._notification_kwargs(metadata),
                },
                metadata,
                reply_to_id,
                "animation",
            )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.error(
                "[%s] Failed to send Telegram animation, falling back to photo: %s",
                self.name,
                e,
                exc_info=True,
            )
            # Fallback: try as a regular photo
            return await self.send_image(chat_id, animation_url, caption, reply_to, metadata=metadata)

    async def send_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Send typing indicator."""
        if self._bot:
            try:
                _typing_thread = self._metadata_thread_id(metadata)
                # Skip the Bot API call entirely for Hermes-created DM topic
                # lanes: send_chat_action only accepts message_thread_id, which
                # Telegram's Bot API 10.0 rejects for these lanes. The send
                # path uses the reply-anchor fallback instead, but typing has
                # no equivalent — skipping avoids noisy "thread not found"
                # debug logs on every typing tick.
                if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
                    return
                message_thread_id = self._message_thread_id_for_typing(_typing_thread)
                # No retry-without-thread fallback here: _message_thread_id_for_typing
                # already maps the forum General topic to None, so any non-None value
                # reaching this call is a user-created topic. If Telegram rejects it
                # (e.g. topic deleted mid-session), we swallow the failure rather than
                # showing a typing indicator in the wrong chat/All Messages.
                await self._bot.send_chat_action(
                    chat_id=int(chat_id),
                    action="typing",
                    message_thread_id=message_thread_id,
                    **self._business_kwargs(metadata),
                )
            except Exception as e:
                # Typing failures are non-fatal; log at debug level only.
                logger.debug(
                    "[%s] Failed to send Telegram typing indicator: %s",
                    self.name,
                    e,
                    exc_info=True,
                )

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get information about a Telegram chat."""
        if not self._bot:
            return {"name": "Unknown", "type": "dm"}
        
        try:
            chat = await self._bot.get_chat(int(chat_id))
            
            chat_type = "dm"
            if chat.type == ChatType.GROUP:
                chat_type = "group"
            elif chat.type == ChatType.SUPERGROUP:
                chat_type = "group"
                if chat.is_forum:
                    chat_type = "forum"
            elif chat.type == ChatType.CHANNEL:
                chat_type = "channel"
            
            return {
                "name": chat.title or chat.full_name or str(chat_id),
                "type": chat_type,
                "username": chat.username,
                "is_forum": getattr(chat, "is_forum", False),
            }
        except Exception as e:
            logger.error(
                "[%s] Failed to get Telegram chat info for %s: %s",
                self.name,
                chat_id,
                e,
                exc_info=True,
            )
            return {"name": str(chat_id), "type": "dm", "error": str(e)}

    def format_message(self, content: str) -> str:
        """
        Convert standard markdown to Telegram MarkdownV2 format.

        Protected regions (code blocks, inline code) are extracted first so
        their contents are never modified.  Standard markdown constructs
        (headers, bold, italic, links) are translated to MarkdownV2 syntax,
        and all remaining special characters are escaped.
        """
        if not content:
            return content

        placeholders: dict = {}
        counter = [0]

        def _ph(value: str) -> str:
            """Stash *value* behind a placeholder token that survives escaping."""
            key = f"\x00PH{counter[0]}\x00"
            counter[0] += 1
            placeholders[key] = value
            return key

        text = content

        # 0) Rewrite GFM-style pipe tables into Telegram-friendly row groups
        #    before the normal MarkdownV2 conversions run.
        text = _wrap_markdown_tables(text)

        # 1) Protect fenced code blocks (``` ... ```)
        #    Per MarkdownV2 spec, \ and ` inside pre/code must be escaped.
        def _protect_fenced(m):
            raw = m.group(0)
            # Split off opening ``` (with optional language) and closing ```
            open_end = raw.index('\n') + 1 if '\n' in raw[3:] else 3
            opening = raw[:open_end]
            body_and_close = raw[open_end:]
            body = body_and_close[:-3]
            body = body.replace('\\', '\\\\').replace('`', '\\`')
            return _ph(opening + body + '```')

        text = re.sub(
            r'(```(?:[^\n]*\n)?[\s\S]*?```)',
            _protect_fenced,
            text,
        )

        # 2) Protect inline code (`...`)
        #    Escape \ inside inline code per MarkdownV2 spec.
        text = re.sub(
            r'(`[^`]+`)',
            lambda m: _ph(m.group(0).replace('\\', '\\\\')),
            text,
        )

        # 3) Convert markdown links – escape the display text; inside the URL
        #    only ')' and '\' need escaping per the MarkdownV2 spec.
        def _convert_link(m):
            display = _escape_mdv2(m.group(1))
            url = m.group(2).replace('\\', '\\\\').replace(')', '\\)')
            return _ph(f'[{display}]({url})')

        text = re.sub(r'\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)', _convert_link, text)

        # 4) Convert markdown headers (## Title) → bold *Title*
        def _convert_header(m):
            inner = m.group(1).strip()
            # Strip redundant bold markers that may appear inside a header
            inner = re.sub(r'\*\*(.+?)\*\*', r'\1', inner)
            return _ph(f'*{_escape_mdv2(inner)}*')

        text = re.sub(
            r'^#{1,6}\s+(.+)$', _convert_header, text, flags=re.MULTILINE
        )

        # 5) Convert bold: **text** → *text* (MarkdownV2 bold)
        text = re.sub(
            r'\*\*(.+?)\*\*',
            lambda m: _ph(f'*{_escape_mdv2(m.group(1))}*'),
            text,
        )

        # 6) Convert italic: *text* (single asterisk) → _text_ (MarkdownV2 italic)
        #    [^*\n]+ prevents matching across newlines (which would corrupt
        #    bullet lists using * markers and multi-line content).
        text = re.sub(
            r'\*([^*\n]+)\*',
            lambda m: _ph(f'_{_escape_mdv2(m.group(1))}_'),
            text,
        )

        # 7) Convert strikethrough: ~~text~~ → ~text~ (MarkdownV2)
        text = re.sub(
            r'~~(.+?)~~',
            lambda m: _ph(f'~{_escape_mdv2(m.group(1))}~'),
            text,
        )

        # 8) Convert spoiler: ||text|| → ||text|| (protect from | escaping)
        text = re.sub(
            r'\|\|(.+?)\|\|',
            lambda m: _ph(f'||{_escape_mdv2(m.group(1))}||'),
            text,
        )

        # 9) Convert blockquotes: > at line start → protect > from escaping
        #    Handle both regular blockquotes (> text) and expandable blockquotes
        #    (Telegram MarkdownV2: **> for expandable start, || to end the quote)
        def _convert_blockquote(m):
            prefix = m.group(1)  # >, >>, >>>, **>, or **>> etc.
            content = m.group(2)
            # Check if content ends with || (expandable blockquote end marker)
            # In this case, preserve the trailing || unescaped for Telegram
            if prefix.startswith('**') and content.endswith('||'):
                return _ph(f'{prefix} {_escape_mdv2(content[:-2])}||')
            return _ph(f'{prefix} {_escape_mdv2(content)}')

        text = re.sub(
            r'^((?:\*\*)?>{1,3}) (.+)$',
            _convert_blockquote,
            text,
            flags=re.MULTILINE,
        )

        # 10) Escape remaining special characters in plain text
        text = _escape_mdv2(text)

        # 11) Restore placeholders in reverse insertion order so that
        #    nested references (a placeholder inside another) resolve correctly.
        for key in reversed(list(placeholders.keys())):
            text = text.replace(key, placeholders[key])

        # 12) Safety net: escape unescaped ( ) { } that slipped through
        #     placeholder processing.  Split the text into code/non-code
        #     segments so we never touch content inside ``` or ` spans.
        _code_split = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
        _safe_parts = []
        for _idx, _seg in enumerate(_code_split):
            if _idx % 2 == 1:
                # Inside code span/block — leave untouched
                _safe_parts.append(_seg)
            else:
                # Outside code — escape bare ( ) { }
                def _esc_bare(m, _seg=_seg):
                    s = m.start()
                    ch = m.group(0)
                    # Already escaped
                    if s > 0 and _seg[s - 1] == '\\':
                        return ch
                    # ( that opens a MarkdownV2 link [text](url)
                    if ch == '(' and s > 0 and _seg[s - 1] == ']':
                        return ch
                    # ) that closes a link URL
                    if ch == ')':
                        before = _seg[:s]
                        if '](http' in before or '](' in before:
                            # Check depth
                            depth = 0
                            for j in range(s - 1, max(s - 2000, -1), -1):
                                if _seg[j] == '(':
                                    depth -= 1
                                    if depth < 0:
                                        if j > 0 and _seg[j - 1] == ']':
                                            return ch
                                        break
                                elif _seg[j] == ')':
                                    depth += 1
                    return '\\' + ch
                _safe_parts.append(re.sub(r'[(){}]', _esc_bare, _seg))
        text = ''.join(_safe_parts)

        return text

    # ── Group mention gating ──────────────────────────────────────────────

    def _telegram_require_mention(self) -> bool:
        """Return whether group chats should require an explicit bot trigger."""
        configured = self.config.extra.get("require_mention")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in ("true", "1", "yes", "on")
            return bool(configured)
        return os.getenv("TELEGRAM_REQUIRE_MENTION", "false").lower() in ("true", "1", "yes", "on")

    def _telegram_guest_mode(self) -> bool:
        """Return whether non-allowlisted groups may trigger via direct @mention."""
        configured = self.config.extra.get("guest_mode")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in ("true", "1", "yes", "on")
            return bool(configured)
        return os.getenv("TELEGRAM_GUEST_MODE", "false").lower() in ("true", "1", "yes", "on")

    def _telegram_free_response_chats(self) -> set[str]:
        raw = self.config.extra.get("free_response_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_FREE_RESPONSE_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_require_mention_chats(self) -> set[str]:
        """Return group chat IDs that require an explicit bot trigger.

        This is the per-chat counterpart to the global ``require_mention``
        switch. It lets one noisy group/topic behave as mention/reply-only
        without changing the default free-response behavior in other chats.
        """
        raw = self.config.extra.get("require_mention_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_REQUIRE_MENTION_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_allowed_chats(self) -> set[str]:
        """Return the whitelist of group/supergroup chat IDs the bot will respond in.

        When non-empty, group messages from chats NOT in this set are
        silently ignored unless ``guest_mode`` is enabled and the bot is
        explicitly @mentioned.  DMs are never filtered.
        Empty set means no restriction (fully backward compatible).
        """
        raw = self.config.extra.get("allowed_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_ALLOWED_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_ignored_threads(self) -> set[int]:
        raw = self.config.extra.get("ignored_threads")
        if raw is None:
            raw = os.getenv("TELEGRAM_IGNORED_THREADS", "")

        if isinstance(raw, list):
            values = raw
        else:
            values = str(raw).split(",")

        ignored: set[int] = set()
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            try:
                ignored.add(int(text))
            except (TypeError, ValueError):
                logger.warning("[%s] Ignoring invalid Telegram thread id: %r", self.name, value)
        return ignored

    def _compile_mention_patterns(self) -> List[re.Pattern]:
        """Compile optional regex wake-word patterns for group triggers."""
        patterns = self.config.extra.get("mention_patterns")
        if patterns is None:
            raw = os.getenv("TELEGRAM_MENTION_PATTERNS", "").strip()
            if raw:
                try:
                    loaded = json.loads(raw)
                except Exception:
                    loaded = [part.strip() for part in raw.splitlines() if part.strip()]
                    if not loaded:
                        loaded = [part.strip() for part in raw.split(",") if part.strip()]
                patterns = loaded

        if patterns is None:
            return []
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list):
            logger.warning(
                "[%s] telegram mention_patterns must be a list or string; got %s",
                self.name,
                type(patterns).__name__,
            )
            return []

        compiled: List[re.Pattern] = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                logger.warning("[%s] Invalid Telegram mention pattern %r: %s", self.name, pattern, exc)
        if compiled:
            logger.info("[%s] Loaded %d Telegram mention pattern(s)", self.name, len(compiled))
        return compiled

    def _is_group_chat(self, message: Message) -> bool:
        chat = getattr(message, "chat", None)
        if not chat:
            return False
        chat_type = str(getattr(chat, "type", "")).split(".")[-1].lower()
        return chat_type in ("group", "supergroup")

    def _is_reply_to_bot(self, message: Message) -> bool:
        if not self._bot or not getattr(message, "reply_to_message", None):
            return False
        reply_user = getattr(message.reply_to_message, "from_user", None)
        return bool(reply_user and getattr(reply_user, "id", None) == getattr(self._bot, "id", None))

    def _message_mentions_bot(self, message: Message) -> bool:
        if not self._bot:
            return False

        bot_username = (getattr(self._bot, "username", None) or "").lstrip("@").lower()
        bot_id = getattr(self._bot, "id", None)
        expected = f"@{bot_username}" if bot_username else None

        def _iter_sources():
            yield getattr(message, "text", None) or "", getattr(message, "entities", None) or []
            yield getattr(message, "caption", None) or "", getattr(message, "caption_entities", None) or []

        # Telegram parses mentions server-side and emits MessageEntity objects
        # (type=mention for @username, type=text_mention for @FirstName targeting
        # a user without a public username). Only those entities are authoritative —
        # raw substring matches like "foo@hermes_bot.example" are not mentions
        # (bug #12545). Entities also correctly handle @handles inside URLs, code
        # blocks, and quoted text, where a regex scan would over-match.
        for source_text, entities in _iter_sources():
            for entity in entities:
                entity_type = str(getattr(entity, "type", "")).split(".")[-1].lower()
                if entity_type == "mention" and expected:
                    offset = int(getattr(entity, "offset", -1))
                    length = int(getattr(entity, "length", 0))
                    if offset < 0 or length <= 0:
                        continue
                    if source_text[offset:offset + length].strip().lower() == expected:
                        return True
                elif entity_type == "text_mention":
                    user = getattr(entity, "user", None)
                    if user and getattr(user, "id", None) == bot_id:
                        return True
                elif entity_type == "bot_command" and expected:
                    # Telegram's official group-disambiguation form for slash
                    # commands (``/cmd@botname``) is emitted as a single
                    # ``bot_command`` entity covering the whole span — there
                    # is no accompanying ``mention`` entity. Treat it as a
                    # direct address to this bot when the ``@botname`` suffix
                    # matches. This is the form Telegram's own command menu
                    # autocomplete produces in groups, so dropping it at the
                    # mention gate would break /new, /reset, /help, ... for
                    # every group that has ``require_mention`` enabled (#15415).
                    offset = int(getattr(entity, "offset", -1))
                    length = int(getattr(entity, "length", 0))
                    if offset < 0 or length <= 0:
                        continue
                    command_text = source_text[offset:offset + length]
                    at_index = command_text.find("@")
                    if at_index < 0:
                        continue
                    if command_text[at_index:].strip().lower() == expected:
                        return True
        return False

    def _message_matches_mention_patterns(self, message: Message) -> bool:
        if not self._mention_patterns:
            return False
        for candidate in (getattr(message, "text", None), getattr(message, "caption", None)):
            if not candidate:
                continue
            for pattern in self._mention_patterns:
                if pattern.search(candidate):
                    return True
        return False

    @staticmethod
    def _message_matches_sigurd_trigger(message: Message) -> bool:
        for candidate in (getattr(message, "text", None), getattr(message, "caption", None)):
            if candidate and re.search(r"(?i)\bsigurd\b", candidate):
                return True
        return False

    def _cache_observed_chat_type(self, chat_id: str, chat_type: str) -> None:
        chat_type = str(chat_type or "").strip().lower()
        if not chat_type:
            return
        cache = getattr(self, "_chat_type_cache", None)
        if cache is None:
            cache = {}
            self._chat_type_cache = cache
        cache[str(chat_id)] = chat_type

    def _outbound_chat_type(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
            return "dm"
        cache = getattr(self, "_chat_type_cache", None) or {}
        return cache.get(str(chat_id))

    def _human20_inline_markup(
        self,
        chat_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        reply_markup: Optional[Any] = None,
    ) -> Optional[Any]:
        metadata = metadata or {}
        if self._outbound_chat_type(chat_id, metadata) != "dm":
            return reply_markup
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        except Exception:
            return reply_markup

        button = InlineKeyboardButton(_HUMAN20_CTA_TEXT, url=_HUMAN20_CTA_URL)
        if reply_markup is None:
            return InlineKeyboardMarkup([[button]])

        try:
            rows = [list(row) for row in getattr(reply_markup, "inline_keyboard", [])]
        except Exception:
            rows = []
        rows.append([button])
        return InlineKeyboardMarkup(rows)

    def _is_guest_mention(self, message: Message) -> bool:
        """Return True for the narrow guest-mode bypass: explicit bot mention.

        The caller (:meth:`_should_process_message`) has already verified
        the message is a group chat, so that check is not repeated here.
        """
        return self._telegram_guest_mode() and self._message_mentions_bot(message)

    def _clean_bot_trigger_text(self, text: Optional[str]) -> Optional[str]:
        if not text or not self._bot or not getattr(self._bot, "username", None):
            return text
        username = re.escape(self._bot.username)
        cleaned = re.sub(rf"(?i)@{username}\b[,:\-]*\s*", "", text).strip()
        return cleaned or text

    def _should_process_message(self, message: Message, *, is_command: bool = False) -> bool:
        """Apply Telegram group trigger rules.

        DMs require a direct trigger: reply, explicit @mention, or the
        configured Sigurd wake word. Group/supergroup messages are accepted when:
        - the chat passes the ``allowed_chats`` whitelist (when set), or
          ``guest_mode`` is enabled and the bot is explicitly mentioned
        - the chat is explicitly listed in ``require_mention_chats`` and the
          message replies to the bot, @mentions the bot, or matches a configured
          regex wake-word pattern
        - the chat is explicitly allowlisted in ``free_response_chats``
        - ``require_mention`` is disabled
        - the message replies to the bot
        - the bot is @mentioned
        - the text/caption matches a configured regex wake-word pattern

        When ``allowed_chats`` is non-empty, it remains a hard gate except for
        the narrow ``guest_mode`` bypass: group/supergroup messages that
        explicitly @mention this bot. Replies and regex wake words do not bypass
        ``allowed_chats``. When ``require_mention`` is enabled, slash commands are not given
        special treatment — they must pass the same mention/reply checks
        as any other group message.  Users can still trigger commands via
        the Telegram bot menu (``/command@botname``) or by explicitly
        mentioning the bot (``@botname /command``), both of which are
        recognised as mentions by :meth:`_message_mentions_bot`.
        """
        if not self._is_group_chat(message):
            sender = getattr(message, "from_user", None)
            sender_id = str(getattr(sender, "id", "") or "").strip()
            allowed_csv = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
            allowed_ids = {part.strip() for part in allowed_csv.split(",") if part.strip()}
            if sender_id and ("*" in allowed_ids or sender_id in allowed_ids):
                return True
            return (
                bool(getattr(message, "reply_to_message", None))
                or self._message_mentions_bot(message)
                or self._message_matches_sigurd_trigger(message)
            )

        thread_id = getattr(message, "message_thread_id", None)
        if thread_id is not None:
            try:
                if int(thread_id) in self._telegram_ignored_threads():
                    return False
            except (TypeError, ValueError):
                logger.warning("[%s] Ignoring non-numeric Telegram message_thread_id: %r", self.name, thread_id)

        chat_id_str = str(getattr(getattr(message, "chat", None), "id", ""))

        # Resolve guest-mode mention bypass once so _message_mentions_bot
        # is not called redundantly in the normal flow below.
        guest_mention = self._is_guest_mention(message)

        # allowed_chats check (whitelist). When set, group messages from chats
        # outside the whitelist are ignored unless guest_mode permits this
        # exact message as an explicit direct mention. DMs are excluded above.
        allowed = self._telegram_allowed_chats()
        if allowed and chat_id_str not in allowed:
            return guest_mention

        if guest_mention:
            return True
        if chat_id_str in self._telegram_require_mention_chats():
            if self._is_reply_to_bot(message):
                return True
            if self._message_mentions_bot(message):
                return True
            return self._message_matches_mention_patterns(message)
        if chat_id_str in self._telegram_free_response_chats():
            return True
        if not self._telegram_require_mention():
            return True
        if self._is_reply_to_bot(message):
            return True
        # When guest_mode is True, _is_guest_mention already called
        # _message_mentions_bot above — skip the redundant second call.
        if not self._telegram_guest_mode() and self._message_mentions_bot(message):
            return True
        return self._message_matches_mention_patterns(message)

    def _load_auto_skill_routes(self) -> List[Dict[str, Any]]:
        """Load declarative auto-skill routes from Telegram config.

        Example:
            telegram:
              auto_skill_routes:
                - skill: tg
                  chats: [-1003437858232]
                  match:
                    urls: true
                    media: [photo, video]
        """
        raw_routes = self.config.extra.get("auto_skill_routes", [])
        if not isinstance(raw_routes, list):
            return []

        routes: List[Dict[str, Any]] = []
        for raw in raw_routes:
            if not isinstance(raw, dict):
                continue
            skill = str(raw.get("skill") or "").strip().lstrip("/")
            chats = raw.get("chats") or []
            match = raw.get("match") or {}
            if not skill or not isinstance(chats, list) or not isinstance(match, dict):
                continue
            routes.append({
                "skill": skill,
                "chats": {str(chat_id) for chat_id in chats},
                "match_urls": bool(match.get("urls", False)),
                "match_media": {str(kind).lower() for kind in (match.get("media") or [])},
            })
        return routes

    def _load_inline_preview_guard(self) -> Dict[str, Any]:
        """Load per-chat guard against Hermes-bot `/tg` previews.

        Chip's TG chats are fail-closed by default in this install: finished
        post previews must be routed through telegram-chip/ChipCR even when the
        config entry is missing or incomplete. Config can still add chats or
        override script/timeout, but not silently disable the hardcoded Chip
        guard unless HERMES_DISABLE_CHIP_TG_PREVIEW_GUARD=1 is set for tests.
        """
        raw = self.config.extra.get("inline_preview_guard", {})
        if raw is True:
            raw = {"enabled": True}
        if not isinstance(raw, dict):
            raw = {}
        chats = raw.get("chats") or []
        if isinstance(chats, (str, int)):
            chats = [chats]
        chat_set = {str(chat_id) for chat_id in chats}
        if os.getenv("HERMES_DISABLE_CHIP_TG_PREVIEW_GUARD", "0") != "1":
            chat_set |= set(_CHIP_TG_PREVIEW_GUARD_CHAT_IDS)
        enabled = bool(raw.get("enabled", False)) or bool(chat_set & _CHIP_TG_PREVIEW_GUARD_CHAT_IDS)
        return {
            "enabled": enabled,
            "chats": chat_set,
            "blocker": str(raw.get("blocker") or _INLINE_TG_PREVIEW_BLOCKER),
            "action": str(raw.get("action") or "chipcr_preview"),
            "script": str(raw.get("script") or "/home/hermes/.hermes/skills/tg/scripts/send-reworked-preview.sh"),
            "timeout": float(raw.get("timeout", 120)),
        }

    def _inline_preview_guard_applies(
        self,
        chat_id: str,
        content: str,
    ) -> bool:
        guard = getattr(self, "_inline_preview_guard", None) or {}
        if not guard.get("enabled"):
            return False
        chats = guard.get("chats") or set()
        if chats and str(chat_id) not in chats:
            return False
        return _looks_like_inline_tg_preview(content)

    @staticmethod
    def _extract_tg_preview_state_message_id(state_path: str = "/tmp/tg_preview_state.json") -> Optional[str]:
        try:
            data = json.loads(_Path(state_path).read_text(encoding="utf-8"))
        except Exception:
            return None
        if not (data.get("ok") and data.get("verified")):
            return None
        mid = data.get("message_id") or (data.get("message_ids") or [None])[0]
        return str(mid) if mid else None

    def _send_inline_preview_via_chipcr_sync(
        self,
        chat_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        guard = getattr(self, "_inline_preview_guard", None) or {}
        script = str(guard.get("script") or "")
        timeout = float(guard.get("timeout") or 120)
        if not script or not os.path.exists(script):
            return SendResult(success=False, error=f"inline preview guard script missing: {script}")
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".html",
            prefix="hermes-inline-tg-preview-",
            delete=False,
        ) as tmp:
            tmp.write(content.strip() + "\n")
            tmp_path = tmp.name
        try:
            proc = subprocess.run(
                [script, "--chat-id", str(chat_id), "--file", tmp_path, "--no-media"],
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            if proc.returncode != 0:
                detail = stderr or stdout or f"exit {proc.returncode}"
                return SendResult(success=False, error=f"ChipCR preview send failed: {detail[:500]}")
            message_id = self._extract_tg_preview_state_message_id()
            if not message_id:
                return SendResult(
                    success=False,
                    error="ChipCR preview send did not leave verified /tmp/tg_preview_state.json",
                )
            return SendResult(
                success=True,
                message_id=message_id,
                raw_response={
                    "inline_preview_guard": "chipcr_preview",
                    "stdout": stdout[-1000:],
                },
            )
        except Exception as exc:
            return SendResult(success=False, error=f"ChipCR preview guard exception: {exc}")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def _inline_preview_guard_send_result(
        self,
        chat_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[SendResult]:
        """Route configured inline `/tg` previews through ChipCR instead of bot-send."""
        if not self._inline_preview_guard_applies(chat_id, content):
            return None
        guard = getattr(self, "_inline_preview_guard", None) or {}
        action = str(guard.get("action") or "blocker").strip().lower()
        if action not in {"chipcr_preview", "chipcr", "send_chipcr"}:
            return None
        result = await asyncio.to_thread(
            self._send_inline_preview_via_chipcr_sync,
            chat_id,
            content,
            metadata,
        )
        if result.success:
            logger.info(
                "[%s] Routed inline TG preview through ChipCR guard (chat=%s message_id=%s)",
                self.name,
                chat_id,
                result.message_id,
            )
            return result
        self._last_inline_preview_guard_error = result.error
        logger.error(
            "[%s] ChipCR inline TG preview guard failed (chat=%s): %s",
            self.name,
            chat_id,
            result.error,
        )
        return None

    def _inline_preview_guard_replacement(
        self,
        chat_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Return blocker text when a configured chat would get inline preview."""
        guard = getattr(self, "_inline_preview_guard", None) or {}
        if not self._inline_preview_guard_applies(chat_id, content):
            return None
        thread_id = self._metadata_thread_id(metadata)
        error = getattr(self, "_last_inline_preview_guard_error", "") or ""
        logger.error(
            "[%s] Blocked inline TG preview from Hermes bot (chat=%s thread=%s len=%d error=%s)",
            self.name,
            chat_id,
            thread_id,
            len(content or ""),
            error,
        )
        blocker = str(guard.get("blocker") or _INLINE_TG_PREVIEW_BLOCKER)
        if error:
            blocker = f"превью не отправлено/не подтверждено: {error[:700]}"
            self._last_inline_preview_guard_error = ""
        return blocker

    @staticmethod
    def _recent_chipcr_preview_message_ids() -> set[str]:
        """Return exact message ids sent by the ChipCR `/tg` preview path.

        `telegram-chip` sends previews from Chip's human account, so Telegram
        delivers those messages back to this bot as normal user messages. If we
        process that echo, the auto `/tg` route can recursively preview the
        preview. The canonical sender writes verified ids to these local state
        files; treat only exact ids as self-generated preview echoes.
        """
        ids: set[str] = set()
        state_path = os.getenv("TG_PREVIEW_STATE_FILE", "/tmp/tg_preview_state.json")
        message_id_path = os.getenv("TG_PREVIEW_MESSAGE_ID_FILE", "/tmp/tg_preview_message_id.txt")

        try:
            data = json.loads(_Path(state_path).read_text(encoding="utf-8"))
            if data.get("ok") and data.get("verified"):
                for value in data.get("message_ids") or []:
                    if value is not None:
                        ids.add(str(value))
                if data.get("message_id") is not None:
                    ids.add(str(data.get("message_id")))
        except Exception:
            pass

        try:
            for line in _Path(message_id_path).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    ids.add(line)
        except Exception:
            pass

        return ids

    @staticmethod
    def _pending_chipcr_preview_echo_fingerprints(chat_id: str) -> set[str]:
        """Return still-valid pre-send preview fingerprints for this chat.

        The exact-id guard is not enough: Telegram can deliver the ChipCR echo
        while `send-preview.sh` is still verifying and before it has written the
        final state file (or when callers use a custom state path). The sender
        therefore writes a short-lived content fingerprint before sending.
        """
        path = os.getenv("TG_PREVIEW_PENDING_ECHO_FILE", _TG_PREVIEW_PENDING_ECHO_FILE)
        now = time.time()
        fingerprints: set[str] = set()
        try:
            lines = _Path(path).read_text(encoding="utf-8").splitlines()
        except Exception:
            return fingerprints
        for line in lines[-200:]:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            if str(data.get("chat_id") or "") != str(chat_id):
                continue
            try:
                expires_at = float(data.get("expires_at") or 0)
            except (TypeError, ValueError):
                expires_at = 0
            if expires_at and expires_at < now:
                continue
            value = str(data.get("sha256") or "").strip()
            if value:
                fingerprints.add(value)
        return fingerprints

    def _is_chipcr_tg_preview_echo(self, message: Message) -> bool:
        """Return True for ChipCR preview messages that must be ignored.

        `telegram-chip` posts from Chip's human account, so Bot API receives the
        preview as a normal user message. Exact ids are preferred; pending
        fingerprints cover the race before exact state is visible.
        """
        chat_id = str(getattr(getattr(message, "chat", None), "id", ""))
        guard = getattr(self, "_inline_preview_guard", None) or {}
        guarded_chats = guard.get("chats") or set(_CHIP_TG_PREVIEW_GUARD_CHAT_IDS)
        if guarded_chats and chat_id not in guarded_chats:
            return False

        user = getattr(message, "from_user", None)
        user_id = str(getattr(user, "id", "") or "")
        username = str(getattr(user, "username", "") or "").lstrip("@").lower()
        if user_id and user_id != "617744661" and username != "chipcr":
            return False

        message_id = str(getattr(message, "message_id", "") or "")
        if message_id and message_id in self._recent_chipcr_preview_message_ids():
            return True

        visible_text = getattr(message, "caption", None) or getattr(message, "text", None) or ""
        if not _looks_like_inline_tg_preview(visible_text):
            return False
        pending = self._pending_chipcr_preview_echo_fingerprints(chat_id)
        return bool(pending and _tg_preview_echo_fingerprint(visible_text) in pending)

    def _auto_skill_prefix_for_text(self, chat_id: str, text: str) -> Optional[str]:
        """Return a slash-command prefix when text matches an auto-skill route."""
        if not chat_id or not text or text.lstrip().startswith("/"):
            return None
        if _looks_like_inline_tg_preview(text):
            return None
        has_url = bool(re.search(r"https?://\S+|t\.co/\S+", text))
        for route in self._auto_skill_routes:
            if chat_id not in route["chats"]:
                continue
            if has_url and route.get("match_urls"):
                return f"/{route['skill']} "
        return None

    def _auto_skill_prefix_for_media(self, chat_id: str, msg_type: MessageType) -> Optional[str]:
        """Return a slash-command prefix when media matches an auto-skill route."""
        if not chat_id:
            return None
        media_kind = msg_type.value.lower()
        for route in self._auto_skill_routes:
            if chat_id not in route["chats"]:
                continue
            if media_kind in route.get("match_media", set()):
                return f"/{route['skill']} "
        return None

    def _telegram_business_config(self) -> Dict[str, Any]:
        cfg = self.config.extra.get("business") or {}
        return cfg if isinstance(cfg, dict) else {}

    def _telegram_business_enabled(self) -> bool:
        value = self._telegram_business_config().get("enabled", False)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _telegram_business_allowed_chats(self) -> set[str]:
        raw = self._telegram_business_config().get("allowed_chats") or []
        if isinstance(raw, (str, int)):
            raw = [raw]
        return {str(item).strip() for item in raw if str(item).strip()}

    def _telegram_business_trigger_words(self) -> List[str]:
        raw = self._telegram_business_config().get("trigger_words") or []
        if isinstance(raw, str):
            raw = [raw]
        words = [str(item).strip() for item in raw if str(item).strip()]
        if not words:
            words = ["Sigurd", "Сигурд"]
        return words

    def _telegram_business_allow_reply_trigger(self) -> bool:
        """Return whether replying to a prior bot message wakes Business mode.

        Telegram Business delegated chats are often used as ambient operator
        inboxes. A plain reply to a previous bot message can be accidental noise
        (especially around inline-keyboard/status messages), so Business mode is
        wake-word/mention-only by default. Installations that explicitly want
        reply-to-bot follow-ups can opt in with
        ``telegram.business.allow_reply_trigger: true``.
        """
        value = self._telegram_business_config().get("allow_reply_trigger", False)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _message_matches_business_trigger(self, message: Message) -> bool:
        if self._telegram_business_allow_reply_trigger() and self._is_reply_to_bot(message):
            return True
        if self._message_mentions_bot(message):
            return True
        text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
        if not text:
            return False
        for word in self._telegram_business_trigger_words():
            if re.match(rf"(?iu)^\s*{re.escape(word)}(?:\b|[\s,.:;!?-])", text):
                return True
        return False

    def _strip_business_trigger_text(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return text
        cleaned = self._clean_bot_trigger_text(text)
        for word in self._telegram_business_trigger_words():
            stripped = re.sub(rf"(?iu)^\s*{re.escape(word)}(?:\b|[\s,.:;!?-])*\s*", "", cleaned, count=1).strip()
            stripped = stripped.lstrip(" ,.:;!?-").strip()
            if stripped != cleaned:
                return stripped or cleaned
        return cleaned

    def _is_business_trusted_actor(self, message: Message) -> bool:
        sender = getattr(message, "from_user", None)
        sender_id = str(getattr(sender, "id", "") or "").strip()
        if not sender_id:
            return False
        explicit_ids: set[str] = set()
        for env_name in ("TELEGRAM_ALLOWED_USERS", "GATEWAY_ALLOWED_USERS"):
            for part in os.getenv(env_name, "").split(","):
                part = part.strip()
                if part and part != "*":
                    explicit_ids.add(part)
        if sender_id in explicit_ids:
            return True
        runner = getattr(getattr(self, "_message_handler", None), "__self__", None)
        pairing_store = getattr(runner, "pairing_store", None)
        is_approved = getattr(pairing_store, "is_approved", None)
        if callable(is_approved):
            try:
                return bool(is_approved("telegram", sender_id))
            except Exception:
                logger.debug("[Telegram] Business pairing-store trust check failed", exc_info=True)
        return False

    async def _handle_business_connection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        conn = getattr(update, "business_connection", None)
        logger.info(
            "Received Telegram Business connection update id=%s user_id=%s can_reply=%s",
            getattr(conn, "id", None),
            getattr(getattr(conn, "user", None), "id", None),
            getattr(conn, "can_reply", None),
        )

    async def _handle_business_messages_deleted(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        deleted = getattr(update, "deleted_business_messages", None)
        logger.info(
            "Received Telegram Business messages deleted update business_connection_id=%s chat_id=%s",
            getattr(deleted, "business_connection_id", None),
            getattr(getattr(deleted, "chat", None), "id", None),
        )

    async def _handle_business_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = getattr(update, "business_message", None)
        if not msg:
            return
        chat = getattr(msg, "chat", None)
        sender = getattr(msg, "from_user", None)
        business_connection_id = getattr(msg, "business_connection_id", None)
        has_text = bool(getattr(msg, "text", None) or getattr(msg, "caption", None))
        logger.info(
            "Received Telegram Business message update chat_id=%s user_id=%s has_text=%s business_connection_id=%s",
            getattr(chat, "id", None),
            getattr(sender, "id", None),
            has_text,
            bool(business_connection_id),
        )
        if not self._telegram_business_enabled():
            logger.info("Ignored Telegram Business message chat_id=%s reason=business_disabled", getattr(chat, "id", None))
            return
        if not business_connection_id:
            logger.warning("Ignored Telegram Business message chat_id=%s reason=missing_business_connection_id", getattr(chat, "id", None))
            return
        if not has_text:
            logger.info("Ignored Telegram Business message chat_id=%s reason=missing_text", getattr(chat, "id", None))
            return
        if sender and getattr(sender, "is_bot", False):
            logger.info("Ignored Telegram Business message chat_id=%s reason=bot_sender", getattr(chat, "id", None))
            return
        if self._bot and sender and getattr(sender, "id", None) == getattr(self._bot, "id", None):
            logger.info("Ignored Telegram Business message chat_id=%s reason=self_sender", getattr(chat, "id", None))
            return
        chat_id = str(getattr(chat, "id", "") or "")
        allowed_chats = self._telegram_business_allowed_chats()
        if allowed_chats and chat_id not in allowed_chats:
            logger.info("Ignored Telegram Business message chat_id=%s reason=chat_not_allowed", chat_id)
            return
        if not self._message_matches_business_trigger(msg):
            logger.info("Ignored Telegram Business message chat_id=%s reason=missing_trigger", chat_id)
            return

        trusted = self._is_business_trusted_actor(msg)
        event = self._build_message_event(msg, MessageType.TEXT, update_id=update.update_id)
        event.text = self._strip_business_trigger_text(getattr(msg, "text", None) or getattr(msg, "caption", None) or event.text) or event.text
        event.source.business_connection_id = str(business_connection_id)
        event.source.external_safe_mode = not trusted
        event.source.chat_type = "dm"
        await self._attach_replied_media_to_event(msg, event)
        logger.info(
            "Accepted Telegram Business message chat_id=%s user_id=%s business_connection_id=True external_safe_mode=%s",
            chat_id,
            getattr(sender, "id", None),
            event.source.external_safe_mode,
        )
        self._enqueue_text_event(event)

    async def _handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages.

        Telegram clients split long messages into multiple updates.  Buffer
        rapid successive text messages from the same user/chat and aggregate
        them into a single MessageEvent before dispatching.
        """
        if not update.message or not update.message.text:
            return
        if self._is_chipcr_tg_preview_echo(update.message):
            logger.info(
                "[Telegram] Ignoring ChipCR /tg preview echo chat=%s message_id=%s",
                getattr(getattr(update.message, "chat", None), "id", None),
                getattr(update.message, "message_id", None),
            )
            return
        if not self._should_process_message(update.message):
            return

        event = self._build_message_event(update.message, MessageType.TEXT, update_id=update.update_id)
        event.text = self._clean_bot_trigger_text(event.text)
        await self._attach_replied_media_to_event(update.message, event)

        # Auto skill route: e.g. a chat can route URL-like source messages to /tg.
        chat_id = str(getattr(update.message.chat, "id", ""))
        skill_prefix = self._auto_skill_prefix_for_text(chat_id, event.text)
        if skill_prefix:
            event.text = skill_prefix + event.text

        self._enqueue_text_event(event)

    async def _handle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming command messages."""
        if not update.message or not update.message.text:
            return
        if not self._should_process_message(update.message, is_command=True):
            return
        
        event = self._build_message_event(update.message, MessageType.COMMAND, update_id=update.update_id)
        await self._attach_replied_media_to_event(update.message, event)
        await self.handle_message(event)

    async def _handle_location_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming location/venue pin messages."""
        if not update.message:
            return
        if not self._should_process_message(update.message):
            return

        msg = update.message
        venue = getattr(msg, "venue", None)
        location = getattr(venue, "location", None) if venue else getattr(msg, "location", None)

        if not location:
            return

        lat = getattr(location, "latitude", None)
        lon = getattr(location, "longitude", None)
        if lat is None or lon is None:
            return

        # Build a text message with coordinates and context
        parts = ["[The user shared a location pin.]"]
        if venue:
            title = getattr(venue, "title", None)
            address = getattr(venue, "address", None)
            if title:
                parts.append(f"Venue: {title}")
            if address:
                parts.append(f"Address: {address}")
        parts.append(f"latitude: {lat}")
        parts.append(f"longitude: {lon}")
        parts.append(f"Map: https://www.google.com/maps/search/?api=1&query={lat},{lon}")
        parts.append("Ask what they'd like to find nearby (restaurants, cafes, etc.) and any preferences.")

        event = self._build_message_event(msg, MessageType.LOCATION, update_id=update.update_id)
        event.text = "\n".join(parts)
        await self.handle_message(event)

    # ------------------------------------------------------------------
    # Text message aggregation (handles Telegram client-side splits)
    # ------------------------------------------------------------------

    def _text_batch_key(self, event: MessageEvent) -> str:
        """Session-scoped key for text message batching."""
        from gateway.session import build_session_key
        return build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )

    def _enqueue_text_event(self, event: MessageEvent) -> None:
        """Buffer a text event and reset the flush timer.

        When Telegram splits a long user message into multiple updates,
        they arrive within a few hundred milliseconds.  This method
        concatenates them and waits for a short quiet period before
        dispatching the combined message.
        """
        key = self._text_batch_key(event)
        existing = self._pending_text_batches.get(key)
        chunk_len = len(event.text or "")
        if existing is None:
            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            self._pending_text_batches[key] = event
        else:
            # Append text from the follow-up chunk
            if event.text:
                existing.text = f"{existing.text}\n{event.text}" if existing.text else event.text
            existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            # Merge any media that might be attached
            if event.media_urls:
                existing.media_urls.extend(event.media_urls)
                existing.media_types.extend(event.media_types)

        # Cancel any pending flush and restart the timer
        prior_task = self._pending_text_batch_tasks.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_text_batch_tasks[key] = asyncio.create_task(
            self._flush_text_batch(key)
        )

    async def _flush_text_batch(self, key: str) -> None:
        """Wait for the quiet period then dispatch the aggregated text.

        Uses a longer delay when the latest chunk is near Telegram's 4096-char
        split point, since a continuation chunk is almost certain.
        """
        current_task = asyncio.current_task()
        try:
            # Adaptive delay: if the latest chunk is near Telegram's 4096-char
            # split point, a continuation is almost certain — wait longer.
            pending = self._pending_text_batches.get(key)
            last_len = getattr(pending, "_last_chunk_len", 0) if pending else 0
            if last_len >= self._SPLIT_THRESHOLD:
                delay = self._text_batch_split_delay_seconds
            else:
                delay = self._text_batch_delay_seconds
            await asyncio.sleep(delay)
            event = self._pending_text_batches.pop(key, None)
            if not event:
                return
            logger.info(
                "[Telegram] Flushing text batch %s (%d chars)",
                key, len(event.text or ""),
            )
            await self.handle_message(event)
        finally:
            if self._pending_text_batch_tasks.get(key) is current_task:
                self._pending_text_batch_tasks.pop(key, None)

    # ------------------------------------------------------------------
    # Photo batching
    # ------------------------------------------------------------------

    def _photo_batch_key(self, event: MessageEvent, msg: Message) -> str:
        """Return a batching key for Telegram photos/albums."""
        from gateway.session import build_session_key
        session_key = build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )
        media_group_id = getattr(msg, "media_group_id", None)
        if media_group_id:
            return f"{session_key}:album:{media_group_id}"
        return f"{session_key}:photo-burst"

    async def _flush_photo_batch(self, batch_key: str) -> None:
        """Send a buffered photo burst/album as a single MessageEvent."""
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._media_batch_delay_seconds)
            event = self._pending_photo_batches.pop(batch_key, None)
            if not event:
                return
            logger.info("[Telegram] Flushing photo batch %s with %d image(s)", batch_key, len(event.media_urls))
            await self.handle_message(event)
        finally:
            if self._pending_photo_batch_tasks.get(batch_key) is current_task:
                self._pending_photo_batch_tasks.pop(batch_key, None)

    def _enqueue_photo_event(self, batch_key: str, event: MessageEvent) -> None:
        """Merge photo events into a pending batch and schedule flush."""
        existing = self._pending_photo_batches.get(batch_key)
        if existing is None:
            self._pending_photo_batches[batch_key] = event
        else:
            existing.media_urls.extend(event.media_urls)
            existing.media_types.extend(event.media_types)
            if event.text:
                existing.text = self._merge_caption(existing.text, event.text)

        prior_task = self._pending_photo_batch_tasks.get(batch_key)
        if prior_task and not prior_task.done():
            prior_task.cancel()

        self._pending_photo_batch_tasks[batch_key] = asyncio.create_task(self._flush_photo_batch(batch_key))

    async def _handle_media_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming media messages, downloading images to local cache."""
        if not update.message:
            return
        if self._is_chipcr_tg_preview_echo(update.message):
            logger.info(
                "[Telegram] Ignoring ChipCR /tg preview media echo chat=%s message_id=%s",
                getattr(getattr(update.message, "chat", None), "id", None),
                getattr(update.message, "message_id", None),
            )
            return
        if not self._should_process_message(update.message):
            return
        
        msg = update.message
        
        # Determine media type
        if msg.sticker:
            msg_type = MessageType.STICKER
        elif msg.photo:
            msg_type = MessageType.PHOTO
        elif msg.video:
            msg_type = MessageType.VIDEO
        elif msg.audio:
            msg_type = MessageType.AUDIO
        elif msg.voice:
            msg_type = MessageType.VOICE
        elif msg.document:
            msg_type = MessageType.DOCUMENT
        else:
            msg_type = MessageType.DOCUMENT
        
        event = self._build_message_event(msg, msg_type, update_id=update.update_id)
        await self._attach_replied_media_to_event(msg, event)
        
        # Add caption as text
        if msg.caption:
            event.text = self._clean_bot_trigger_text(msg.caption)

        # Auto skill route early, before photo/video batching can return.
        chat_id = str(getattr(msg.chat, "id", ""))
        skill_prefix = self._auto_skill_prefix_for_media(chat_id, msg_type)
        if skill_prefix:
            if event.text:
                event.text = skill_prefix + event.text
            else:
                event.text = skill_prefix
        
        # Handle stickers: describe via vision tool with caching
        if msg.sticker:
            await self._handle_sticker(msg, event)
            await self.handle_message(event)
            return
        
        # Download photo to local image cache so the vision tool can access it
        # even after Telegram's ephemeral file URLs expire (~1 hour).
        if msg.photo:
            try:
                # msg.photo is a list of PhotoSize sorted by size; take the largest
                photo = msg.photo[-1]
                file_obj = await photo.get_file()
                # Download the image bytes directly into memory
                image_bytes = await file_obj.download_as_bytearray()
                # Determine extension from the file path if available
                ext = ".jpg"
                if file_obj.file_path:
                    for candidate in [".png", ".webp", ".gif", ".jpeg", ".jpg"]:
                        if file_obj.file_path.lower().endswith(candidate):
                            ext = candidate
                            break
                # Save to local cache (for vision tool access)
                cached_path = cache_image_from_bytes(bytes(image_bytes), ext=ext)
                event.media_urls = [cached_path]
                event.media_types = [f"image/{ext.lstrip('.')}" ]
                logger.info("[Telegram] Cached user photo at %s", cached_path)
                media_group_id = getattr(msg, "media_group_id", None)
                if media_group_id:
                    await self._queue_media_group_event(str(media_group_id), event)
                else:
                    batch_key = self._photo_batch_key(event, msg)
                    self._enqueue_photo_event(batch_key, event)
                return

            except Exception as e:
                logger.warning("[Telegram] Failed to cache photo: %s", e, exc_info=True)

        # Download voice/audio messages to cache for STT transcription
        if msg.voice:
            try:
                file_obj = await msg.voice.get_file()
                audio_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=".ogg")
                event.media_urls = [cached_path]
                event.media_types = ["audio/ogg"]
                logger.info("[Telegram] Cached user voice at %s", cached_path)
            except Exception as e:
                logger.warning("[Telegram] Failed to cache voice: %s", e, exc_info=True)
        elif msg.audio:
            try:
                file_obj = await msg.audio.get_file()
                audio_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=".mp3")
                event.media_urls = [cached_path]
                event.media_types = ["audio/mp3"]
                logger.info("[Telegram] Cached user audio at %s", cached_path)
            except Exception as e:
                logger.warning("[Telegram] Failed to cache audio: %s", e, exc_info=True)

        elif msg.video:
            try:
                file_obj = await msg.video.get_file()
                video_bytes = await file_obj.download_as_bytearray()
                ext = ".mp4"
                if getattr(file_obj, "file_path", None):
                    for candidate in SUPPORTED_VIDEO_TYPES:
                        if file_obj.file_path.lower().endswith(candidate):
                            ext = candidate
                            break
                cached_path = cache_video_from_bytes(bytes(video_bytes), ext=ext)
                event.media_urls = [cached_path]
                event.media_types = [SUPPORTED_VIDEO_TYPES.get(ext, "video/mp4")]
                logger.info("[Telegram] Cached user video at %s", cached_path)
            except Exception as e:
                logger.warning("[Telegram] Failed to cache video: %s", e, exc_info=True)

        # Download document files to cache for agent processing
        elif msg.document:
            doc = msg.document
            try:
                # Determine file extension
                ext = ""
                original_filename = doc.file_name or ""
                if original_filename:
                    _, ext = os.path.splitext(original_filename)
                    ext = ext.lower()

                # Normalize mime_type for robust comparisons (some clients send
                # uppercase like "IMAGE/PNG").
                doc_mime = (doc.mime_type or "").lower()

                # If no extension from filename, reverse-lookup from MIME type
                if not ext and doc_mime:
                    ext = _TELEGRAM_IMAGE_MIME_TO_EXT.get(doc_mime, "")
                    if not ext:
                        mime_to_ext = {v: k for k, v in SUPPORTED_DOCUMENT_TYPES.items()}
                        ext = mime_to_ext.get(doc_mime, "")

                # Check file size early so image documents cannot bypass the
                # document size limit by taking the image path.
                MAX_DOC_BYTES = 20 * 1024 * 1024
                if not doc.file_size or doc.file_size > MAX_DOC_BYTES:
                    event.text = (
                        "The document is too large or its size could not be verified. "
                        "Maximum: 20 MB."
                    )
                    logger.info("[Telegram] Document too large: %s bytes", doc.file_size)
                    await self.handle_message(event)
                    return

                # Telegram may deliver screenshots/photos as documents. If the
                # payload is actually an image, route it through the image cache
                # and batching path instead of rejecting it as a document.
                if ext in _TELEGRAM_IMAGE_EXTENSIONS or doc_mime.startswith("image/"):
                    file_obj = await doc.get_file()
                    image_bytes = await file_obj.download_as_bytearray()
                    image_ext = ext if ext in _TELEGRAM_IMAGE_EXTENSIONS else _TELEGRAM_IMAGE_MIME_TO_EXT.get(doc_mime, ".jpg")
                    try:
                        cached_path = cache_image_from_bytes(bytes(image_bytes), ext=image_ext)
                    except ValueError as e:
                        logger.warning("[Telegram] Failed to cache image document: %s", e, exc_info=True)
                        event.text = (
                            f"Image document '{original_filename or doc_mime or ext or 'unknown'}' "
                            "could not be read as an image."
                        )
                        await self.handle_message(event)
                        return

                    event.message_type = MessageType.PHOTO
                    event.media_urls = [cached_path]
                    event.media_types = [doc_mime if doc_mime.startswith("image/") else _TELEGRAM_IMAGE_EXT_TO_MIME.get(image_ext, "image/jpeg")]
                    logger.info("[Telegram] Cached user image-document at %s", cached_path)

                    media_group_id = getattr(msg, "media_group_id", None)
                    if media_group_id:
                        await self._queue_media_group_event(str(media_group_id), event)
                    else:
                        batch_key = self._photo_batch_key(event, msg)
                        self._enqueue_photo_event(batch_key, event)
                    return

                if not ext and doc.mime_type:
                    video_mime_to_ext = {v: k for k, v in SUPPORTED_VIDEO_TYPES.items()}
                    ext = video_mime_to_ext.get(doc.mime_type, "")

                if ext in SUPPORTED_VIDEO_TYPES:
                    file_obj = await doc.get_file()
                    video_bytes = await file_obj.download_as_bytearray()
                    cached_path = cache_video_from_bytes(bytes(video_bytes), ext=ext)
                    event.media_urls = [cached_path]
                    event.media_types = [SUPPORTED_VIDEO_TYPES[ext]]
                    event.message_type = MessageType.VIDEO
                    logger.info("[Telegram] Cached user video document at %s", cached_path)
                    await self.handle_message(event)
                    return

                # Check if supported
                if ext not in SUPPORTED_DOCUMENT_TYPES:
                    supported_list = ", ".join(sorted(SUPPORTED_DOCUMENT_TYPES.keys()))
                    event.text = (
                        f"Unsupported document type '{ext or 'unknown'}'. "
                        f"Supported types: {supported_list}"
                    )
                    logger.info("[Telegram] Unsupported document type: %s", ext or "unknown")
                    await self.handle_message(event)
                    return

                # Download and cache
                file_obj = await doc.get_file()
                doc_bytes = await file_obj.download_as_bytearray()
                raw_bytes = bytes(doc_bytes)
                cached_path = cache_document_from_bytes(raw_bytes, original_filename or f"document{ext}")
                mime_type = SUPPORTED_DOCUMENT_TYPES[ext]
                event.media_urls = [cached_path]
                event.media_types = [mime_type]
                logger.info("[Telegram] Cached user document at %s", cached_path)

                # For text files, inject content into event.text (capped at 100 KB)
                MAX_TEXT_INJECT_BYTES = 100 * 1024
                if ext in TEXT_DOCUMENT_EXTENSIONS and len(raw_bytes) <= MAX_TEXT_INJECT_BYTES:
                    try:
                        text_content = raw_bytes.decode("utf-8")
                        display_name = original_filename or f"document{ext}"
                        display_name = re.sub(r'[^\w.\- ]', '_', display_name)
                        injection = f"[Content of {display_name}]:\n{text_content}"
                        if event.text:
                            event.text = f"{injection}\n\n{event.text}"
                        else:
                            event.text = injection
                    except UnicodeDecodeError:
                        logger.warning(
                            "[Telegram] Could not decode text file as UTF-8, skipping content injection",
                            exc_info=True,
                        )

            except Exception as e:
                logger.warning("[Telegram] Failed to cache document: %s", e, exc_info=True)

        media_group_id = getattr(msg, "media_group_id", None)
        if media_group_id:
            await self._queue_media_group_event(str(media_group_id), event)
            return

        await self.handle_message(event)

    async def _attach_replied_media_to_event(self, msg: Message, event: MessageEvent) -> None:
        """Attach media from the Telegram message being replied to.

        Telegram reply updates include ``reply_to_message`` metadata. Hermes
        already injects replied-to text/captions into context, but file-only
        messages have empty text and were previously invisible to the agent.
        This downloads supported replied-to media into the same local caches as
        first-class incoming media so commands like ``/summ`` can operate on a
        file referenced via Telegram's native Reply UI.
        """
        replied = getattr(msg, "reply_to_message", None)
        if not replied:
            return

        def merge_replied_context(note: str) -> None:
            if event.text and event.text.startswith("/"):
                event.text = f"{event.text}\n\n{note}"
            else:
                event.text = f"{note}\n\n{event.text}" if event.text else note

        try:
            if getattr(replied, "photo", None):
                photo = replied.photo[-1]
                file_obj = await photo.get_file()
                image_bytes = await file_obj.download_as_bytearray()
                ext = ".jpg"
                if getattr(file_obj, "file_path", None):
                    for candidate in [".png", ".webp", ".gif", ".jpeg", ".jpg"]:
                        if file_obj.file_path.lower().endswith(candidate):
                            ext = candidate
                            break
                cached_path = cache_image_from_bytes(bytes(image_bytes), ext=ext)
                event.media_urls.append(cached_path)
                event.media_types.append(f"image/{ext.lstrip('.')}")
                if event.message_type in (MessageType.TEXT, MessageType.COMMAND):
                    event.message_type = MessageType.PHOTO
                logger.info("[Telegram] Cached replied-to photo at %s", cached_path)
                return

            if getattr(replied, "voice", None):
                file_obj = await replied.voice.get_file()
                audio_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=".ogg")
                event.media_urls.append(cached_path)
                event.media_types.append("audio/ogg")
                if event.message_type in (MessageType.TEXT, MessageType.COMMAND):
                    event.message_type = MessageType.VOICE
                logger.info("[Telegram] Cached replied-to voice at %s", cached_path)
                return

            if getattr(replied, "audio", None):
                file_obj = await replied.audio.get_file()
                audio_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=".mp3")
                event.media_urls.append(cached_path)
                event.media_types.append("audio/mp3")
                if event.message_type in (MessageType.TEXT, MessageType.COMMAND):
                    event.message_type = MessageType.AUDIO
                logger.info("[Telegram] Cached replied-to audio at %s", cached_path)
                return

            if getattr(replied, "video", None):
                file_obj = await replied.video.get_file()
                video_bytes = await file_obj.download_as_bytearray()
                ext = ".mp4"
                if getattr(file_obj, "file_path", None):
                    for candidate in SUPPORTED_VIDEO_TYPES:
                        if file_obj.file_path.lower().endswith(candidate):
                            ext = candidate
                            break
                cached_path = cache_video_from_bytes(bytes(video_bytes), ext=ext)
                event.media_urls.append(cached_path)
                event.media_types.append(SUPPORTED_VIDEO_TYPES.get(ext, "video/mp4"))
                if event.message_type in (MessageType.TEXT, MessageType.COMMAND):
                    event.message_type = MessageType.VIDEO
                logger.info("[Telegram] Cached replied-to video at %s", cached_path)
                return

            doc = getattr(replied, "document", None)
            if not doc:
                return

            original_filename = doc.file_name or ""
            _, ext = os.path.splitext(original_filename)
            ext = ext.lower()
            doc_mime = (doc.mime_type or "").lower()
            if not ext and doc_mime:
                ext = _TELEGRAM_IMAGE_MIME_TO_EXT.get(doc_mime, "")
                if not ext:
                    mime_to_ext = {v: k for k, v in SUPPORTED_DOCUMENT_TYPES.items()}
                    ext = mime_to_ext.get(doc_mime, "")
                if not ext:
                    video_mime_to_ext = {v: k for k, v in SUPPORTED_VIDEO_TYPES.items()}
                    ext = video_mime_to_ext.get(doc_mime, "")

            MAX_DOC_BYTES = 20 * 1024 * 1024
            if not doc.file_size or doc.file_size > MAX_DOC_BYTES:
                note = (
                    "[The message you replied to contains a document that is too large "
                    "or whose size could not be verified. Maximum: 20 MB.]"
                )
                merge_replied_context(note)
                logger.info("[Telegram] Replied-to document too large: %s bytes", doc.file_size)
                return

            if ext in _TELEGRAM_IMAGE_EXTENSIONS or doc_mime.startswith("image/"):
                file_obj = await doc.get_file()
                image_bytes = await file_obj.download_as_bytearray()
                image_ext = ext if ext in _TELEGRAM_IMAGE_EXTENSIONS else _TELEGRAM_IMAGE_MIME_TO_EXT.get(doc_mime, ".jpg")
                cached_path = cache_image_from_bytes(bytes(image_bytes), ext=image_ext)
                event.media_urls.append(cached_path)
                event.media_types.append(doc_mime if doc_mime.startswith("image/") else _TELEGRAM_IMAGE_EXT_TO_MIME.get(image_ext, "image/jpeg"))
                if event.message_type in (MessageType.TEXT, MessageType.COMMAND):
                    event.message_type = MessageType.PHOTO
                logger.info("[Telegram] Cached replied-to image-document at %s", cached_path)
                return

            if ext in SUPPORTED_VIDEO_TYPES:
                file_obj = await doc.get_file()
                video_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_video_from_bytes(bytes(video_bytes), ext=ext)
                event.media_urls.append(cached_path)
                event.media_types.append(SUPPORTED_VIDEO_TYPES[ext])
                if event.message_type in (MessageType.TEXT, MessageType.COMMAND):
                    event.message_type = MessageType.VIDEO
                logger.info("[Telegram] Cached replied-to video document at %s", cached_path)
                return

            if ext not in SUPPORTED_DOCUMENT_TYPES:
                note = (
                    f"[The message you replied to contains an unsupported document type "
                    f"'{ext or 'unknown'}'. Supported types: {', '.join(sorted(SUPPORTED_DOCUMENT_TYPES.keys()))}.]"
                )
                merge_replied_context(note)
                logger.info("[Telegram] Unsupported replied-to document type: %s", ext or "unknown")
                return

            file_obj = await doc.get_file()
            doc_bytes = await file_obj.download_as_bytearray()
            raw_bytes = bytes(doc_bytes)
            cached_path = cache_document_from_bytes(raw_bytes, original_filename or f"document{ext}")
            event.media_urls.append(cached_path)
            event.media_types.append(SUPPORTED_DOCUMENT_TYPES[ext])
            if event.message_type in (MessageType.TEXT, MessageType.COMMAND):
                event.message_type = MessageType.DOCUMENT
            logger.info("[Telegram] Cached replied-to document at %s", cached_path)

            MAX_TEXT_INJECT_BYTES = 100 * 1024
            if ext in TEXT_DOCUMENT_EXTENSIONS and len(raw_bytes) <= MAX_TEXT_INJECT_BYTES:
                try:
                    text_content = raw_bytes.decode("utf-8")
                    display_name = original_filename or f"document{ext}"
                    display_name = re.sub(r'[^\w.\- ]', '_', display_name)
                    injection = f"[Content of replied-to {display_name}]:\n{text_content}"
                    merge_replied_context(injection)
                except UnicodeDecodeError:
                    logger.warning(
                        "[Telegram] Could not decode replied-to text file as UTF-8, skipping content injection",
                        exc_info=True,
                    )
        except Exception as e:
            logger.warning("[Telegram] Failed to cache replied-to media: %s", e, exc_info=True)

    async def _queue_media_group_event(self, media_group_id: str, event: MessageEvent) -> None:
        """Buffer Telegram media-group items so albums arrive as one logical event.

        Telegram delivers albums as multiple updates with a shared media_group_id.
        If we forward each item immediately, the gateway thinks the second image is a
        new user message and interrupts the first. We debounce briefly and merge the
        attachments into a single MessageEvent.
        """
        existing = self._media_group_events.get(media_group_id)
        if existing is None:
            self._media_group_events[media_group_id] = event
        else:
            existing.media_urls.extend(event.media_urls)
            existing.media_types.extend(event.media_types)
            if event.text:
                existing.text = self._merge_caption(existing.text, event.text)

        prior_task = self._media_group_tasks.get(media_group_id)
        if prior_task:
            prior_task.cancel()

        self._media_group_tasks[media_group_id] = asyncio.create_task(
            self._flush_media_group_event(media_group_id)
        )

    async def _flush_media_group_event(self, media_group_id: str) -> None:
        try:
            await asyncio.sleep(self.MEDIA_GROUP_WAIT_SECONDS)
            event = self._media_group_events.pop(media_group_id, None)
            if event is not None:
                await self.handle_message(event)
        except asyncio.CancelledError:
            return
        finally:
            self._media_group_tasks.pop(media_group_id, None)

    async def _handle_sticker(self, msg: Message, event: "MessageEvent") -> None:
        """
        Describe a Telegram sticker via vision analysis, with caching.

        For static stickers (WEBP), we download, analyze with vision, and cache
        the description by file_unique_id. For animated/video stickers, we inject
        a placeholder noting the emoji.
        """
        from gateway.sticker_cache import (
            get_cached_description,
            cache_sticker_description,
            build_sticker_injection,
            build_animated_sticker_injection,
            STICKER_VISION_PROMPT,
        )

        sticker = msg.sticker
        emoji = sticker.emoji or ""
        set_name = sticker.set_name or ""

        # Animated and video stickers can't be analyzed as static images
        if sticker.is_animated or sticker.is_video:
            event.text = build_animated_sticker_injection(emoji)
            return

        # Check the cache first
        cached = get_cached_description(sticker.file_unique_id)
        if cached:
            event.text = build_sticker_injection(
                cached["description"], cached.get("emoji", emoji), cached.get("set_name", set_name)
            )
            logger.info("[Telegram] Sticker cache hit: %s", sticker.file_unique_id)
            return

        # Cache miss -- download and analyze
        try:
            file_obj = await sticker.get_file()
            image_bytes = await file_obj.download_as_bytearray()
            cached_path = cache_image_from_bytes(bytes(image_bytes), ext=".webp")
            logger.info("[Telegram] Analyzing sticker at %s", cached_path)

            from tools.vision_tools import vision_analyze_tool
            result_json = await vision_analyze_tool(
                image_url=cached_path,
                user_prompt=STICKER_VISION_PROMPT,
            )
            result = json.loads(result_json)

            if result.get("success"):
                description = result.get("analysis", "a sticker")
                cache_sticker_description(sticker.file_unique_id, description, emoji, set_name)
                event.text = build_sticker_injection(description, emoji, set_name)
            else:
                # Vision failed -- use emoji as fallback
                event.text = build_sticker_injection(
                    f"a sticker with emoji {emoji}" if emoji else "a sticker",
                    emoji, set_name,
                )
        except Exception as e:
            logger.warning("[Telegram] Sticker analysis error: %s", e, exc_info=True)
            event.text = build_sticker_injection(
                f"a sticker with emoji {emoji}" if emoji else "a sticker",
                emoji, set_name,
            )

    def _reload_dm_topics_from_config(self) -> None:
        """Re-read dm_topics from config.yaml and load any new thread_ids into cache.

        This allows topics created externally (e.g. by the agent via API) to be
        recognized without a gateway restart.
        """
        try:
            from hermes_constants import get_hermes_home
            config_path = get_hermes_home() / "config.yaml"
            if not config_path.exists():
                return

            import yaml as _yaml
            with open(config_path, "r", encoding="utf-8") as f:
                config = _yaml.safe_load(f) or {}

            dm_topics = (
                config.get("platforms", {})
                .get("telegram", {})
                .get("extra", {})
                .get("dm_topics", [])
            )
            if not dm_topics:
                return

            # Update in-memory config and cache any new thread_ids
            self._dm_topics_config = dm_topics
            for chat_entry in dm_topics:
                cid = chat_entry.get("chat_id")
                if not cid:
                    continue
                for t in chat_entry.get("topics", []):
                    tid = t.get("thread_id")
                    name = t.get("name")
                    if tid and name:
                        cache_key = f"{cid}:{name}"
                        if cache_key not in self._dm_topics:
                            self._dm_topics[cache_key] = int(tid)
                            logger.info(
                                "[%s] Hot-loaded DM topic from config: %s -> thread_id=%s",
                                self.name, cache_key, tid,
                            )
        except Exception as e:
            logger.debug("[%s] Failed to reload dm_topics from config: %s", self.name, e)

    def _get_dm_topic_info(self, chat_id: str, thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Look up DM topic config by chat_id and thread_id.

        Returns the topic config dict (name, skill, etc.) if this thread_id
        matches a known DM topic, or None.
        """
        if not thread_id:
            return None

        thread_id_int = int(thread_id)

        # Check cached topics first (created by us or loaded at startup)
        for key, cached_tid in self._dm_topics.items():
            if cached_tid == thread_id_int and key.startswith(f"{chat_id}:"):
                topic_name = key.split(":", 1)[1]
                # Find the full config for this topic
                for chat_entry in self._dm_topics_config:
                    if str(chat_entry.get("chat_id")) == chat_id:
                        for t in chat_entry.get("topics", []):
                            if t.get("name") == topic_name:
                                return t
                return {"name": topic_name}

        # Not in cache — hot-reload config in case topics were added externally
        self._reload_dm_topics_from_config()

        # Check cache again after reload
        for key, cached_tid in self._dm_topics.items():
            if cached_tid == thread_id_int and key.startswith(f"{chat_id}:"):
                topic_name = key.split(":", 1)[1]
                for chat_entry in self._dm_topics_config:
                    if str(chat_entry.get("chat_id")) == chat_id:
                        for t in chat_entry.get("topics", []):
                            if t.get("name") == topic_name:
                                return t
                return {"name": topic_name}

        return None

    def _cache_dm_topic_from_message(self, chat_id: str, thread_id: str, topic_name: str) -> None:
        """Cache a thread_id -> topic_name mapping discovered from an incoming message."""
        cache_key = f"{chat_id}:{topic_name}"
        if cache_key not in self._dm_topics:
            self._dm_topics[cache_key] = int(thread_id)
            logger.info(
                "[%s] Cached DM topic from message: %s -> thread_id=%s",
                self.name, cache_key, thread_id,
            )

    def _build_message_event(
        self,
        message: Message,
        msg_type: MessageType,
        update_id: Optional[int] = None,
    ) -> MessageEvent:
        """Build a MessageEvent from a Telegram message.

        ``update_id`` is the ``Update.update_id`` from PTB; passing it through
        lets ``/restart`` record the triggering offset so the new gateway
        process can advance past it (prevents ``/restart`` being re-delivered
        when PTB's graceful-shutdown ACK fails).
        """
        chat = message.chat
        user = message.from_user
        
        # Determine chat type
        chat_type = "dm"
        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            chat_type = "group"
        elif chat.type == ChatType.CHANNEL:
            chat_type = "channel"

        # Resolve DM topic name and skill binding
        thread_id_raw = message.message_thread_id
        thread_id_str = str(thread_id_raw) if thread_id_raw is not None else None
        if chat_type == "group" and thread_id_str is None and getattr(chat, "is_forum", False):
            thread_id_str = self._GENERAL_TOPIC_THREAD_ID
        chat_topic = None
        topic_skill = None

        if chat_type == "dm" and thread_id_str:
            topic_info = self._get_dm_topic_info(str(chat.id), thread_id_str)
            if topic_info:
                chat_topic = topic_info.get("name")
                topic_skill = topic_info.get("skill")

            # Also check forum_topic_created service message for topic discovery
            if hasattr(message, "forum_topic_created") and message.forum_topic_created:
                created_name = message.forum_topic_created.name
                if created_name:
                    self._cache_dm_topic_from_message(str(chat.id), thread_id_str, created_name)
                    if not chat_topic:
                        chat_topic = created_name

        elif chat_type == "group" and thread_id_str:
            # Group/supergroup forum topic skill binding via config.extra['group_topics']
            group_topics_config: list = self.config.extra.get("group_topics", [])
            for chat_entry in group_topics_config:
                if str(chat_entry.get("chat_id", "")) == str(chat.id):
                    for topic in chat_entry.get("topics", []):
                        tid = topic.get("thread_id")
                        if tid is not None and str(tid) == thread_id_str:
                            chat_topic = topic.get("name")
                            topic_skill = topic.get("skill")
                            break
                    break

        # Build source
        source = self.build_source(
            chat_id=str(chat.id),
            chat_name=getattr(chat, "title", None) or getattr(chat, "full_name", None),
            chat_type=chat_type,
            user_id=str(user.id) if user else (str(chat.id) if chat_type == "dm" else None),
            user_name=getattr(user, "full_name", None) if user else (getattr(chat, "full_name", None) if chat_type == "dm" else None),
            thread_id=thread_id_str,
            chat_topic=chat_topic,
        )
        self._cache_observed_chat_type(str(chat.id), chat_type)
        
        # Extract reply context if this message is a reply.
        # Prefer Telegram's native partial quote (message.quote, TextQuote)
        # so a user replying to a single selected substring of a prior
        # multi-section message doesn't get the whole replied-to message
        # injected into the agent's context — which can cause the agent
        # to act on unrelated actionable-looking text the user didn't
        # quote (#22619). Fall back to the full replied-to message text
        # / caption when no native quote is present.
        reply_to_id = None
        reply_to_text = None
        if message.reply_to_message:
            reply_to_id = str(message.reply_to_message.message_id)
            quote = getattr(message, "quote", None)
            quote_text = getattr(quote, "text", None) if quote is not None else None
            if quote_text:
                reply_to_text = quote_text
            else:
                reply_to_text = (
                    message.reply_to_message.text
                    or message.reply_to_message.caption
                    or None
                )

        # Per-channel/topic ephemeral prompt
        from gateway.platforms.base import resolve_channel_prompt
        _chat_id_str = str(chat.id)
        _channel_prompt = resolve_channel_prompt(
            self.config.extra,
            thread_id_str or _chat_id_str,
            _chat_id_str if thread_id_str else None,
        )

        return MessageEvent(
            text=message.text or "",
            message_type=msg_type,
            source=source,
            raw_message=message,
            message_id=str(message.message_id),
            platform_update_id=update_id,
            reply_to_message_id=reply_to_id,
            reply_to_text=reply_to_text,
            auto_skill=topic_skill,
            channel_prompt=_channel_prompt,
            timestamp=message.date,
        )

    # ── Message reactions (processing lifecycle) ──────────────────────────

    def _reactions_enabled(self) -> bool:
        """Check if message reactions are enabled via config/env."""
        return os.getenv("TELEGRAM_REACTIONS", "false").lower() not in ("false", "0", "no")

    async def _set_reaction(self, chat_id: str, message_id: str, emoji: str) -> bool:
        """Set a single emoji reaction on a Telegram message."""
        if not self._bot:
            return False
        try:
            await self._bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=int(message_id),
                reaction=emoji,
            )
            return True
        except Exception as e:
            logger.debug("[%s] set_message_reaction failed (%s): %s", self.name, emoji, e)
            return False

    async def on_processing_start(self, event: MessageEvent) -> None:
        """Add an in-progress reaction when message processing begins."""
        if not self._reactions_enabled():
            return
        chat_id = getattr(event.source, "chat_id", None)
        message_id = getattr(event, "message_id", None)
        if chat_id and message_id:
            await self._set_reaction(chat_id, message_id, "\U0001f440")

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        """Swap the in-progress reaction for a final success/failure reaction.

        Unlike Discord (additive reactions), Telegram's set_message_reaction
        replaces all existing reactions in one call — no remove step needed.
        """
        if not self._reactions_enabled():
            return
        chat_id = getattr(event.source, "chat_id", None)
        message_id = getattr(event, "message_id", None)
        if chat_id and message_id and outcome != ProcessingOutcome.CANCELLED:
            await self._set_reaction(
                chat_id,
                message_id,
                "\U0001f44d" if outcome == ProcessingOutcome.SUCCESS else "\U0001f44e",
            )
