"""
Telegram platform adapter.

Uses python-telegram-bot library for:
- Receiving messages from users/groups
- Sending responses back
- Handling media and commands
"""

import asyncio
import dataclasses
import inspect
import json
import logging
import os
import subprocess
import tempfile
import html as _html
import hashlib
import re
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Any

logger = logging.getLogger(__name__)


def _redact_telegram_error_text(error: object) -> str:
    """Redact Telegram transport credentials regardless of debug settings."""
    text = "" if error is None else str(error)
    if not text:
        return text
    try:
        from agent.redact import redact_sensitive_text

        return redact_sensitive_text(text, force=True)
    except Exception:
        return "<telegram error redacted>"

SUBC_DEFAULT_ROOM = Path("/home/hermes/.hermes/profiles/subc/room")
SUBC_DEFAULT_PROJECT = Path("/home/hermes/workspace/chip-subconscious")

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
from gateway import goal_launch
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
    cache_media_bytes,
    classify_send_error,
    resolve_proxy_url,
    SUPPORTED_VIDEO_TYPES,
    SUPPORTED_DOCUMENT_TYPES,
    SUPPORTED_IMAGE_DOCUMENT_TYPES,
    TEXT_DOCUMENT_EXTENSIONS,
    utf16_len,
)
from gateway.platforms.helpers import strip_markdown
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

_SUPERGOAL_REPLY_DOCUMENT_MAX_BYTES = 100 * 1024


MAX_COMMANDS_PER_SCOPE = 30


def check_telegram_requirements() -> bool:
    """Check if Telegram dependencies are available.

    If python-telegram-bot is missing, attempts to lazy-install it via
    ``tools.lazy_deps.ensure("platform.telegram")``. After a successful
    install, re-imports the SDK and flips ``TELEGRAM_AVAILABLE`` to True
    so the adapter's class-level type aliases get rebound.
    """
    global TELEGRAM_AVAILABLE, Update, Bot, Message, InlineKeyboardButton
    global InlineKeyboardMarkup, LinkPreviewOptions, Application
    global CommandHandler, CallbackQueryHandler, TelegramMessageHandler
    global ContextTypes, filters, ParseMode, ChatType, HTTPXRequest
    if TELEGRAM_AVAILABLE:
        return True
    try:
        from tools.lazy_deps import ensure as _lazy_ensure
        _lazy_ensure("platform.telegram", prompt=False)
    except Exception:
        return False
    try:
        from telegram import Update as _Update, Bot as _Bot, Message as _Message
        from telegram import InlineKeyboardButton as _IKB, InlineKeyboardMarkup as _IKM
        try:
            from telegram import LinkPreviewOptions as _LPO
        except ImportError:
            _LPO = None
        from telegram.ext import (
            Application as _App, CommandHandler as _CH,
            CallbackQueryHandler as _CQH,
            MessageHandler as _MH,
            ContextTypes as _CT, filters as _filters,
        )
        from telegram.constants import ParseMode as _PM, ChatType as _CtT
        from telegram.request import HTTPXRequest as _HR
    except ImportError:
        return False
    Update = _Update
    Bot = _Bot
    Message = _Message
    InlineKeyboardButton = _IKB
    InlineKeyboardMarkup = _IKM
    LinkPreviewOptions = _LPO
    Application = _App
    CommandHandler = _CH
    CallbackQueryHandler = _CQH
    TelegramMessageHandler = _MH
    ContextTypes = _CT
    filters = _filters
    ParseMode = _PM
    ChatType = _CtT
    HTTPXRequest = _HR
    TELEGRAM_AVAILABLE = True
    return True


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
    # Remove standard markdown bold (**text** → text) BEFORE MarkdownV2 bold
    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)
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
    "Нужный путь — external preview bridge with exact-message verify-gate."
)
_TG_PREVIEW_GUARD_CHAT_IDS: set[str] = set()
_TG_PREVIEW_PENDING_ECHO_FILE = "/tmp/tg_preview_echo_pending.jsonl"
_HUMAN20_CTA_TEXT = "перейти в @human20"
_HUMAN20_CTA_URL = "https://t.me/human20"
_INLINE_TG_OPERATOR_PREFIX_RE = re.compile(
    r"(?i)^\s*(готово|сделал|отправил|принял|да|нет|ок|ошибка|блокер|превью\s+не|smoke|proof)\b"
)


def _looks_like_inline_tg_preview(text: str) -> bool:
    """Return True for finished Telegram-post drafts that must not be bot-sent.

    In Configured `/tg` chats, any finished TG preview or edited post must be
    delivered by the human account (the configured external preview bridge), not by the
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
    # the preview bridge, never emitted by the Hermes bot as a final response.
    short_title = 3 <= len(first_line) <= 120 and not first_line.startswith(("/", "#"))
    has_multiple_tg_blocks = has_tg_block_separator and has_post_body
    return bool(short_title and has_multiple_tg_blocks and (has_source_label or has_markdown_source))


def _tg_preview_visible_text(text: str) -> str:
    """Normalize a TG preview caption/body for echo fingerprinting.

    `send-preview.sh` writes HTML, but Telegram delivers the same external-preview
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

    rendered_groups: list[str] = []
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

        # Build the bulleted lines for this row.  Skip any bullet whose value
        # duplicates the heading text -- when has_row_label_col is False the
        # heading IS the first data cell, and emitting it twice (once as the
        # bold heading, once as the first bullet) is visual noise.
        bullets: list[str] = []
        for header, value in zip(headers, data_cells):
            if not has_row_label_col and value == heading:
                continue
            bullets.append(f"• {header}: {value}")

        # Within a row-group: single newline between heading and its bullets,
        # and between successive bullets.  This keeps the row visually tight
        # on Telegram instead of stretching each bullet into its own paragraph.
        group_lines = [f"**{heading}**", *bullets]
        rendered_groups.append("\n".join(group_lines))

    # Between row-groups: blank line so each group reads as a distinct block.
    return "\n\n".join(rendered_groups)


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
    supports_code_blocks = True  # Telegram MarkdownV2 renders fenced code blocks
    # Bot API 10.1 Rich Messages cap the raw markdown/html text at 32,768
    # UTF-8 characters. Content above this is sent via the legacy chunking path.
    RICH_MESSAGE_MAX_CHARS = 32768
    # Backwards-compatible alias for tests/external callers that referenced the
    # initial implementation name. The API limit is character-based, not bytes.
    RICH_MESSAGE_MAX_BYTES = RICH_MESSAGE_MAX_CHARS
    # Threshold for detecting Telegram client-side message splits.
    # When a chunk is near this limit, a continuation is almost certain.
    _SPLIT_THRESHOLD = 4000
    MEDIA_GROUP_WAIT_SECONDS = 0.8
    _GENERAL_TOPIC_THREAD_ID = "1"

    # Telegram's edit_message applies MarkdownV2 formatting only on the
    # finalize=True path.  Without this flag, stream_consumer._send_or_edit
    # short-circuits when the raw text is unchanged between the last streamed
    # edit and the final edit, skipping the plain-text → MarkdownV2 conversion.
    # Fixes #25710.
    REQUIRES_EDIT_FINALIZE: bool = True

    # Adaptive text-batch ingress: short messages need a tighter delay so the
    # first token reaches the agent fast.  Numbers tuned for "feels instant":
    # ≤320 codepoints (one short paragraph) settles in ~180ms; ≤1024
    # (a normal paragraph) in ~240ms; longer waits the configured cap.
    # Always clamped to ``_text_batch_delay_seconds`` so an operator can lower
    # the cap further via env var.
    _TEXT_BATCH_FAST_LEN = 320
    _TEXT_BATCH_FAST_DELAY_S = 0.18
    _TEXT_BATCH_SHORT_LEN = 1024
    _TEXT_BATCH_SHORT_DELAY_S = 0.24

    @staticmethod
    def _env_float_clamped(
        name: str,
        default: float,
        *,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
    ) -> float:
        """Read a float env var, reject non-finite values, and clamp to bounds.

        Guarantees the returned value is a finite number usable directly in
        ``asyncio.sleep()`` and similar APIs that reject NaN / Inf.
        """
        import math

        raw = os.getenv(name)
        try:
            value = float(raw) if raw is not None else float(default)
        except (TypeError, ValueError):
            value = float(default)
        if not math.isfinite(value):
            value = float(default)
        if min_value is not None:
            value = max(value, min_value)
        if max_value is not None:
            value = min(value, max_value)
        return value

    @property
    def message_len_fn(self):
        """Telegram measures message length in UTF-16 code units."""
        return utf16_len

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.TELEGRAM)
        self._app: Optional[Application] = None
        self._bot: Optional[Bot] = None
        self._webhook_mode: bool = False
        self._mention_patterns = self._compile_mention_patterns()
        self._reply_to_mode: str = getattr(config, 'reply_to_mode', 'first') or 'first'
        self._disable_link_previews: bool = self._coerce_bool_extra("disable_link_previews", False)
        # Optional private gate for chats where rich messages are desired.
        # If unset, the capability-based rich path may apply globally when
        # rich messages are explicitly enabled; if set, only listed chats use
        # the private rich helpers / edit path.
        self._rich_message_chat_ids: Set[str] = self._coerce_str_set_extra("rich_message_chats")
        self._rich_message_min_chars: int = self._coerce_int_extra("rich_message_min_chars", 500)
        # Bot API 10.1 Rich Messages: when explicitly enabled, send final
        # replies via sendRichMessage with the raw agent markdown so
        # tables/task lists/etc. render natively. Disabled by default because
        # several Telegram clients accept but render rich messages poorly.
        self._rich_messages_enabled: bool = self._coerce_bool_extra("rich_messages", False)
        # Latched off after a capability failure on sendRichMessage /
        # sendRichMessageDraft (e.g. older python-telegram-bot without the
        # endpoint) so later sends skip the doomed rich attempt entirely.
        self._rich_send_disabled: bool = False
        self._rich_draft_disabled: bool = False
        # Buffer rapid/album photo updates so Telegram image bursts are handled
        # as a single MessageEvent instead of self-interrupting multiple turns.
        self._media_batch_delay_seconds = float(os.getenv("HERMES_TELEGRAM_MEDIA_BATCH_DELAY_SECONDS", "0.8"))
        self._pending_photo_batches: Dict[str, MessageEvent] = {}
        self._pending_photo_batch_tasks: Dict[str, asyncio.Task] = {}
        self._media_group_events: Dict[str, MessageEvent] = {}
        self._media_group_tasks: Dict[str, asyncio.Task] = {}
        # Buffer rapid text messages so Telegram client-side splits of long
        # messages are aggregated into a single MessageEvent.  Lower defaults
        # (0.3s / 1.0s instead of 0.6s / 2.0s) let short replies stream
        # without a noticeable wait — combined with the adaptive fast-path
        # in ``_calc_text_batch_delay`` below, ≤320-codepoint replies settle
        # in ~180ms.  All bounds are conservative for Telegram's
        # ~1 edit/s flood envelope.
        self._text_batch_delay_seconds = self._env_float_clamped(
            "HERMES_TELEGRAM_TEXT_BATCH_DELAY_SECONDS",
            0.3,
            min_value=0.08,
            max_value=2.0,
        )
        self._text_batch_split_delay_seconds = self._env_float_clamped(
            "HERMES_TELEGRAM_TEXT_BATCH_SPLIT_DELAY_SECONDS",
            1.0,
            min_value=self._text_batch_delay_seconds,
            max_value=4.0,
        )
        self._pending_text_batches: Dict[str, MessageEvent] = {}
        self._pending_text_batch_tasks: Dict[str, asyncio.Task] = {}
        # Some Telegram clients / Bot API surfaces deliver the native voice
        # transcription as a follow-up text message right after the voice
        # update. Hermes already transcribes the voice internally, so letting
        # that text through creates a second user turn that looks like a
        # visible transcript echo.
        self._recent_voice_message_keys: Dict[tuple[str, str], float] = {}
        self._polling_error_task: Optional[asyncio.Task] = None
        self._polling_conflict_count: int = 0
        self._polling_network_error_count: int = 0
        self._polling_error_callback_ref = None
        # After sustained reconnect storms the PTB httpx pool can return
        # SendResult(success=True) for sends that never actually transmit.
        # _handle_polling_network_error sets this; _verify_polling_after_reconnect
        # clears it once getMe() confirms the Bot client is healthy.
        # While True, send() short-circuits to a failure so callers
        # (cron live-adapter branch) fall through to standalone delivery.
        self._send_path_degraded: bool = False
        self._general_request_drain_lock = asyncio.Lock()
        # DM Topics: map of topic_name -> message_thread_id (populated at startup)
        self._dm_topics: Dict[str, int] = {}
        # Track forum chats where we've already registered bot commands
        self._forum_command_registered: set[int] = set()
        # Lock per la registrazione sicura dei comandi nei forum supergroup
        self._forum_lock = asyncio.Lock()
        # DM Topics config from extra.dm_topics
        self._dm_topics_config: List[Dict[str, Any]] = self.config.extra.get("dm_topics", [])
        # Precomputed chat_ids that have DM topics configured (for O(1) root-DM ignore check)
        self._dm_topic_chat_ids: Set[str] = {
            str(e["chat_id"]) for e in self._dm_topics_config if "chat_id" in e
        }
        # Document size cap. Telegram's public Bot API caps getFile at 20MB; a
        # locally-hosted telegram-bot-api server (configured via extra.base_url)
        # raises that to 2GB, so the presence of base_url is the opt-in.
        self._max_doc_bytes: int = (
            2 * 1024 * 1024 * 1024
            if self.config.extra.get("base_url")
            else 20 * 1024 * 1024
        )
        # Inline preview guard: fail closed when a configured chat would receive
        # a finished `/tg` preview from the Hermes bot instead of the required
        # human-account sender path.
        self._inline_preview_guard: Dict[str, Any] = self._load_inline_preview_guard()
        self._auto_skill_routes: List[Dict[str, Any]] = self._load_auto_skill_routes()
        # Interactive model picker state per chat
        self._model_picker_state: Dict[str, dict] = {}
        # Approval button state: message_id → session_key
        self._approval_state: Dict[int, str] = {}
        # Slash-confirm button state: confirm_id → session_key (for /reload-mcp
        # and any other slash-confirm prompts; see GatewayRunner._request_slash_confirm).
        self._slash_confirm_state: Dict[str, str] = {}
        # Clarify button state: clarify_id → session_key (for the clarify tool's
        # multiple-choice prompts; see GatewayRunner clarify_callback wiring).
        self._clarify_state: Dict[str, str] = {}
        # Notification mode for message sends.
        # "important" — only final responses, approvals, and slash confirmations
        #               trigger notifications; tool progress, streaming, status
        #               messages are delivered silently via disable_notification.
        #               This is the default — Telegram users found per-tool-call
        #               push notifications too noisy.
        # "all"       — every message triggers a push notification (legacy
        #               behavior; opt-in via display.platforms.telegram.notifications).
        self._notifications_mode: str = "important"
        # send_or_update_status() bookkeeping: {(chat_id, status_key) -> bot message_id}
        # Tracks status bubbles owned by this adapter so subsequent calls with the
        # same key edit the same message instead of appending new ones (#30045).
        self._status_message_ids: Dict[tuple, str] = {}

    def _is_transcribe_route_chat(self, chat_id: Any) -> bool:
        """Return True when a chat is configured as a Telegram transcribe route."""
        routes = self.config.extra.get("transcribe_routes") or []
        if isinstance(routes, (str, int)):
            routes = [{"chat_id": routes, "enabled": True}]
        if not isinstance(routes, list):
            return False
        target = str(chat_id)
        for route in routes:
            if isinstance(route, dict):
                if route.get("enabled", True) is False:
                    continue
                if str(route.get("chat_id")) == target:
                    return True
            elif str(route) == target:
                return True
        return False

    def _telegram_chip_media_download_sync(self, chat_id: Any, message_id: Any, ext: str) -> str:
        """Download an exact Telegram message media through the local telegram-chip API."""
        safe_ext = ext if ext.startswith(".") and len(ext) <= 12 else ".bin"
        safe_chat = re.sub(r"[^0-9A-Za-z_-]+", "_", str(chat_id))
        out_path = f"/var/tmp/hermes_tgchip_{safe_chat}_{message_id}_{uuid.uuid4().hex[:10]}{safe_ext}"
        url = (
            "http://127.0.0.1:8080/chats/"
            + urllib.parse.quote(str(chat_id), safe="")
            + f"/messages/{message_id}/media?"
            + urllib.parse.urlencode({"output_path": out_path})
        )
        raw = urllib.request.urlopen(url, timeout=300).read().decode("utf-8")
        obj = json.loads(raw)
        if not obj.get("success"):
            raise RuntimeError(obj.get("error") or raw[:300])
        data = obj.get("data")
        if isinstance(data, str):
            try:
                data_obj = json.loads(data)
            except Exception:
                data_obj = {}
        elif isinstance(data, dict):
            data_obj = data
        else:
            data_obj = {}
        path = str(data_obj.get("path") or out_path)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return path

    def _extract_audio_for_transcribe_sync(self, media_path: str) -> str:
        """Extract a small mono MP3 for STT from a recovered video/audio container."""
        src = Path(media_path)
        if not src.exists():
            raise FileNotFoundError(media_path)
        out_path = str(src.with_suffix(".transcribe.mp3"))
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
            "-b:a", "32k", out_path,
        ]
        subprocess.run(cmd, check=True, timeout=300)
        if not os.path.exists(out_path):
            raise FileNotFoundError(out_path)
        return out_path

    _TME_C_LINK_RE = re.compile(r"https?://t\.me/c/(?P<chat>\d+)(?:/\d+)?/(?P<message>\d+)(?:\?[^\s]+)?", re.IGNORECASE)

    @classmethod
    def _extract_private_tme_c_links(cls, text: str | None) -> list[tuple[str, int]]:
        """Extract private Telegram t.me/c links as full chat id + final message id."""
        out: list[tuple[str, int]] = []
        if not text:
            return out
        for match in cls._TME_C_LINK_RE.finditer(text):
            try:
                out.append((f"-100{match.group('chat')}", int(match.group('message'))))
            except Exception:
                continue
        return out

    def _telegram_chip_fetch_message_sync(self, chat_id: Any, message_id: Any) -> dict:
        """Fetch exact message metadata through the local telegram-chip API."""
        url = (
            "http://127.0.0.1:8080/chats/"
            + urllib.parse.quote(str(chat_id), safe="")
            + f"/messages/{message_id}"
        )
        raw = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
        obj = json.loads(raw)
        if not obj.get("success"):
            raise RuntimeError(obj.get("error") or raw[:300])
        data = obj.get("data")
        if isinstance(data, str):
            return json.loads(data)
        if isinstance(data, dict):
            return data
        raise RuntimeError(f"Unexpected telegram-chip message payload: {type(data).__name__}")

    async def _recover_transcribe_route_tme_link_via_telegram_chip(
        self,
        event: MessageEvent,
        current_chat_id: Any,
    ) -> bool:
        """Recover media from private t.me/c links posted into transcribe-route chats.

        Bot API events only expose the link text; the linked media may live in a
        different private group/topic. For configured transcription dropboxes,
        resolve the link through the shared telegram-chip runtime and attach the
        downloaded media to the event before the LLM runs.
        """
        if not self._is_transcribe_route_chat(current_chat_id):
            return False
        links = self._extract_private_tme_c_links(event.text)
        if not links:
            return False
        errors: list[str] = []
        for linked_chat_id, linked_message_id in links:
            try:
                meta = await asyncio.to_thread(
                    self._telegram_chip_fetch_message_sync,
                    linked_chat_id,
                    linked_message_id,
                )
                if not meta.get("has_media"):
                    event.text = (
                        f"[Telegram private link was fetched via telegram-chip: "
                        f"chat={linked_chat_id} message={linked_message_id}; no media found. "
                        f"Visible text: {meta.get('text') or ''}]\n\n{event.text or ''}"
                    ).strip()
                    continue
                media_type = str(meta.get("media_type") or "")
                ext = ".mp4" if "Document" in media_type or "Video" in media_type else ".mp3" if "Audio" in media_type else ".bin"
                path = await asyncio.to_thread(
                    self._telegram_chip_media_download_sync,
                    linked_chat_id,
                    linked_message_id,
                    ext,
                )
                if path.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
                    audio_path = await asyncio.to_thread(self._extract_audio_for_transcribe_sync, path)
                    path = audio_path
                    mime_type = "audio/mpeg"
                    msg_type = MessageType.VOICE
                elif path.lower().endswith((".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac", ".opus")):
                    mime_type = "audio/mpeg"
                    msg_type = MessageType.VOICE
                else:
                    mime_type = "application/octet-stream"
                    msg_type = MessageType.DOCUMENT
                event.media_urls = [path]
                event.media_types = [mime_type]
                event.message_type = msg_type
                event.text = (
                    "[Telegram private t.me/c media recovered via telegram-chip. "
                    f"Source: chat={linked_chat_id} message={linked_message_id}. "
                    f"Recovered local file path: {path}. "
                    "Transcribe this recovered file now. The gateway will send the transcript file; "
                    "return only a concise Russian /summ-style summary for the second file. "
                    "Do not include the full transcript in the chat response.]"
                )
                logger.info(
                    "[Telegram] Recovered transcribe-route private link media via telegram-chip: "
                    "source_chat=%s source_message=%s path=%s",
                    linked_chat_id,
                    linked_message_id,
                    path,
                )
                return True
            except Exception as exc:
                errors.append(f"{linked_chat_id}/{linked_message_id}: {exc}")
                logger.warning(
                    "[Telegram] Failed private t.me/c telegram-chip recovery: source_chat=%s source_message=%s error=%s",
                    linked_chat_id,
                    linked_message_id,
                    exc,
                    exc_info=True,
                )
        if errors:
            event.text = (
                "[Telegram private t.me/c recovery through telegram-chip was attempted and failed: "
                + "; ".join(errors[:3])
                + "]\n\n"
                + (event.text or "")
            )
        return False

    async def _recover_transcribe_route_media_via_telegram_chip(
        self,
        msg: Any,
        event: MessageEvent,
        *,
        ext: str,
        mime_type: str,
        message_type: MessageType,
        reason: str,
    ) -> bool:
        """Recover oversized transcribe-route media when Bot API getFile fails."""
        chat_id = getattr(getattr(msg, "chat", None), "id", None)
        message_id = getattr(msg, "message_id", None)
        if chat_id is None or message_id is None:
            return False
        if not self._is_transcribe_route_chat(chat_id):
            return False
        try:
            path = await asyncio.to_thread(
                self._telegram_chip_media_download_sync,
                chat_id,
                message_id,
                ext,
            )
            if message_type == MessageType.VIDEO or str(mime_type).startswith("video/"):
                path = await asyncio.to_thread(self._extract_audio_for_transcribe_sync, path)
                mime_type = "audio/mpeg"
                message_type = MessageType.VOICE
            elif message_type == MessageType.AUDIO or str(mime_type).startswith("audio/"):
                message_type = MessageType.VOICE
            event.media_urls = [path]
            event.media_types = [mime_type]
            event.message_type = message_type
            if not event.text:
                event.text = (
                    "[Telegram transcribe-route media recovered via telegram-chip. "
                    f"Recovered local file path: {path}. "
                    "Transcribe this file now. The gateway will send transcript and summary as files; "
                    "return only a concise Russian /summ-style summary.]"
                )
            logger.info(
                "[Telegram] Recovered transcribe-route media via telegram-chip: chat=%s message=%s path=%s reason=%s",
                chat_id,
                message_id,
                path,
                reason,
            )
            return True
        except Exception as recover_error:
            logger.warning(
                "[Telegram] Failed telegram-chip recovery for transcribe-route media: chat=%s message=%s reason=%s error=%s",
                chat_id,
                message_id,
                reason,
                recover_error,
                exc_info=True,
            )
            return False

    def _load_auto_skill_routes(self) -> List[Dict[str, Any]]:
        """Normalize declarative chat/media-to-skill routes."""
        routes: List[Dict[str, Any]] = []
        raw_routes = self.config.extra.get("auto_skill_routes", [])
        if not isinstance(raw_routes, list):
            return routes
        for raw in raw_routes:
            if not isinstance(raw, dict):
                continue
            skill = str(raw.get("skill") or "").strip().lstrip("/")
            chats = raw.get("chats") or []
            match = raw.get("match") or {}
            if not skill or not isinstance(match, dict):
                continue
            if isinstance(chats, (str, int)):
                chats = [chats]
            chat_ids = {str(item).strip() for item in chats if str(item).strip()}
            media = match.get("media") or []
            if isinstance(media, str):
                media = [media]
            route = {
                "skill": skill,
                "chats": chat_ids,
                "match_urls": bool(match.get("urls", False)),
                "match_media": {str(item).strip().lower() for item in media if str(item).strip()},
            }
            if chat_ids and (route["match_urls"] or route["match_media"]):
                routes.append(route)
        return routes

    def _auto_skill_prefix_for_text(self, chat_id: Any, text: str) -> Optional[str]:
        content = str(text or "").strip()
        if not content or content.startswith("/") or _looks_like_inline_tg_preview(content):
            return None
        has_url = bool(re.search(r"https?://\S+", content, re.IGNORECASE))
        for route in getattr(self, "_auto_skill_routes", []) or []:
            if str(chat_id) in route["chats"] and route["match_urls"] and has_url:
                return f"/{route['skill']} "
        return None

    def _auto_skill_prefix_for_media(
        self,
        chat_id: Any,
        message_type: MessageType,
    ) -> Optional[str]:
        media_name = str(getattr(message_type, "value", message_type)).strip().lower()
        for route in getattr(self, "_auto_skill_routes", []) or []:
            if str(chat_id) in route["chats"] and media_name in route["match_media"]:
                return f"/{route['skill']} "
        return None

    def _load_inline_preview_guard(self) -> Dict[str, Any]:
        """Load per-chat guard against Hermes-bot `/tg` previews.

        operator's TG chats are fail-closed by default in this install: finished
        post previews must be routed through the configured external preview bridge even when the
        config entry is missing or incomplete. Config can still add chats or
        override script/timeout, but not silently disable the configured preview
        guard unless HERMES_DISABLE_TG_PREVIEW_GUARD=1 is set for tests.
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
        if os.getenv("HERMES_DISABLE_TG_PREVIEW_GUARD", "0") != "1":
            chat_set |= set(_TG_PREVIEW_GUARD_CHAT_IDS)
        enabled = bool(raw.get("enabled", False)) or bool(chat_set & _TG_PREVIEW_GUARD_CHAT_IDS)
        return {
            "enabled": enabled,
            "chats": chat_set,
            "blocker": str(raw.get("blocker") or _INLINE_TG_PREVIEW_BLOCKER),
            "action": str(raw.get("action") or "external_preview"),
            "script": str(raw.get("script") or ""),
            "timeout": float(raw.get("timeout", 120)),
            "echo_user_ids": {str(v).strip() for v in (raw.get("echo_user_ids") or []) if str(v).strip()},
            "echo_usernames": {str(v).strip().lstrip("@").lower() for v in (raw.get("echo_usernames") or []) if str(v).strip()},
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

    def _send_inline_preview_via_external_bridge_sync(
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
                return SendResult(success=False, error=f"Preview bridge send failed: {detail[:500]}")
            message_id = self._extract_tg_preview_state_message_id()
            if not message_id:
                return SendResult(
                    success=False,
                    error="Preview bridge send did not leave verified /tmp/tg_preview_state.json",
                )
            return SendResult(
                success=True,
                message_id=message_id,
                raw_response={
                    "inline_preview_guard": "external_preview",
                    "stdout": stdout[-1000:],
                },
            )
        except Exception as exc:
            return SendResult(success=False, error=f"Preview bridge guard exception: {exc}")
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
        """Route configured inline `/tg` previews through an external preview bridge instead of bot-send."""
        if not self._inline_preview_guard_applies(chat_id, content):
            return None
        guard = getattr(self, "_inline_preview_guard", None) or {}
        action = str(guard.get("action") or "blocker").strip().lower()
        if action not in {"external_preview", "external_preview", "send_external_preview"}:
            return None
        result = await asyncio.to_thread(
            self._send_inline_preview_via_external_bridge_sync,
            chat_id,
            content,
            metadata,
        )
        if result.success:
            logger.info(
                "[%s] Routed inline TG preview through external preview guard (chat=%s message_id=%s)",
                self.name,
                chat_id,
                result.message_id,
            )
            return result
        self._last_inline_preview_guard_error = result.error
        logger.error(
            "[%s] External inline TG preview guard failed (chat=%s): %s",
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
    def _recent_external_preview_message_ids() -> set[str]:
        """Return exact message ids sent by the external `/tg` preview path.

        `the external preview bridge sends previews from a non-bot account, so Telegram
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
    def _pending_external_preview_echo_fingerprints(chat_id: str) -> set[str]:
        """Return still-valid pre-send preview fingerprints for this chat.

        The exact-id guard is not enough: Telegram can deliver the external-preview echo
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

    def _is_external_tg_preview_echo(self, message: Message) -> bool:
        """Return True for external preview messages that must be ignored.

        `the external preview bridge posts from a non-bot account, so Bot API receives the
        preview as a normal user message. Exact ids are preferred; pending
        fingerprints cover the race before exact state is visible.
        """
        chat_id = str(getattr(getattr(message, "chat", None), "id", ""))
        guard = getattr(self, "_inline_preview_guard", None) or {}
        guarded_chats = guard.get("chats") or set(_TG_PREVIEW_GUARD_CHAT_IDS)
        if guarded_chats and chat_id not in guarded_chats:
            return False

        user = getattr(message, "from_user", None)
        user_id = str(getattr(user, "id", "") or "")
        username = str(getattr(user, "username", "") or "").lstrip("@").lower()
        allowed_user_ids = guard.get("echo_user_ids") or set()
        allowed_usernames = guard.get("echo_usernames") or set()
        if (allowed_user_ids or allowed_usernames) and user_id not in allowed_user_ids and username not in allowed_usernames:
            return False

        message_id = str(getattr(message, "message_id", "") or "")
        if message_id and message_id in self._recent_external_preview_message_ids():
            return True

        visible_text = getattr(message, "caption", None) or getattr(message, "text", None) or ""
        if not _looks_like_inline_tg_preview(visible_text):
            return False
        pending = self._pending_external_preview_echo_fingerprints(chat_id)
        return bool(pending and _tg_preview_echo_fingerprint(visible_text) in pending)

    @staticmethod
    def _self_echo_normalize(text: Optional[str]) -> str:
        """Return a stable text form for outbound-response echo detection.

        Telegram Business/userbot bridges can re-deliver a message we just sent
        as if it was authored by the human account. Those echoes often lose
        Markdown formatting and may include Telegram's compact reply preview.
        Normalise both sides before comparing.
        """
        value = str(text or "")
        value = re.sub(r'^\[Replying to: "(?:.|\n)*?"\]\s*', "", value).strip()
        try:
            value = strip_markdown(value)
        except Exception:
            pass
        value = re.sub(r"\s+", " ", value).strip()
        return value

    @classmethod
    def _self_echo_fingerprint(cls, text: Optional[str]) -> str:
        return hashlib.sha256(cls._self_echo_normalize(text).encode("utf-8")).hexdigest()

    @staticmethod
    def _self_echo_store_path() -> Path:
        """Small persistent echo ledger, used across gateway restarts."""
        return Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))) / "telegram_outbound_echoes.jsonl"

    def _load_recent_persistent_outbound_echoes(self, chat_id: str, now: float) -> List[tuple[float, str, str]]:
        path = self._self_echo_store_path()
        if not path.exists():
            return []
        entries: List[tuple[float, str, str]] = []
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]
            for line in lines:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if str(item.get("chat_id") or "") != str(chat_id):
                    continue
                expires_at = float(item.get("expires_at") or 0)
                if expires_at <= now:
                    continue
                normalized = str(item.get("text") or "")
                fp = str(item.get("fingerprint") or "")
                if normalized and fp:
                    entries.append((expires_at, normalized, fp))
        except Exception as exc:
            logger.debug("[%s] Failed to read persistent Telegram self-echo ledger: %s", self.name, exc)
        return entries[-50:]

    def _persist_recent_outbound_echo(self, chat_id: str, expires_at: float, normalized: str, fingerprint: str) -> None:
        try:
            path = self._self_echo_store_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "chat_id": str(chat_id),
                    "expires_at": float(expires_at),
                    "text": normalized,
                    "fingerprint": fingerprint,
                }, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("[%s] Failed to persist Telegram self-echo fingerprint: %s", self.name, exc)

    def _remember_recent_outbound_text(self, chat_id: str, content: str) -> None:
        """Remember a just-sent response so reflected userbot echoes are ignored."""
        normalized = self._self_echo_normalize(content)
        if not normalized:
            return
        store = getattr(self, "_recent_outbound_text_echoes", None)
        if store is None:
            store = {}
            self._recent_outbound_text_echoes = store
        now = time.time()
        expires_at = now + float(os.getenv("TELEGRAM_SELF_ECHO_TTL_SECONDS", "900") or 900)
        key = str(chat_id)
        entries = [item for item in store.get(key, []) if item[0] > now]
        fingerprint = self._self_echo_fingerprint(normalized)
        entries.append((expires_at, normalized, fingerprint))
        store[key] = entries[-50:]
        self._persist_recent_outbound_echo(key, expires_at, normalized, fingerprint)

    def _recent_outbound_echo_entries(self, chat_id: str, now: Optional[float] = None) -> List[tuple[float, str, str]]:
        """Return same-chat and bounded cross-chat recent outbound echo entries."""
        if now is None:
            now = time.time()
        store = getattr(self, "_recent_outbound_text_echoes", None) or {}
        entries = [item for item in store.get(chat_id, []) if item[0] > now]
        if entries:
            store[chat_id] = entries

        # Outbound echoes can arrive after a gateway restart or after the
        # in-memory cache was lost. Keep a short persistent fingerprint ledger
        # so reflected copies do not get re-ingested as Chip's text.
        entries = entries + self._load_recent_persistent_outbound_echoes(chat_id, now)

        # Some Chip delivery paths (userbot/concierge relays, linked DMs, or
        # platform-level reply mirrors) can reflect a bot-authored message into
        # a different Telegram chat id from the one the gateway sent to.
        cross_chat_entries: List[tuple[float, str, str]] = []
        store_all = getattr(self, "_recent_outbound_text_echoes", None) or {}
        for key, values in list(store_all.items()):
            if key == chat_id:
                continue
            cross_chat_entries.extend([item for item in values if item[0] > now])
        try:
            path = self._self_echo_store_path()
            if path.exists():
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]:
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    if str(data.get("chat_id", "")) == chat_id:
                        continue
                    expires_at = float(data.get("expires_at", 0) or 0)
                    if expires_at <= now:
                        continue
                    text = str(data.get("text", "") or "")
                    fingerprint = str(data.get("fingerprint", "") or "")
                    if text and fingerprint:
                        cross_chat_entries.append((expires_at, text, fingerprint))
        except Exception as exc:
            logger.debug("[%s] Failed to read cross-chat Telegram self-echo ledger: %s", self.name, exc)

        return (entries + cross_chat_entries[-100:])[-150:]

    def _is_recent_outbound_text_echo(self, message: "Message") -> bool:
        """True if this incoming text is a reflected copy of our own response."""
        chat_id = str(getattr(getattr(message, "chat", None), "id", "") or "")
        visible_text = getattr(message, "caption", None) or getattr(message, "text", None) or ""
        normalized = self._self_echo_normalize(visible_text)
        if not chat_id or not normalized:
            return False

        incoming_hash = self._self_echo_fingerprint(normalized)
        for _expires_at, sent_text, sent_hash in self._recent_outbound_echo_entries(chat_id):
            if incoming_hash == sent_hash:
                return True
            # Markdown stripping and Telegram reply previews can leave tiny
            # differences. Only suppress near-whole-message echoes; a real user
            # follow-up with extra instructions should still pass through and
            # then be stripped by _strip_recent_outbound_text_echo_prefix().
            if sent_text in normalized and len(normalized) <= len(sent_text) + 24:
                return True
            if normalized in sent_text and len(sent_text) <= len(normalized) + 24:
                return True
        return False

    def _strip_recent_outbound_text_echo_prefix(self, message: "Message") -> Optional[str]:
        """Return only the real user suffix when a message starts with our outbound text.

        A user may reply/forward/copy a bot-authored report and add a complaint or
        instruction after it. Treating the whole body as user-authored pollutes the
        transcript and looks like Hermes wrote as Chip. Full echoes are dropped by
        _is_recent_outbound_text_echo(); this handles echoed-prefix + real suffix.
        """
        chat_id = str(getattr(getattr(message, "chat", None), "id", "") or "")
        visible_text = getattr(message, "caption", None) or getattr(message, "text", None) or ""
        normalized = self._self_echo_normalize(visible_text)
        if not chat_id or not normalized:
            return None
        for _expires_at, sent_text, _sent_hash in self._recent_outbound_echo_entries(chat_id):
            if not sent_text or not normalized.startswith(sent_text):
                continue
            suffix = normalized[len(sent_text):].strip()
            suffix = re.sub(r"^[\s\-–—:;,.!?]+", "", suffix).strip()
            if len(suffix) >= 3:
                return suffix
        return None

    def _is_recent_outbound_text_quote(self, chat_id: str, text: Optional[str]) -> bool:
        """True when reply_to_text is a quote/snippet of a recent bot-authored outbound.

        Telegram reply previews may carry only the first part of a bot message.
        Even if the reflected full echo was dropped, a user's reply to that echo
        can still expose the preview as `[Replying to: ...]` unless we classify
        the replied-to text as assistant-owned by content, not only by from_user.
        """
        normalized = self._self_echo_normalize(text)
        if not chat_id or len(normalized) < 40:
            return False
        for _expires_at, sent_text, sent_hash in self._recent_outbound_echo_entries(str(chat_id)):
            if not sent_text:
                continue
            if normalized == sent_text or self._self_echo_fingerprint(normalized) == sent_hash:
                return True
            if sent_text.startswith(normalized):
                return True
            if normalized in sent_text and len(normalized) >= min(120, max(40, int(len(sent_text) * 0.20))):
                return True
        return False

    def _is_self_bot_message(self, message: Any) -> bool:
        """Return True for updates authored by this bot account itself."""
        if not self._bot:
            return False
        user = getattr(message, "from_user", None)
        if not user:
            return False
        return bool(
            getattr(user, "is_bot", False)
            and getattr(user, "id", None) == getattr(self._bot, "id", None)
        )

    @staticmethod
    def _recent_visible_context_key(event: MessageEvent) -> tuple[str, str]:
        source = event.source
        return (str(getattr(source, "chat_id", "") or ""), str(getattr(source, "thread_id", "") or ""))

    def _attach_recent_visible_context(self, event: MessageEvent) -> None:
        """Attach a short same-chat/topic recent-context block to an event."""
        store = getattr(self, "_recent_visible_messages", None) or {}
        entries = list(store.get(self._recent_visible_context_key(event), []))[-8:]
        current_id = str(event.message_id or "")
        rows = []
        for item in entries:
            msg_id = str(item.get("message_id") or "")
            text = str(item.get("text") or "").strip()
            if not text or (current_id and msg_id == current_id):
                continue
            rows.append(f"- ID {msg_id} | {text[:500]}")
        if rows:
            event.recent_context = "## Recent visible Telegram context\n\n" + "\n".join(rows)

    def _record_recent_visible_message(self, event: MessageEvent) -> None:
        """Remember a visible Telegram message for future same-topic turns."""
        text = str(event.text or "").strip()
        if not text:
            return
        store = getattr(self, "_recent_visible_messages", None)
        if store is None:
            store = {}
            self._recent_visible_messages = store
        key = self._recent_visible_context_key(event)
        entries = list(store.get(key, []))
        entries.append({"message_id": str(event.message_id or ""), "text": re.sub(r"\s+", " ", text)})
        store[key] = entries[-20:]

    def _prepare_recent_visible_context(self, event: MessageEvent) -> MessageEvent:
        self._attach_recent_visible_context(event)
        self._record_recent_visible_message(event)
        return event

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
            # Fail-closed: no allowlist means deny by default.
            # The runner auth path in _is_user_authorized() handles
            # GATEWAY_ALLOW_ALL_USERS; this fallback must not silently
            # allow everyone (fixes #24457).
            return os.getenv("GATEWAY_ALLOW_ALL_USERS", "").lower() in {"true", "1", "yes"}
        allowed_ids = {uid.strip() for uid in allowed_csv.split(",") if uid.strip()}
        return "*" in allowed_ids or normalized_user_id in allowed_ids

    @classmethod
    def _metadata_thread_id(cls, metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        if not metadata:
            return None
        thread_id = metadata.get("thread_id") or metadata.get("message_thread_id")
        return str(thread_id) if thread_id is not None else None

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

    @staticmethod
    def _looks_like_private_chat_id(chat_id: str) -> bool:
        try:
            return int(chat_id) > 0
        except (TypeError, ValueError):
            return False

    @classmethod
    def _is_private_dm_topic_send(
        cls,
        chat_id: str,
        thread_id: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> bool:
        if cls._metadata_direct_messages_topic_id(metadata) is not None:
            return bool(
                metadata
                and metadata.get("telegram_dm_topic_reply_fallback")
                and cls._metadata_reply_to_message_id(metadata) is not None
            )
        if metadata and metadata.get("telegram_dm_topic_created_for_send"):
            return False
        return bool(
            thread_id
            and metadata
            and metadata.get("telegram_dm_topic_reply_fallback")
        )

    @staticmethod
    def _dm_topic_missing_anchor_error() -> str:
        return "Telegram DM topic delivery requires a reply anchor; refusing to send outside the requested topic"

    @classmethod
    def _reply_to_message_id_for_send(
        cls,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        reply_to_mode: Optional[str] = None,
    ) -> Optional[int]:
        if reply_to:
            return int(reply_to)
        if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
            if reply_to_mode == "off":
                return None
            return cls._metadata_reply_to_message_id(metadata)
        return None

    @classmethod
    def _thread_kwargs_for_send(
        cls,
        chat_id: str,
        thread_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        reply_to_message_id: Optional[int] = None,
        reply_to_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return Telegram send kwargs for forum and direct-message topic routing.

        Supergroup/forum topics use ``message_thread_id``. True Bot API Direct
        Messages topics can opt in with explicit ``direct_messages_topic_id``
        metadata. Hermes-created private-chat topic lanes are marked with
        ``telegram_dm_topic_reply_fallback``. Live replies send the private
        topic thread id together with a reply anchor; synthetic/resumed sends
        without an anchor use ``direct_messages_topic_id`` when metadata has it.
        ``message_thread_id`` alone can render outside the visible lane.

        When ``reply_to_mode`` is ``"off"``, the reply anchor is suppressed for
        DM topic fallback sends while preserving the ``message_thread_id`` so
        the message still lands in the correct topic.
        """
        if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
            if reply_to_mode == "off":
                return {"message_thread_id": cls._message_thread_id_for_send(thread_id)}
            if reply_to_message_id is None:
                reply_to_message_id = cls._metadata_reply_to_message_id(metadata)
            if reply_to_message_id is None:
                direct_topic_id = cls._metadata_direct_messages_topic_id(metadata)
                if direct_topic_id is not None:
                    return {
                        "message_thread_id": None,
                        "direct_messages_topic_id": int(direct_topic_id),
                    }
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
        """True when a DM-topic send should be retried with routing stripped.

        Two cases trigger the retry:

        1. The original anchor-stale case — the reply target was deleted, so
           Bot API returns "message to be replied not found". The retry drops
           the reply anchor and the topic id together.

        2. The synthetic-event case (added when #27937 introduced
           ``direct_messages_topic_id`` fallback for sends without an anchor):
           if Bot API rejects the topic id itself with any BadRequest that
           mentions topic/thread routing, we retry without routing rather
           than dropping the message.
        """
        if not (metadata and metadata.get("telegram_dm_topic_reply_fallback")):
            return False
        if not cls._is_bad_request_error(error):
            return False
        err_lower = str(error).lower()
        if reply_to_message_id is not None and "message to be replied not found" in err_lower:
            return True
        # Synthetic / resumed sends route via ``direct_messages_topic_id``
        # instead of a reply anchor. If Telegram rejects the topic id, fall
        # back to a plain DM send.
        if metadata.get("direct_messages_topic_id"):
            topic_markers = (
                "direct_messages_topic",
                "message thread not found",
                "thread not found",
                "topic_closed",
                "topic_deleted",
                "topic not found",
            )
            if any(marker in err_lower for marker in topic_markers):
                return True
        return False

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
        if name in {"networkerror", "timedout", "connectionerror"}:
            return True
        try:
            from telegram.error import NetworkError, TimedOut
            if isinstance(error, (NetworkError, TimedOut)):
                return True
        except ImportError:
            pass
        return isinstance(error, OSError)

    @staticmethod
    def _looks_like_connect_timeout(error: Exception) -> bool:
        """Return True when a Telegram TimedOut wraps a connect-timeout.

        A plain Telegram TimedOut may mean the request reached Telegram and
        should not be re-sent. A ConnectTimeout means the TCP connection was
        never established, so retrying is safe and prevents silent drops.
        """
        seen: set[int] = set()
        stack: list[BaseException] = [error]
        while stack:
            cur = stack.pop()
            ident = id(cur)
            if ident in seen:
                continue
            seen.add(ident)
            name = cur.__class__.__name__.lower()
            text = str(cur).lower()
            if "connecttimeout" in name or "connect timeout" in text or "connect timed out" in text:
                return True
            cause = getattr(cur, "__cause__", None)
            context = getattr(cur, "__context__", None)
            if cause is not None:
                stack.append(cause)
            if context is not None:
                stack.append(context)
        return False

    @staticmethod
    def _looks_like_pool_timeout(error: Exception) -> bool:
        """Return True when a Telegram TimedOut wraps an httpx pool timeout.

        PTB converts ``httpx.PoolTimeout`` into ``telegram.error.TimedOut`` with
        a message that explicitly states the request was *not* sent
        (``"Pool timeout: All connections in the connection pool are occupied.
        Request was *not* sent to Telegram."``). Because the request never left
        the process, re-sending is safe and cannot duplicate -- the opposite of
        a generic TimedOut, which may have reached Telegram. We match the
        wrapped ``httpx.PoolTimeout`` class as well as the message string so the
        check survives PTB message-wording changes.
        """
        seen: set[int] = set()
        stack: list[BaseException] = [error]
        while stack:
            cur = stack.pop()
            ident = id(cur)
            if ident in seen:
                continue
            seen.add(ident)
            name = cur.__class__.__name__.lower()
            text = str(cur).lower()
            if "pooltimeout" in name or "pool timeout" in text or (
                "connection pool" in text and "occupied" in text
            ):
                return True
            cause = getattr(cur, "__cause__", None)
            context = getattr(cur, "__context__", None)
            if cause is not None:
                stack.append(cause)
            if context is not None:
                stack.append(context)
        return False

    def _coerce_bool_extra(self, key: str, default: bool = False) -> bool:
        value = self.config.extra.get(key) if getattr(self.config, "extra", None) else None
        if value is None:
            return default
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
            return default
        return bool(value)

    def _coerce_int_extra(self, key: str, default: int = 0) -> int:
        value = self.config.extra.get(key) if getattr(self.config, "extra", None) else None
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _coerce_str_set_extra(self, key: str) -> Set[str]:
        value = self.config.extra.get(key) if getattr(self.config, "extra", None) else None
        if value is None:
            return set()
        if isinstance(value, (str, int)):
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith("["):
                    try:
                        parsed = json.loads(stripped)
                        if isinstance(parsed, list):
                            value = parsed
                        else:
                            value = [stripped]
                    except Exception:
                        value = stripped.split(",")
                else:
                    value = stripped.split(",")
            else:
                value = str(value).split(",")
        if not isinstance(value, (list, tuple, set)):
            return set()
        return {str(v).strip() for v in value if str(v).strip()}

    def _link_preview_kwargs(self) -> Dict[str, Any]:
        if not getattr(self, "_disable_link_previews", False):
            return {}
        if LinkPreviewOptions is not None:
            return {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
        return {"disable_web_page_preview": True}

    @staticmethod
    def _truthy_config_value(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _business_connection_store_path() -> Path:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / "state" / "telegram_business_connections.json"

    def _known_business_connection_id(self, chat_id: Any) -> Optional[str]:
        """Return the last verified Business connection observed for a DM."""
        path = self._business_connection_store_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            value = payload.get(str(chat_id)) if isinstance(payload, dict) else None
            return str(value) if value else None
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return None

    def _remember_business_connection_id(self, chat_id: Any, connection_id: Any) -> None:
        """Persist a Business connection only after Telegram supplied it."""
        if not chat_id or not connection_id:
            return
        path = self._business_connection_store_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload[str(chat_id)] = str(connection_id)
            temp_path = path.with_suffix(f"{path.suffix}.tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temp_path, path)
        except Exception as exc:
            logger.debug(
                "[%s] Failed to persist Telegram Business connection for chat %s: %s",
                self.name,
                chat_id,
                exc,
            )

    def _business_connection_kwargs(
        self, metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        metadata = metadata or {}
        business_connection_id = metadata.get("business_connection_id")
        if not business_connection_id:
            return {}

        send_as_account = metadata.get("telegram_business_send_as_account")
        if send_as_account is None:
            business_cfg: Dict[str, Any] = {}
            config = getattr(self, "config", None)
            extra = getattr(config, "extra", {}) if config is not None else {}
            raw = extra.get("business") if isinstance(extra, dict) else None
            if isinstance(raw, dict):
                business_cfg = raw
            send_as_account = business_cfg.get(
                "send_as_account",
                business_cfg.get("reply_via_business_connection", False),
            )

        if not self._truthy_config_value(send_as_account):
            # Telegram Business Bot API sends with business_connection_id render
            # as the connected human/business account in the peer chat. Keep the
            # default fail-closed, but allow Chip's explicitly configured
            # Business concierge route to use the official business connection.
            logger.warning(
                "[%s] Suppressing Telegram Business send-as-account for agent reply",
                self.name,
            )
            return {}
        return {"business_connection_id": str(business_connection_id)}

    @staticmethod
    def _business_connection_id_from_metadata(
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not metadata:
            return None
        value = metadata.get("business_connection_id") or metadata.get(
            "telegram_business_connection_id"
        )
        return str(value) if value else None


    def _telegram_api_base_url(self) -> str:
        """Return the Bot API method base URL, without the token suffix."""
        base_url = str(self.config.extra.get("base_url") or "https://api.telegram.org/bot")
        base_url = base_url.rstrip("/")
        token = str(self.config.token or "")
        if token and base_url.endswith(token):
            return base_url[: -len(token)].rstrip("/")
        return base_url

    def _rich_markdown_from_content(self, content: str) -> str:
        """Convert Hermes/chipline text into Telegram rich markdown."""
        text = content.strip()
        lines: list[str] = []
        for raw in text.splitlines():
            line = raw.rstrip()
            m = re.match(r"^\s*[➊➋➌➍➎➏➐➑➒➓]\s+(.+)$", line)
            if m:
                lines.append(f"## {m.group(1).strip()}")
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _should_use_rich_message(
        self,
        chat_id: str,
        content: str,
        *,
        finalize: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not finalize:
            return False
        if not content or not content.strip():
            return False
        stripped = content.strip()
        if "▉" in content or stripped.startswith(("MEDIA:", "Checking:", "Проверяю:")):
            return False
        if utf16_len(content) > 32768:
            return False

        if self._has_cjk_text(content):
            return False

        configured_chats = getattr(self, "_rich_message_chat_ids", set())
        rich_shape = self._has_rich_message_shape(stripped)
        bare_table = self._has_markdown_table(stripped) and not re.search(r"^\s*- \[[ xX]\] ", stripped, re.M) and not re.search(r"^\s*#{1,6}\s+", stripped, re.M)
        if not configured_chats:
            # Default/global behavior: plain rich-markdown stays legacy unless
            # explicitly opted in. Bare pipe tables are auto-routed because the
            # legacy path destroys their structure.
            if not getattr(self, "_rich_messages_enabled", False):
                return bare_table
            return rich_shape
        if str(chat_id) not in configured_chats:
            return False

        # Private HEL1 gate: configured chats use rich for chipline reports,
        # tables/images, or sufficiently long final replies.
        if rich_shape:
            return True
        if "![" in content and "](http" in content:
            return True
        return len(stripped) >= getattr(self, "_rich_message_min_chars", 500)

    @staticmethod
    def _has_cjk_text(content: str) -> bool:
        return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0003ffff]", content or ""))

    @staticmethod
    def _has_markdown_table(content: str) -> bool:
        lines = (content or "").splitlines()
        for i in range(len(lines) - 1):
            if "|" in lines[i] and _TABLE_SEPARATOR_RE.match(lines[i + 1]):
                return True
        return False

    def _has_rich_message_shape(self, content: str) -> bool:
        return bool(
            self._has_markdown_table(content)
            or re.search(r"^\s*- \[[ xX]\] ", content, re.M)
            or re.search(r"^\s*[➊➋➌➍➎➏➐➑➒➓]\s+", content, re.M)
            or "<details" in content.lower()
            or "$$" in content
        )

    async def _post_telegram_bot_api(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        token = str(self.config.token or "")
        if not token:
            raise RuntimeError("Telegram token is not configured")
        url = f"{self._telegram_api_base_url()}{token}/{method}"

        def _request() -> Dict[str, Any]:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        return await asyncio.to_thread(_request)

    async def _send_rich_message(
        self,
        chat_id: str,
        content: str,
        *,
        reply_to_id: Optional[int] = None,
        thread_kwargs: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        payload: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "rich_message": {"markdown": self._rich_markdown_from_content(content)},
        }
        if reply_to_id is not None:
            payload["reply_parameters"] = {"message_id": int(reply_to_id)}
        if thread_kwargs and thread_kwargs.get("message_thread_id") is not None:
            payload["message_thread_id"] = int(thread_kwargs["message_thread_id"])
        payload.update(self._business_connection_kwargs(metadata))
        raw = await self._post_telegram_bot_api("sendRichMessage", payload)
        if not raw.get("ok"):
            raise RuntimeError(raw.get("description") or raw)
        result = raw.get("result") or {}
        return SendResult(
            success=True,
            message_id=str(result.get("message_id")) if result.get("message_id") is not None else None,
            raw_response={"rich_message": True, "method": "sendRichMessage"},
        )

    async def _edit_rich_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        payload: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "rich_message": {"markdown": self._rich_markdown_from_content(content)},
        }
        payload.update(self._business_connection_kwargs(metadata))
        raw = await self._post_telegram_bot_api("editMessageText", payload)
        if not raw.get("ok"):
            raise RuntimeError(raw.get("description") or raw)
        return SendResult(
            success=True,
            message_id=message_id,
            raw_response={"rich_message": True, "method": "editMessageText"},
        )

# ------------------------------------------------------------------
    # Bot API 10.1 Rich Messages (sendRichMessage)
    #
    # Final / new-message replies opportunistically use sendRichMessage with
    # the RAW agent markdown so richer constructs (tables, task lists,
    # collapsible details, math, ...) render natively. The legacy MarkdownV2
    # send() path stays as the fallback for unsupported/oversized content and
    # older PTB/clients. Streaming edits stay on Hermes' existing MarkdownV2
    # edit path for now; finalization can re-send as rich and delete the stale
    # preview until rich_message edit support is wired directly.
    # ------------------------------------------------------------------
    def _content_fits_rich_limits(self, content: str) -> bool:
        """Cheap pre-check for the one hard rich limit we can count locally.

        Only the 32,768 UTF-8 character text cap is enforced here. Other Bot API
        rich limits (500 blocks, 16 nesting levels, 20 table columns, ...) are
        not pre-counted; if exceeded Telegram returns a BadRequest, which
        :meth:`_is_rich_fallback_error` classifies as permanent so the send
        degrades to the legacy chunking path.
        """
        return len(content) <= self.RICH_MESSAGE_MAX_CHARS

    def _bot_supports_rich(self) -> bool:
        """True when the bound bot can issue raw ``sendRichMessage`` calls.

        Gates on ``do_api_request`` being an *async* callable. The real
        ``telegram.Bot.do_api_request`` is a coroutine function; test doubles
        that opt into rich set it to an ``AsyncMock`` (also a coroutine
        function). Plain ``MagicMock`` bots expose a *sync* auto-child and
        ``SimpleNamespace`` bots lack the attribute entirely — both resolve to
        ``False`` here, so the legacy path is used unchanged.
        """
        return inspect.iscoroutinefunction(getattr(self._bot, "do_api_request", None))

    _RICH_DETAILS_RE = re.compile(r"<details\b[^>]*>.*?</details>", re.IGNORECASE | re.DOTALL)
    _RICH_MATH_IN_DETAILS_RE = re.compile(
        r"(\$\$.*?\$\$|"
        r"\\\[.*?\\\]|"
        r"\\\(.*?\\\)|"
        r"\\(?:sum|frac|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|"
        r"int|prod|sqrt|lim|infty|begin\{(?:equation|align|matrix|cases)\}))",
        re.IGNORECASE | re.DOTALL,
    )

    def _has_telegram_desktop_details_math_crash_shape(self, content: str) -> bool:
        """Return True for rich-message details+math content that crashes TDesktop.

        Telegram Desktop 6.9.1 can crash while rendering Bot API 10.1 rich
        messages containing math inside a collapsible details block
        (telegramdesktop/tdesktop#30808). The Bot API accepts the payload, so
        Hermes must skip rich delivery up front and use the legacy MarkdownV2
        path until affected Desktop clients age out.
        """
        if not content:
            return False
        for details_block in self._RICH_DETAILS_RE.findall(content):
            if self._RICH_MATH_IN_DETAILS_RE.search(details_block):
                return True
        return False

    def _should_attempt_rich(
        self, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        return bool(
            (getattr(self, "_rich_messages_enabled", False) or self._has_markdown_table(content))
            and not getattr(self, "_rich_send_disabled", False)
            and not (metadata or {}).get("expect_edits")
            and content
            and content.strip()
            and not self._has_cjk_text(content)
            and not self._has_telegram_desktop_details_math_crash_shape(content)
            and self._content_fits_rich_limits(content)
            and self._bot_supports_rich()
        )

    def prefers_fresh_final_streaming(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        """Whether to replace a streamed preview with a fresh rich final.

        Keep the fresh-final path disabled by default for Telegram. It briefly
        shows two copies of the final answer, then deletes the streaming preview
        after the rich send succeeds. That is especially visible on clients that
        support rich messages well.

        A private ``rich_message_chats`` pilot may opt specific chats into the
        fresh rich final, but only when the stream consumer passes ``chat_id``;
        without a chat id we fail closed.
        """
        configured_chats = getattr(self, "_rich_message_chat_ids", set())
        if not configured_chats or chat_id is None:
            return False
        if not self._should_use_rich_message(
            str(chat_id), content, finalize=True, metadata=metadata
        ):
            return False
        return self._should_attempt_rich(content)

    def streaming_overflow_limit(self) -> Optional[int]:
        """Allow the stream consumer to accumulate up to the rich-message cap
        before splitting, so a reply that fits one ``sendRichMessage`` /
        ``sendRichMessageDraft`` isn't fragmented at the 4,096 MarkdownV2 limit.

        Gated on the same rich capability as the send path (minus the
        content-length check — raising that cap is the whole point): rich not
        latched off and the bot exposes an async ``do_api_request``.  Returns
        ``None`` (→ legacy 4,096 limit) when rich isn't available, so non-rich
        streams split exactly as before.
        """
        if (
            getattr(self, "_rich_messages_enabled", False)
            and not getattr(self, "_rich_send_disabled", False)
            and self._bot_supports_rich()
        ):
            return self.RICH_MESSAGE_MAX_CHARS
        return None

    def _rich_message_payload(
        self, content: str, *, skip_entity_detection: bool = False
    ) -> Dict[str, Any]:
        """Build the ``InputRichMessage`` object from RAW markdown.

        Never pass ``format_message(content)`` here — that converts to
        MarkdownV2 and would escape/destroy rich syntax like table pipes.
        """
        payload: Dict[str, Any] = {"markdown": self._rich_markdown_from_content(content)}
        if skip_entity_detection:
            payload["skip_entity_detection"] = True
        return payload

    def _is_rich_capability_error(self, exc: Exception) -> bool:
        """True ⇒ the rich endpoint itself is unavailable (old PTB/server).

        These latch rich off for the rest of the adapter's life — retrying is
        pointless and would cost a failed roundtrip on every send. Per-message
        rejections (BadRequest from a parser/limit issue) are NOT capability
        errors: the next message may be fine.
        """
        name = exc.__class__.__name__.lower()
        if name in {"endpointnotfound", "invalidtoken"}:
            return True
        if isinstance(exc, (AttributeError, TypeError, NotImplementedError)):
            return True
        if getattr(exc, "error_code", None) == 404:
            return True
        s = str(exc).lower()
        if ("method" in s or "endpoint" in s) and (
            "not found" in s or "does not exist" in s
        ):
            return True
        return "no such method" in s

    def _is_rich_fallback_error(self, exc: Exception) -> bool:
        """True ⇒ permanent/capability error ⇒ safe to fall back to legacy.

        Conservative on purpose: only clearly-permanent failures (BadRequest,
        capability errors, unknown/unsupported endpoint) qualify. Everything
        else is treated as transient — the rich request may have reached
        Telegram, so we must NOT legacy-resend and risk a duplicate.
        """
        if self._is_bad_request_error(exc):
            return True
        if self._is_rich_capability_error(exc):
            return True
        s = str(exc).lower()
        return "unsupported" in s or "not implemented" in s

    def _compute_single_send_routing(
        self,
        chat_id: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
        thread_id: Optional[str],
    ) -> Optional[tuple]:
        """Routing for a single (rich) send — mirrors send()'s index-0 block.

        Returns ``(reply_to_id, thread_kwargs)``, or ``None`` to signal "skip
        rich, let the legacy path handle it" — used for the DM-topic fail-loud
        case so the legacy path stays the single source of the refuse result.
        """
        metadata_reply_to = self._metadata_reply_to_message_id(metadata)
        private_dm_topic_send = self._is_private_dm_topic_send(chat_id, thread_id, metadata)
        dm_topic_reply_to_off = (
            private_dm_topic_send
            and self._reply_to_mode == "off"
            and bool(metadata and metadata.get("telegram_dm_topic_reply_fallback"))
        )
        reply_to_source = reply_to or (
            str(metadata_reply_to)
            if private_dm_topic_send and metadata_reply_to is not None
            else None
        )
        if private_dm_topic_send:
            should_thread = reply_to_source is not None and self._reply_to_mode != "off"
        else:
            should_thread = self._should_thread_reply(reply_to_source, 0)
        reply_to_id = int(reply_to_source) if should_thread and reply_to_source else None
        if private_dm_topic_send and reply_to_id is None and not dm_topic_reply_to_off:
            # Refusing to send outside the requested DM topic — defer to the
            # legacy path, which returns the canonical fail-loud SendResult.
            return None
        thread_kwargs = self._thread_kwargs_for_send(
            chat_id,
            thread_id,
            metadata,
            reply_to_message_id=reply_to_id,
            reply_to_mode=self._reply_to_mode,
        )
        return reply_to_id, thread_kwargs

    async def _try_send_rich(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[SendResult]:
        """Attempt a single ``sendRichMessage`` send.

        Returns a :class:`SendResult` (success, or a transient failure that the
        caller must NOT legacy-resend), or ``None`` to signal "fall back to the
        legacy MarkdownV2 path" (permanent/capability error or DM-topic skip).
        """
        thread_id = self._metadata_thread_id(metadata)
        routing = self._compute_single_send_routing(chat_id, reply_to, metadata, thread_id)
        if routing is None:
            return None
        reply_to_id, thread_kwargs = routing

        payload: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "rich_message": self._rich_message_payload(content),
        }
        # Only forward non-None routing keys: when direct_messages_topic_id is
        # present _thread_kwargs_for_send pairs it with message_thread_id=None,
        # which must not be sent as a stray field on the raw endpoint.
        payload.update({k: v for k, v in thread_kwargs.items() if v is not None})
        payload.update(self._notification_kwargs(metadata))
        if getattr(self, "_disable_link_previews", False):
            payload["link_preview_options"] = {"is_disabled": True}
        if reply_to_id is not None:
            # Spec: sendRichMessage takes reply_parameters (ReplyParameters
            # object), NOT the legacy reply_to_message_id scalar. Unknown
            # params are silently ignored by the Bot API, so the scalar would
            # quietly drop the reply anchor instead of erroring.
            payload["reply_parameters"] = {"message_id": reply_to_id}

        try:
            # Take the raw Bot API result (dict under real PTB). Passing
            # return_type=Message would make PTB deserialize a Bot API 10.1
            # response shape it does not fully model yet; a post-delivery parse
            # error must not be mistaken for a sendable failure.
            msg = await self._bot.do_api_request(
                "sendRichMessage", api_kwargs=payload
            )
        except Exception as exc:
            if self._is_rich_fallback_error(exc):
                if self._is_rich_capability_error(exc):
                    # Endpoint missing (old PTB/server) — latch rich off so
                    # every later send doesn't pay a doomed extra roundtrip.
                    self._rich_send_disabled = True
                logger.debug(
                    "[%s] sendRichMessage rejected (%s) — falling back to MarkdownV2",
                    self.name, exc,
                )
                return None
            # Transient / network / unknown: the request may have reached
            # Telegram. Do NOT legacy-resend (duplicate risk); surface a
            # failure with retry semantics mirroring the legacy send() except.
            err_str = str(exc).lower()
            try:
                from telegram.error import TimedOut as _TimedOut
            except (ImportError, AttributeError):
                _TimedOut = None
            is_timeout = (_TimedOut and isinstance(exc, _TimedOut)) or "timed out" in err_str
            is_connect_timeout = self._looks_like_connect_timeout(exc)
            safe_error = _redact_telegram_error_text(exc)
            logger.warning(
                "[%s] sendRichMessage transient failure (no legacy resend): %s",
                self.name, safe_error,
            )
            return SendResult(
                success=False,
                error=safe_error,
                retryable=(is_connect_timeout or not is_timeout),
            )

        message_id = None
        if isinstance(msg, dict):
            message_id = msg.get("message_id")
            if message_id is None:
                message_id = (msg.get("result") or {}).get("message_id")
        else:
            message_id = getattr(msg, "message_id", None)
        if message_id is not None:
            try:
                from gateway import rich_sent_store

                rich_sent_store.record(chat_id, str(message_id), content)
            except Exception:
                pass
        return SendResult(
            success=True,
            message_id=str(message_id) if message_id is not None else None,
        )

    async def _try_edit_rich(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[SendResult]:
        """Attempt ``editMessageText`` with Bot API 10.1 ``rich_message``."""
        thread_id = self._metadata_thread_id(metadata)
        thread_kwargs = self._thread_kwargs_for_send(
            chat_id,
            thread_id,
            metadata,
            reply_to_message_id=None,
            reply_to_mode=self._reply_to_mode,
        )
        payload: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "rich_message": self._rich_message_payload(content),
        }
        payload.update({k: v for k, v in thread_kwargs.items() if v is not None})
        payload.update(self._business_connection_kwargs(metadata))
        try:
            await self._bot.do_api_request("editMessageText", api_kwargs=payload)
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return SendResult(success=True, message_id=message_id)
            if self._is_rich_fallback_error(exc):
                if self._is_rich_capability_error(exc):
                    self._rich_send_disabled = True
                return None
            return SendResult(
                success=False,
                error=_redact_telegram_error_text(exc),
                retryable=True,
            )
        try:
            from gateway import rich_sent_store

            rich_sent_store.record(chat_id, str(message_id), content)
        except Exception:
            pass
        return SendResult(success=True, message_id=message_id)

    def _should_attempt_rich_draft(self, content: str) -> bool:
        return bool(
            self._coerce_bool_extra("rich_drafts", False)
            and getattr(self, "_rich_messages_enabled", False)
            and not self._has_cjk_text(content)
            and not getattr(self, "_rich_send_disabled", False)
            and not getattr(self, "_rich_draft_disabled", False)
            and content
            and content.strip()
            and not self._has_telegram_desktop_details_math_crash_shape(content)
            and self._content_fits_rich_limits(content)
            and self._bot_supports_rich()
        )

    async def _try_send_rich_draft(
        self,
        chat_id: str,
        draft_id: int,
        content: str,
        metadata: Optional[Dict[str, Any]],
    ) -> bool:
        """Emit one ``sendRichMessageDraft`` preview frame; True on success.

        Draft frames are ephemeral and overwritten by the next frame / the
        final ``sendRichMessage``, so a duplicate or lost rich draft is
        harmless — any failure simply returns False and the caller renders the
        legacy plain-text draft. A permanent/capability failure additionally
        latches ``_rich_draft_disabled`` so later frames skip the rich attempt.
        """
        payload: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "draft_id": int(draft_id),
            "rich_message": self._rich_message_payload(content),
        }
        thread_id = self._metadata_thread_id(metadata)
        if thread_id is not None:
            payload["message_thread_id"] = int(thread_id)
        try:
            ok = await self._bot.do_api_request("sendRichMessageDraft", api_kwargs=payload)
            return bool(ok)
        except Exception as exc:
            if self._is_rich_capability_error(exc):
                self._rich_draft_disabled = True
                logger.debug(
                    "[%s] sendRichMessageDraft unsupported (%s) — using legacy drafts",
                    self.name, exc,
                )
            else:
                logger.debug(
                    "[%s] sendRichMessageDraft transient failure (%s) — legacy draft this frame",
                    self.name, exc,
                )
            return False


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

    def _get_general_request_drain_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_general_request_drain_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._general_request_drain_lock = lock
        return lock

    async def _drain_general_connections_after_pool_timeout(self) -> None:
        """Reset the Bot API request pool after a confirmed send pool timeout."""
        bot = getattr(getattr(self, "_app", None), "bot", None)
        if bot is None:
            bot = getattr(self, "_bot", None)
        if bot is None:
            return
        try:
            general_req = bot._request[1]  # noqa: SLF001
        except Exception:
            return
        async with self._get_general_request_drain_lock():
            try:
                await general_req.shutdown()
            except Exception:
                logger.debug(
                    "[%s] General request shutdown failed after pool timeout (non-fatal)",
                    self.name, exc_info=True,
                )
            try:
                await general_req.initialize()
                logger.warning(
                    "[%s] General request pool drained after Telegram pool timeout",
                    self.name,
                )
            except Exception:
                logger.debug(
                    "[%s] General request re-initialize failed after pool timeout (non-fatal)",
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
        self._send_path_degraded = True
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
            self._send_path_degraded = False
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
            self._send_path_degraded = False
        except Exception as probe_err:
            logger.warning(
                "[%s] Polling heartbeat probe failed %ds after reconnect: %s",
                self.name, HEARTBEAT_PROBE_DELAY, probe_err,
            )
            await self._handle_polling_network_error(probe_err)

    async def _handle_polling_conflict(self, error: Exception) -> None:
        if self.has_fatal_error and self.fatal_error_code == "telegram_polling_conflict":
            return
        # Transient 409 Conflict errors arise when the previous gateway process
        # has been killed (e.g. during `hermes update` or `--replace` handoffs)
        # but its long-poll connection hasn't yet expired on Telegram's servers.
        # Telegram holds open getUpdates sessions for up to ~30s after the
        # client disconnects, so a new gateway starting immediately will receive
        # a 409 until that server-side session expires.
        #
        # Strategy: stop the local updater, wait long enough for Telegram's
        # server-side session to expire (RETRY_DELAY grows with each attempt),
        # drain the connection pool, then restart polling.  We attempt this
        # MAX_CONFLICT_RETRIES times before declaring a fatal error.
        #
        # Crucially, a failed retry must NOT leave polling in an ambiguous
        # state.  If start_polling() raises, the updater is neither running
        # nor fatal — messages are silently dropped.  We schedule another
        # retry attempt instead of returning silently, and only escalate to
        # fatal after all retries are exhausted.
        self._polling_conflict_count += 1

        MAX_CONFLICT_RETRIES = 5
        # Delay grows with each attempt: 15s, 25s, 35s, 45s, 55s.
        # Telegram server-side getUpdates sessions typically expire within
        # 30s; the increasing back-off ensures we clear that window without
        # hammering the API on fast-restart loops.
        RETRY_DELAY = 10 + (self._polling_conflict_count * 10)  # seconds

        if self._polling_conflict_count <= MAX_CONFLICT_RETRIES:
            logger.warning(
                "[%s] Telegram polling conflict (%d/%d) — previous session still "
                "held open on Telegram's servers. Waiting %ds for it to expire. "
                "Error: %s",
                self.name, self._polling_conflict_count, MAX_CONFLICT_RETRIES,
                RETRY_DELAY, error,
            )
            # Stop the local updater cleanly before sleeping.  If it's already
            # stopped (e.g. PTB raised before updater.running was set) this is
            # a no-op.
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
                logger.info(
                    "[%s] Telegram polling resumed after conflict retry %d/%d",
                    self.name, self._polling_conflict_count, MAX_CONFLICT_RETRIES,
                )
                self._polling_conflict_count = 0  # reset counter on success
                return
            except Exception as retry_err:
                logger.warning(
                    "[%s] Telegram polling retry %d/%d failed: %s. "
                    "Scheduling next attempt.",
                    self.name, self._polling_conflict_count, MAX_CONFLICT_RETRIES,
                    retry_err,
                )
                # Schedule the next retry rather than returning silently.
                # Returning here without either restarting polling or setting
                # a fatal error leaves the adapter in a limbo state: the
                # gateway process is alive and reports "connected" but
                # no messages are received or sent.
                if self._polling_conflict_count < MAX_CONFLICT_RETRIES:
                    # We are inside a running coroutine, so the running loop is
                    # guaranteed to exist. asyncio.get_event_loop() is deprecated
                    # and raises "RuntimeError: There is no current event loop in
                    # thread 'MainThread'" on Python 3.10+ when invoked from a
                    # context without an attached loop (which can happen when PTB
                    # dispatches this error callback). Use get_running_loop().
                    loop = asyncio.get_running_loop()
                    self._polling_error_task = loop.create_task(
                        self._handle_polling_conflict(retry_err)
                    )
                    return
                # Fall through to fatal on the last retry.

        # Exhausted all retries — declare a fatal error so the gateway
        # runner can surface this clearly and the user knows to act.
        message = (
            "Telegram polling could not recover after %d retries (%ds total wait). "
            "The previous gateway session is still held open on Telegram's servers, "
            "or another process is using the same bot token. "
            "To recover: ensure no other Hermes or OpenClaw instance is running "
            "with this token, then restart the gateway with 'hermes gateway restart'."
            % (MAX_CONFLICT_RETRIES, sum(10 + i * 10 for i in range(1, MAX_CONFLICT_RETRIES + 1)))
        )
        logger.error(
            "[%s] %s Original error: %s",
            self.name, message, error,
        )
        self._set_fatal_error("telegram_polling_conflict", message, retryable=False)
        try:
            if self._app and self._app.updater:
                await self._app.updater.stop()
        except Exception as stop_error:
            logger.warning(
                "[%s] Failed stopping Telegram updater after exhausting conflict retries: %s",
                self.name, stop_error, exc_info=True,
            )
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

    async def ensure_dm_topic(self, chat_id: str, topic_name: str, force_create: bool = False) -> Optional[str]:
        """Return a private DM topic thread id, creating and persisting it if needed."""
        name = str(topic_name or "").strip()
        if not name:
            return None
        try:
            chat_id_int = int(chat_id)
        except (TypeError, ValueError):
            return None

        cache_key = f"{chat_id_int}:{name}"
        cached = self._dm_topics.get(cache_key)
        if cached and not force_create:
            return str(cached)

        topic_conf: Optional[Dict[str, Any]] = None
        chat_entry: Optional[Dict[str, Any]] = None
        for entry in self._dm_topics_config:
            if str(entry.get("chat_id")) != str(chat_id_int):
                continue
            chat_entry = entry
            for candidate in entry.get("topics", []):
                if candidate.get("name") == name:
                    topic_conf = candidate
                    break
            break

        if topic_conf and topic_conf.get("thread_id") and not force_create:
            thread_id = int(topic_conf["thread_id"])
            self._dm_topics[cache_key] = thread_id
            return str(thread_id)

        if chat_entry is None:
            chat_entry = {"chat_id": chat_id_int, "topics": []}
            self._dm_topics_config.append(chat_entry)
        if topic_conf is None:
            topic_conf = {"name": name}
            chat_entry.setdefault("topics", []).append(topic_conf)

        thread_id = await self._create_dm_topic(
            chat_id_int,
            name=name,
            icon_color=topic_conf.get("icon_color"),
            icon_custom_emoji_id=topic_conf.get("icon_custom_emoji_id"),
        )
        if not thread_id:
            return None

        topic_conf["thread_id"] = thread_id
        self._dm_topics[cache_key] = int(thread_id)
        self._persist_dm_topic_thread_id(chat_id_int, name, int(thread_id), replace_existing=force_create)
        return str(thread_id)

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

    def _persist_dm_topic_thread_id(
        self,
        chat_id: int,
        topic_name: str,
        thread_id: int,
        replace_existing: bool = False,
    ) -> None:
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

            # Navigate to platforms.telegram.extra.dm_topics, creating the path
            # when a named delivery target asks us to create a topic that was
            # not predeclared in config.yaml.
            platforms = config.setdefault("platforms", {})
            telegram_config = platforms.setdefault("telegram", {})
            extra = telegram_config.setdefault("extra", {})
            dm_topics = extra.setdefault("dm_topics", [])

            changed = False
            matching_chat_entry = None
            for chat_entry in dm_topics:
                try:
                    chat_matches = int(chat_entry.get("chat_id", 0)) == int(chat_id)
                except (TypeError, ValueError):
                    chat_matches = False
                if not chat_matches:
                    continue
                matching_chat_entry = chat_entry
                for t in chat_entry.setdefault("topics", []):
                    if t.get("name") == topic_name:
                        if replace_existing or not t.get("thread_id"):
                            if t.get("thread_id") != thread_id:
                                t["thread_id"] = thread_id
                                changed = True
                        break
                else:
                    chat_entry.setdefault("topics", []).append(
                        {"name": topic_name, "thread_id": thread_id}
                    )
                    changed = True
                break

            if matching_chat_entry is None:
                dm_topics.append({
                    "chat_id": chat_id,
                    "topics": [{"name": topic_name, "thread_id": thread_id}],
                })
                changed = True

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
            # In local-mode telegram-bot-api, file_path is an absolute path on the
            # server's filesystem rather than a relative HTTP path. PTB needs
            # local_mode=True so download_*() reads from disk instead of issuing
            # an HTTP GET that would 404. Requires that the same path is
            # readable by the Hermes process (shared mount, same machine, etc.).
            if self.config.extra.get("local_mode"):
                builder = builder.local_mode(True)
                logger.info("[%s] Using Telegram local_mode (read files from disk)", self.name)

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

            disable_fallback = (os.getenv("HERMES_TELEGRAM_DISABLE_FALLBACK_IPS", "").strip().lower() in {"1", "true", "yes", "on"})
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
                from telegram import (
                    BotCommand,
                    BotCommandScopeAllPrivateChats,
                    BotCommandScopeAllGroupChats,
                    BotCommandScopeDefault,
                )
                from hermes_cli.commands import telegram_menu_commands
                # Telegram allows up to 100 commands but has an undocumented
                # payload size limit (~4KB total).  Limit to 30 core commands
                # to stay well under the threshold while covering all categories.
                menu_commands, hidden_count = telegram_menu_commands(max_commands=MAX_COMMANDS_PER_SCOPE)
                bot_commands = [BotCommand(name, desc) for name, desc in menu_commands]
                # Register for all scopes independently — Telegram picks the
                # narrowest matching scope per chat type (forum topics fall
                # through to AllGroupChats or Default).
                for scope_cls in (BotCommandScopeDefault, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats):
                    scope_name = scope_cls.__name__
                    try:
                        await self._bot.set_my_commands(bot_commands, scope=scope_cls())
                        logger.info("[%s] set_my_commands OK for scope %s (%d cmds)", self.name, scope_name, len(bot_commands))
                    except Exception as scope_err:
                        logger.warning("[%s] set_my_commands FAILED for scope %s: %s", self.name, scope_name, scope_err)
                # Forum topics don't inherit AllGroupChats — Telegram resolves
                # commands via BotCommandScopeChat(chat_id) for forum groups.
                # Lazy registration happens in _ensure_forum_commands on first
                # message from a forum topic (see _handle_text_message).
                if hidden_count:
                    logger.info(
                        "[%s] Telegram menu: %d commands registered, %d hidden (over %d limit). Use /commands for full list.",
                        self.name, len(menu_commands), hidden_count, 30,
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

        # getattr() — tests build adapters via object.__new__() (no __init__).
        if getattr(self, "_send_path_degraded", False):
            return SendResult(success=False, error="send_path_degraded", retryable=True)

        # Skip whitespace-only text to prevent Telegram 400 empty-text errors.
        if not content or not content.strip():
            return SendResult(success=True, message_id=None)

        try:
            guard_result = await self._inline_preview_guard_send_result(chat_id, content, metadata)
            if guard_result is not None:
                if guard_result.success:
                    self._remember_recent_outbound_text(chat_id, content)
                return guard_result
            guard_replacement = self._inline_preview_guard_replacement(chat_id, content, metadata)
            if guard_replacement:
                content = guard_replacement

            # Bot API 10.1 rich fast-path: send the raw agent markdown via
            # sendRichMessage so tables/task lists/etc. render natively. Keep
            # Chip's inline-preview guard ahead of the rich path and preserve
            # the private chat/min-length gate when configured.
            if self._should_use_rich_message(chat_id, content, finalize=True, metadata=metadata) and self._should_attempt_rich(content, metadata=metadata):
                rich_result = await self._try_send_rich(chat_id, content, reply_to, metadata)
                if rich_result is not None:
                    if rich_result.success:
                        self._remember_recent_outbound_text(chat_id, content)
                        # Re-trigger typing like the legacy success path does.
                        try:
                            await self.send_typing(chat_id, metadata=metadata)
                        except Exception:
                            pass  # Typing failures are non-fatal
                    return rich_result

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
            requested_thread_id = self._message_thread_id_for_send(thread_id)
            used_thread_fallback = False

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
                retried_thread_not_found = False
                metadata_reply_to = self._metadata_reply_to_message_id(metadata)
                private_dm_topic_send = self._is_private_dm_topic_send(chat_id, thread_id, metadata)
                # reply_to_mode="off" on the existing telegram_dm_topic_reply_fallback path
                # is an explicit user opt-in to "message_thread_id alone is enough" (PR #23994
                # / commit 21a15b671). Honor it — don't fail loud just because the anchor was
                # suppressed by config. The new fail-loud contract only applies when the caller
                # didn't ask for the anchor to be dropped.
                dm_topic_reply_to_off = (
                    private_dm_topic_send
                    and self._reply_to_mode == "off"
                    and bool(metadata and metadata.get("telegram_dm_topic_reply_fallback"))
                )
                reply_to_source = reply_to or (
                    str(metadata_reply_to) if private_dm_topic_send and metadata_reply_to is not None else None
                )
                if private_dm_topic_send:
                    should_thread = (
                        reply_to_source is not None
                        and self._reply_to_mode != "off"
                    )
                else:
                    should_thread = self._should_thread_reply(reply_to_source, i)
                reply_to_id = int(reply_to_source) if should_thread and reply_to_source else None
                human20_reply_markup = (
                    self._human20_inline_markup(chat_id, metadata)
                    if i == len(chunks) - 1
                    else None
                )
                if private_dm_topic_send and reply_to_id is None and not dm_topic_reply_to_off:
                    return SendResult(
                        success=False,
                        error=self._dm_topic_missing_anchor_error(),
                        retryable=False,
                    )
                thread_kwargs = self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode,
                )
                if used_thread_fallback and thread_kwargs.get("message_thread_id") is not None:
                    thread_kwargs = dict(thread_kwargs)
                    thread_kwargs["message_thread_id"] = None
                effective_thread_id = thread_kwargs.get("message_thread_id")

                msg = None
                business_kwargs_disabled = False
                for _send_attempt in range(3):
                    business_kwargs = {} if business_kwargs_disabled else self._business_connection_kwargs(metadata)
                    try:
                        # Try Markdown first, fall back to plain text if it fails
                        try:
                            msg = await self._bot.send_message(
                                chat_id=int(chat_id),
                                text=chunk,
                                parse_mode=ParseMode.MARKDOWN_V2,
                                reply_to_message_id=reply_to_id,
                                reply_markup=human20_reply_markup,
                                **thread_kwargs,
                                **business_kwargs,
                                **self._link_preview_kwargs(),
                                **self._notification_kwargs(metadata),
                            )
                        except Exception as md_error:
                            md_err_lower = str(md_error).lower()
                            if business_kwargs and "business_peer_invalid" in md_err_lower:
                                logger.warning(
                                    "[%s] Telegram business peer invalid, retrying reply without business_connection_id",
                                    self.name,
                                )
                                business_kwargs_disabled = True
                                continue
                            # Markdown parsing failed, try plain text
                            if "parse" in md_err_lower or "markdown" in md_err_lower:
                                logger.warning(
                                    "[%s] MarkdownV2 parse failed, falling back to plain text: %s",
                                    self.name,
                                    _redact_telegram_error_text(md_error),
                                )
                                plain_chunk = _strip_mdv2(chunk)
                                msg = await self._bot.send_message(
                                    chat_id=int(chat_id),
                                    text=plain_chunk,
                                    parse_mode=None,
                                    reply_to_message_id=reply_to_id,
                                    reply_markup=human20_reply_markup,
                                    **thread_kwargs,
                                    **business_kwargs,
                                    **self._link_preview_kwargs(),
                                    **self._notification_kwargs(metadata),
                                )
                            else:
                                raise
                        break  # success
                    except _NetErr as send_err:
                        if business_kwargs and "business_peer_invalid" in str(send_err).lower():
                            logger.warning(
                                "[%s] Telegram business peer invalid, retrying reply without business_connection_id",
                                self.name,
                            )
                            business_kwargs_disabled = True
                            continue
                        # BadRequest is a subclass of NetworkError in
                        # python-telegram-bot but represents permanent errors
                        # (not transient network issues). Detect and handle
                        # specific cases instead of blindly retrying.
                        if _BadReq and isinstance(send_err, _BadReq):
                            if self._is_thread_not_found_error(send_err) and effective_thread_id is not None:
                                if private_dm_topic_send or (metadata and metadata.get("telegram_dm_topic_created_for_send")):
                                    return SendResult(
                                        success=False,
                                        error=str(send_err),
                                        retryable=False,
                                    )
                                # Telegram has been observed to return a
                                # one-off "thread not found" that recovers on
                                # an immediate retry (transient flake — see
                                # test_send_retries_transient_thread_not_found_before_fallback).
                                # Try the same thread_id once without sleeping
                                # before falling back to a plain send.
                                if not retried_thread_not_found:
                                    retried_thread_not_found = True
                                    logger.warning(
                                        "[%s] Thread %s not found, retrying once with same thread_id",
                                        self.name, effective_thread_id,
                                    )
                                    continue
                                # Second failure: the thread is genuinely gone.
                                # Retry without ``message_thread_id`` so the
                                # message still reaches the chat.
                                logger.warning(
                                    "[%s] Thread %s not found, retrying without message_thread_id",
                                    self.name, effective_thread_id,
                                )
                                used_thread_fallback = True
                                effective_thread_id = None
                                thread_kwargs = {"message_thread_id": None}
                                continue
                            err_lower = str(send_err).lower()
                            if "message to be replied not found" in err_lower and reply_to_id is not None:
                                if private_dm_topic_send:
                                    return SendResult(
                                        success=False,
                                        error=str(send_err),
                                        retryable=False,
                                    )
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
                                        reply_to_mode=self._reply_to_mode,
                                    )
                                    effective_thread_id = thread_kwargs.get("message_thread_id")
                                continue
                            # Other BadRequest errors are permanent — don't retry
                            raise
                        # TimedOut is also a subclass of NetworkError. A
                        # generic timeout may have reached Telegram, so don't
                        # retry; a wrapped ConnectTimeout means no connection
                        # was established, so retrying is safe. A pool timeout
                        # (httpx pool exhausted) is explicitly "not sent to
                        # Telegram" -- retrying through the loop is safe and
                        # prevents silent drops when the pool frees up.
                        is_pool_timeout = self._looks_like_pool_timeout(send_err)
                        if (
                            _TimedOut
                            and isinstance(send_err, _TimedOut)
                            and not self._looks_like_connect_timeout(send_err)
                            and not is_pool_timeout
                        ):
                            raise
                        if is_pool_timeout:
                            await self._drain_general_connections_after_pool_timeout()
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
                sent_message_id = getattr(msg, "message_id", None)
                if sent_message_id is not None:
                    message_ids.append(str(sent_message_id))
                    try:
                        from gateway import rich_sent_store

                        rich_sent_store.record(chat_id, str(sent_message_id), _strip_mdv2(chunk))
                    except Exception:
                        pass

            # Re-trigger typing indicator after sending a message.
            # Telegram clears the typing state when a new message is delivered,
            # so without this the "...typing" bubble disappears mid-response
            # (especially noticeable when the agent sends intermediate progress
            # messages like "Checking:" before running tools).
            try:
                await self.send_typing(chat_id, metadata=metadata)
            except Exception:
                pass  # Typing failures are non-fatal

            self._remember_recent_outbound_text(chat_id, content)
            return SendResult(
                success=True,
                message_id=message_ids[0] if message_ids else None,
                raw_response={
                    "message_ids": message_ids,
                    "requested_thread_id": requested_thread_id,
                    "thread_fallback": used_thread_fallback,
                },
            )

        except Exception as e:
            safe_error = _redact_telegram_error_text(e)
            logger.error("[%s] Failed to send Telegram message: %s", self.name, safe_error)
            err_str = str(e).lower()
            # Message too long — content exceeded 4096 chars. Return failure so
            # stream consumer enters fallback mode and sends the remainder.
            if "message_too_long" in err_str or "too long" in err_str:
                logger.debug(
                    "[%s] send() content too long, falling back to new-message continuation",
                    self.name,
                )
                return SendResult(
                    success=False,
                    error="message_too_long",
                    error_kind="too_long",
                )
            # TimedOut usually means the request may have reached Telegram —
            # mark as non-retryable so _send_with_retry() doesn't re-send.
            # Exceptions: a wrapped ConnectTimeout (no connection established)
            # and an httpx pool timeout (request explicitly not sent) -- both
            # are safe to re-send and must not be silently dropped.
            _to = locals().get("_TimedOut")
            is_timeout = (_to and isinstance(e, _to)) or "timed out" in err_str
            is_connect_timeout = self._looks_like_connect_timeout(e)
            is_pool_timeout = self._looks_like_pool_timeout(e)
            return SendResult(
                success=False,
                error=safe_error,
                retryable=(is_connect_timeout or is_pool_timeout or not is_timeout),
                error_kind=classify_send_error(e, safe_error),
            )

    async def send_or_update_status(
        self,
        chat_id: str,
        status_key: str,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a status message, or edit the previous one with the same key.

        Issue #30045: progress/status callbacks (context-pressure, lifecycle,
        compression, etc.) used to append a fresh bubble on every call. With
        this method, the first call sends and the message id is remembered;
        subsequent calls with the same (chat_id, status_key) edit that same
        message in place. If the edit fails (message deleted, too old, etc.)
        we drop the cached id and send fresh.
        """
        key = (str(chat_id), str(status_key))
        cached_id = self._status_message_ids.get(key)
        if cached_id is not None:
            result = await self.edit_message(
                chat_id, cached_id, content, finalize=True, metadata=metadata,
            )
            if result.success:
                if result.message_id:
                    self._status_message_ids[key] = str(result.message_id)
                return result
            # Edit failed — clear the cached id and fall through to a fresh send.
            self._status_message_ids.pop(key, None)
        result = await self.send(chat_id, content, metadata=metadata)
        if result.success and result.message_id:
            self._status_message_ids[key] = str(result.message_id)
        return result

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Edit a previously sent Telegram message.

        Telegram caps single-message text at 4096 UTF-16 codeunits.  Streaming
        replies that grow past this limit must NOT be silently truncated and
        must NOT return failure (the consumer would re-send and create a
        duplicate).  Instead this method split-and-delivers: edit the
        existing message with the first chunk and send the rest as
        continuation messages, returning the final chunk's id so subsequent
        edits target the most recent visible message.
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            rich_available = self._should_use_rich_message(chat_id, content, finalize=finalize, metadata=metadata) and self._should_attempt_rich(content, metadata=metadata)
            if finalize and rich_available:
                configured_chats = getattr(self, "_rich_message_chat_ids", set())
                if configured_chats and str(chat_id) in configured_chats:
                    rich_edit = await self._edit_rich_message(chat_id, message_id, content, metadata=metadata)
                else:
                    rich_edit = await self._try_edit_rich(chat_id, message_id, content, metadata=metadata)
                if rich_edit is not None:
                    return rich_edit

            # Pre-flight: if content already exceeds the legacy MarkdownV2 limit,
            # split-and-deliver without round-tripping a doomed edit. Rich-capable
            # tables above the legacy cap are handled before this block.
            if utf16_len(content) > self.MAX_MESSAGE_LENGTH:
                return await self._edit_overflow_split(
                    chat_id, message_id, content, finalize=finalize, metadata=metadata,
                )

            if not finalize:
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=strip_markdown(content),
                    **self._business_connection_kwargs(metadata),
                )
                return SendResult(success=True, message_id=message_id)

            if self._should_use_rich_message(chat_id, content, finalize=finalize, metadata=metadata) and self._should_attempt_rich(content, metadata=metadata):
                rich_edit = await self._try_edit_rich(chat_id, message_id, content, metadata=metadata)
                if rich_edit is not None:
                    return rich_edit

            formatted = self.format_message(content)
            try:
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=formatted,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    **self._business_connection_kwargs(metadata),
                )
            except Exception as fmt_err:
                # "Message is not modified" is a no-op, not an error
                if "not modified" in str(fmt_err).lower():
                    return SendResult(success=True, message_id=message_id)
                # Fallback: retry without markdown syntax so raw ** markers
                # never leak into Telegram when MarkdownV2 parsing fails.
                logger.warning(
                    "[%s] MarkdownV2 edit failed, falling back to plain text: %s",
                    self.name,
                    _redact_telegram_error_text(fmt_err),
                )
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=_strip_mdv2(formatted),
                    **self._business_connection_kwargs(metadata),
                )
            return SendResult(success=True, message_id=message_id)
        except Exception as e:
            err_str = str(e).lower()
            # "Message is not modified" — content identical, treat as success
            if "not modified" in err_str:
                return SendResult(success=True, message_id=message_id)
            # Reactive split-and-deliver: parse_mode formatting can inflate
            # the payload past the limit even when the raw text was under
            # (e.g. MarkdownV2 escapes).  Same fix as the pre-flight path.
            if "message_too_long" in err_str or "too long" in err_str:
                logger.debug(
                    "[%s] edit_message overflow (%d UTF-16 > %d), splitting",
                    self.name, utf16_len(content), self.MAX_MESSAGE_LENGTH,
                )
                return await self._edit_overflow_split(
                    chat_id, message_id, content, finalize=finalize, metadata=metadata,
                )
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
                    safe_retry_error = _redact_telegram_error_text(retry_err)
                    logger.error(
                        "[%s] Edit retry failed after flood wait: %s",
                        self.name, safe_retry_error,
                    )
                    return SendResult(success=False, error=safe_retry_error)
            # Transient network errors (ConnectError, timeouts, server
            # disconnects) should not permanently disable progress-message
            # editing.  Mark the result retryable so the caller knows it
            # can keep trying on the next update cycle.
            _transient_markers = (
                "connecterror",
                "connect error",
                "connection error",
                "networkerror",
                "network error",
                "timed out",
                "readtimeout",
                "writetimeout",
                "server disconnected",
                "temporarily unavailable",
                "temporary failure",
                "httpx",
            )
            _is_transient = any(m in err_str for m in _transient_markers)
            if _is_transient:
                safe_error = _redact_telegram_error_text(e)
                logger.warning(
                    "[%s] Transient network error editing message %s (will retry): %s",
                    self.name,
                    message_id,
                    safe_error,
                )
                return SendResult(success=False, error=safe_error, retryable=True)
            safe_error = _redact_telegram_error_text(e)
            logger.error(
                "[%s] Failed to edit Telegram message %s: %s",
                self.name,
                message_id,
                safe_error,
            )
            return SendResult(success=False, error=safe_error)

    async def _edit_overflow_split(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Split an oversized edit across the existing message + continuations.

        Edit the original ``message_id`` with chunk 1 (with the platform's
        usual ``(1/N)`` suffix preserved), then send the remaining chunks as
        new messages threaded as replies to the previous chunk so the user
        sees them grouped.  Returns ``SendResult(success=True,
        message_id=<last-chunk-id>, continuation_message_ids=(...))`` so the
        stream consumer can keep editing the most recent visible message
        and the gateway has full visibility into every message id we put on
        screen.

        Falls back to ``SendResult(success=False)`` only if even the first-
        chunk edit fails — that's a real adapter problem, not an overflow.
        """
        chunks = self.truncate_message(
            content, self.MAX_MESSAGE_LENGTH, len_fn=utf16_len,
        )
        if len(chunks) <= 1:
            # Defensive: shouldn't happen given the caller's pre-flight, but
            # if truncate_message returned a single chunk just edit normally.
            chunks = [content]

        # Step 1 — edit the existing message with the first chunk.
        first_chunk = chunks[0]
        try:
            if finalize:
                # Use format_message + parse_mode for the final chunk;
                # mirror edit_message's main happy-path.
                formatted = self.format_message(first_chunk)
                try:
                    await self._bot.edit_message_text(
                        chat_id=int(chat_id),
                        message_id=int(message_id),
                        text=formatted,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                except Exception as fmt_err:
                    if "not modified" not in str(fmt_err).lower():
                        logger.warning(
                            "[%s] Overflow split: MarkdownV2 first-chunk edit "
                            "failed, falling back to plain text: %s",
                            self.name, fmt_err,
                        )
                        await self._bot.edit_message_text(
                            chat_id=int(chat_id),
                            message_id=int(message_id),
                            text=_strip_mdv2(first_chunk),
                        )
            else:
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=first_chunk,
                )
        except Exception as e:
            err_str = str(e).lower()
            if "not modified" in err_str:
                # First chunk identical to current text — fall through to
                # send continuations.
                pass
            else:
                logger.error(
                    "[%s] Overflow split: first-chunk edit failed: %s",
                    self.name, e, exc_info=True,
                )
                return SendResult(success=False, error=str(e))

        # Step 2 — send each remaining chunk as a continuation message,
        # threaded as a reply to the previous so the user sees them as a
        # contiguous block.  We call self._bot.send_message directly so the
        # continuation skips ``self.send``'s own pre-chunking pass (chunks
        # are already correctly sized).  Best-effort MarkdownV2 with plain
        # fallback, mirroring send().
        continuation_ids: list[str] = []
        delivered_chunks = [first_chunk]
        prev_id = message_id
        thread_id = self._metadata_thread_id(metadata)
        for chunk in chunks[1:]:
            sent_msg = None
            reply_to_id = int(prev_id) if prev_id else None
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                thread_id,
                metadata,
                reply_to_message_id=reply_to_id,
            )
            for use_markdown in (True, False) if finalize else (False,):
                try:
                    if use_markdown:
                        text = self.format_message(chunk)
                    else:
                        # Plain attempt: on finalize the MarkdownV2 attempt
                        # failed, so degrade to clean stripped text, never
                        # the raw chunk (raw ** / ``` markers would render
                        # literally); streaming previews stay raw.
                        text = _strip_mdv2(chunk) if finalize else chunk
                    sent_msg = await self._bot.send_message(
                        chat_id=int(chat_id),
                        text=text,
                        parse_mode=ParseMode.MARKDOWN_V2 if use_markdown else None,
                        reply_to_message_id=reply_to_id,
                        **thread_kwargs,
                        **self._link_preview_kwargs(),
                        **self._notification_kwargs(metadata),
                    )
                    break
                except Exception as send_err:
                    if "reply message not found" in str(send_err).lower():
                        # Drop the reply anchor and try again.  Private DM
                        # topic fallback needs the anchor and topic id together;
                        # forum topics can still safely keep message_thread_id.
                        retry_thread_kwargs = (
                            {}
                            if metadata and metadata.get("telegram_dm_topic_reply_fallback")
                            else self._thread_kwargs_for_send(
                                chat_id, thread_id, metadata, reply_to_message_id=None
                            )
                        )
                        try:
                            sent_msg = await self._bot.send_message(
                                chat_id=int(chat_id),
                                text=_strip_mdv2(chunk) if finalize else chunk,
                                **retry_thread_kwargs,
                                **self._link_preview_kwargs(),
                                **self._notification_kwargs(metadata),
                            )
                            break
                        except Exception as _retry_err:
                            logger.warning(
                                "[%s] Overflow continuation no-reply retry failed: %s",
                                self.name, _retry_err,
                            )
                            sent_msg = None
                            break
                    if use_markdown:
                        # try plain text on next loop iteration
                        continue
                    logger.warning(
                        "[%s] Overflow continuation send failed: %s",
                        self.name, send_err,
                    )
                    sent_msg = None
                    break
            if sent_msg is None:
                # Continuation failed — the user has chunk 1 + however many
                # continuations succeeded, but NOT the full response.  Do not
                # report success: the stream consumer treats a successful edit
                # as final delivery on got_done, which would suppress fallback
                # delivery and leave the Telegram topic clipped after the last
                # delivered chunk.
                logger.warning(
                    "[%s] Overflow split: stopped at %d/%d chunks delivered",
                    self.name, 1 + len(continuation_ids), len(chunks),
                )
                delivered_prefix = "".join(
                    re.sub(r" \(\d+/\d+\)$", "", delivered)
                    for delivered in delivered_chunks
                )
                return SendResult(
                    success=False,
                    message_id=prev_id,
                    error="overflow_continuation_failed",
                    retryable=True,
                    raw_response={
                        "partial_overflow": True,
                        "delivered_chunks": 1 + len(continuation_ids),
                        "total_chunks": len(chunks),
                        "last_message_id": prev_id,
                        "delivered_prefix": delivered_prefix,
                        "continuation_message_ids": tuple(continuation_ids),
                    },
                    continuation_message_ids=tuple(continuation_ids),
                )
            new_id = str(getattr(sent_msg, "message_id", "")) or prev_id
            continuation_ids.append(new_id)
            delivered_chunks.append(chunk)
            prev_id = new_id

        last_id = continuation_ids[-1] if continuation_ids else message_id
        logger.debug(
            "[%s] Overflow split delivered %d chunks; last_id=%s",
            self.name, 1 + len(continuation_ids), last_id,
        )
        return SendResult(
            success=True,
            message_id=last_id,
            continuation_message_ids=tuple(continuation_ids),
        )

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

    def supports_draft_streaming(
        self,
        chat_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Telegram supports sendMessageDraft for private chats only.

        Bot API 9.5 (March 2026) opened ``sendMessageDraft`` to all bots
        unconditionally for private (DM) chats.  Groups, supergroups, and
        channels still rely on the edit-based path.

        We additionally require ``self._bot`` to expose ``send_message_draft``
        (added to python-telegram-bot in 22.6); older PTB installs gracefully
        fall back to the edit path even on DMs.
        """
        if not self._bot or not hasattr(self._bot, "send_message_draft"):
            return False
        return (chat_type or "").lower() in {"dm", "private"}

    async def send_draft(
        self,
        chat_id: str,
        draft_id: int,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Stream a partial message via Telegram's native draft API.

        Uses ``sendRichMessageDraft`` (Bot API 10.1) with the raw markdown when
        rich messages are enabled and supported, otherwise the plain-text
        ``sendMessageDraft``. The Bot API animates the preview when the same
        ``draft_id`` is reused across consecutive calls in the same chat.  When
        the response finishes, the caller sends the final text via the normal
        ``send`` path; the draft preview clears naturally on the client
        (Telegram has no Bot API to "promote" a draft to a real message — the
        final ``sendMessage``/``sendRichMessage`` is what the user receives in
        their history).
        """
        if not self._bot:
            return SendResult(success=False, error="not_connected")

        # Rich draft fast-path (Bot API 10.1 sendRichMessageDraft): render the
        # streaming preview with the same raw markdown the final
        # sendRichMessage will persist, so the animated draft matches the final
        # message. Any failure degrades to the legacy plain-text draft below.
        if self._should_attempt_rich_draft(content):
            if await self._try_send_rich_draft(chat_id, draft_id, content, metadata):
                # Drafts have no message_id; report success without one.
                return SendResult(success=True, message_id=None)

        if not hasattr(self._bot, "send_message_draft"):
            return SendResult(success=False, error="api_unavailable")

        # Trim to the same UTF-16 budget the platform enforces on regular
        # sends.  Drafts have the same length contract as messages.
        text = content if len(content) <= self.MAX_MESSAGE_LENGTH else \
            self.truncate_message(content, self.MAX_MESSAGE_LENGTH, len_fn=utf16_len)[0]

        thread_id = self._metadata_thread_id(metadata)

        # Apply the same MarkdownV2 conversion the regular ``send`` path uses
        # so the animated draft preview renders with identical formatting to
        # the final message.  Without this, the draft streams as raw text and
        # the final ``sendMessage`` (which DOES use MarkdownV2) snaps into
        # formatted output, producing a jarring visual shift at the end of the
        # response.  We try MarkdownV2 first and fall back to plain text if a
        # malformed escape would be rejected — mirroring the (True, False)
        # retry the streaming send loop uses — so a single bad token never
        # kills draft streaming for the whole response.
        for use_markdown in (True, False):
            kwargs: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "draft_id": int(draft_id),
                "text": self.format_message(text) if use_markdown else text,
            }
            if use_markdown:
                kwargs["parse_mode"] = ParseMode.MARKDOWN_V2
            if thread_id is not None:
                kwargs["message_thread_id"] = thread_id

            try:
                ok = await self._bot.send_message_draft(**kwargs)
                if ok:
                    # Drafts have no message_id; we report success without one
                    # so the caller knows the animation frame landed.
                    return SendResult(success=True, message_id=None)
                return SendResult(success=False, error="draft_rejected")
            except Exception as e:
                # A MarkdownV2 parse failure (BadRequest "can't parse entities")
                # is recoverable: retry once as plain text.  Any other failure
                # (chat doesn't allow drafts, transient hiccup) — or a failure
                # on the plain-text attempt — propagates to the caller, which
                # treats it as "fall back to edit-based for this response".
                if use_markdown and self._is_bad_request_error(e):
                    logger.debug(
                        "[%s] sendMessageDraft MarkdownV2 rejected, retrying "
                        "as plain text (chat=%s draft_id=%s): %s",
                        self.name, chat_id, draft_id, e,
                    )
                    continue
                logger.debug(
                    "[%s] sendMessageDraft failed (chat=%s draft_id=%s): %s",
                    self.name, chat_id, draft_id, e,
                )
                return SendResult(success=False, error=str(e))

        return SendResult(success=False, error="draft_rejected")

    async def _send_message_with_thread_fallback(self, **kwargs):
        """Send a Telegram message, retrying once without message_thread_id
        if Telegram returns 'Message thread not found'.

        Used for control-style sends (approval prompts, model picker,
        update prompts) that can carry a stale thread_id from a DM
        reply chain.  The streaming send loop has its own equivalent
        (PR #3390) at the body of ``send``; this helper applies the
        same retry pattern to the non-streaming control paths.
        """
        if not self._bot:
            raise RuntimeError("Not connected")

        message_thread_id = kwargs.get("message_thread_id")
        try:
            return await self._bot.send_message(**kwargs)
        except Exception as send_err:
            if (
                message_thread_id is not None
                and self._is_bad_request_error(send_err)
                and self._is_thread_not_found_error(send_err)
            ):
                logger.warning(
                    "[%s] Thread %s not found for control message, retrying without message_thread_id",
                    self.name,
                    message_thread_id,
                )
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop("message_thread_id", None)
                return await self._bot.send_message(**retry_kwargs)
            raise

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
            text = self.format_message(f"⚕ *Update needs your input:*\n\n{prompt}{default_hint}")
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✓ Yes", callback_data="update_prompt:y"),
                    InlineKeyboardButton("✗ No", callback_data="update_prompt:n"),
                ]
            ])
            thread_id = self._metadata_thread_id(metadata)
            reply_to_id = self._reply_to_message_id_for_send(None, metadata, reply_to_mode=self._reply_to_mode)
            msg = await self._send_message_with_thread_fallback(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
                reply_to_message_id=reply_to_id,
                **self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode
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
        allow_permanent: bool = True,
        smart_denied: bool = False,
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
            if smart_denied:
                text += "\n\n<b>Smart DENY:</b> owner override applies to this one operation only."

            # Resolve thread context for thread replies
            thread_id = self._metadata_thread_id(metadata)

            # We'll use the message_id as part of callback_data to look up session_key
            # Send a placeholder first, then update — or use a counter.
            # Simpler: use a monotonic counter to generate short IDs.
            import itertools
            if not hasattr(self, "_approval_counter"):
                self._approval_counter = itertools.count(1)
            approval_id = next(self._approval_counter)

            from plugins.platforms.telegram import adapter as telegram_plugin

            buttons = [
                telegram_plugin.InlineKeyboardButton(
                    "✅ Allow Once", callback_data=f"ea:once:{approval_id}"
                )
            ]
            if not smart_denied:
                buttons.append(
                    telegram_plugin.InlineKeyboardButton(
                        "✅ Session", callback_data=f"ea:session:{approval_id}"
                    )
                )
                if allow_permanent:
                    buttons.append(
                        telegram_plugin.InlineKeyboardButton(
                            "✅ Always", callback_data=f"ea:always:{approval_id}"
                        )
                    )
            buttons.append(
                telegram_plugin.InlineKeyboardButton(
                    "❌ Deny", callback_data=f"ea:deny:{approval_id}"
                )
            )
            keyboard = telegram_plugin.InlineKeyboardMarkup([buttons])

            kwargs: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "text": text,
                "parse_mode": ParseMode.HTML,
                "reply_markup": keyboard,
                **self._link_preview_kwargs(),
            }
            reply_to_id = self._reply_to_message_id_for_send(None, metadata, reply_to_mode=self._reply_to_mode)
            kwargs["reply_to_message_id"] = reply_to_id
            kwargs.update(
                self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode
                )
            )

            msg = await self._send_message_with_thread_fallback(**kwargs)

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
            preview = self.format_message(message if len(message) <= 3800 else message[:3800] + "...")

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
                "parse_mode": ParseMode.MARKDOWN_V2,
                "reply_markup": keyboard,
                **self._link_preview_kwargs(),
            }
            reply_to_id = self._reply_to_message_id_for_send(None, metadata, reply_to_mode=self._reply_to_mode)
            kwargs["reply_to_message_id"] = reply_to_id
            kwargs.update(
                self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode
                )
            )

            msg = await self._send_message_with_thread_fallback(**kwargs)
            self._slash_confirm_state[confirm_id] = session_key
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_slash_confirm failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Render a clarify prompt with one inline button per choice.

        Multi-choice mode (``choices`` non-empty): renders one button per
        option plus a final "✏️ Other (type answer)" button.  Picking the
        "Other" button flips the entry into text-capture mode so the next
        message becomes the response.

        Open-ended mode (``choices`` empty): renders the question as plain
        text — no buttons.  The next message in the session is captured by
        the gateway's text-intercept and resolves the clarify.
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            text = f"❓ {_html.escape(question)}"
            thread_id = self._metadata_thread_id(metadata)

            if choices:
                # Render full option text in the message body so mobile
                # users can read long choices that would be truncated in
                # inline button labels.  Buttons keep short numeric labels
                # (1, 2, …, Other) to avoid Telegram truncation.
                option_lines = "\n".join(
                    f"{i + 1}. {_html.escape(str(c))}"
                    for i, c in enumerate(choices)
                )
                text += f"\n\n{option_lines}"

            kwargs: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "text": text,
                "parse_mode": ParseMode.HTML,
                **self._link_preview_kwargs(),
            }

            if choices:
                # Telegram caps callback_data at 64 bytes; keep "cl:<id>:<idx>"
                # short.
                rows = []
                for idx in range(len(choices)):
                    rows.append([
                        InlineKeyboardButton(
                            str(idx + 1),
                            callback_data=f"cl:{clarify_id}:{idx}",
                        )
                    ])
                rows.append([
                    InlineKeyboardButton(
                        "✏️ Other (type answer)",
                        callback_data=f"cl:{clarify_id}:other",
                    )
                ])
                kwargs["reply_markup"] = InlineKeyboardMarkup(rows)

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

            msg = await self._send_message_with_thread_fallback(**kwargs)
            self._clarify_state[clarify_id] = session_key
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_clarify failed: %s", self.name, e)
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
            # Build provider buttons — folds provider groups (display only).
            keyboard = self._build_provider_keyboard(providers)

            provider_label = get_label(current_provider)
            text = self.format_message(
                (
                    f"⚙ *Model Configuration*\n\n"
                    f"Current model: `{current_model or 'unknown'}`\n"
                    f"Provider: {provider_label}\n\n"
                    f"Select a provider:"
                )
            )

            thread_id = metadata.get("thread_id") if metadata else None
            reply_to_id = self._reply_to_message_id_for_send(None, metadata, reply_to_mode=self._reply_to_mode)
            msg = await self._send_message_with_thread_fallback(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
                reply_to_message_id=reply_to_id,
                **self._thread_kwargs_for_send(
                    chat_id,
                    thread_id,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode
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

    def _build_provider_keyboard(self, providers: list):
        """Build the top-level provider keyboard, folding provider groups.

        Provider families (Kimi/Moonshot, MiniMax, xAI Grok, ...) collapse to
        a single ``mpg:<gid>`` button; tapping it drills into a member
        sub-keyboard. Single providers (and groups with only one authenticated
        member) render as direct ``mp:<slug>`` buttons. Grouping mirrors the
        CLI ``hermes model`` picker via the shared ``group_providers`` fold,
        so all surfaces stay consistent.
        """
        try:
            from hermes_cli.models import group_providers
        except Exception:
            group_providers = None

        by_slug = {p.get("slug"): p for p in providers}

        def _provider_button(p):
            count = p.get("total_models", len(p.get("models", [])))
            label = f"{p['name']} ({count})"
            if p.get("is_current"):
                label = f"✓ {label}"
            return InlineKeyboardButton(label, callback_data=f"mp:{p['slug']}")

        buttons: list = []
        if group_providers is not None:
            for row in group_providers([p.get("slug") for p in providers]):
                if row["kind"] == "group":
                    members = [by_slug[m] for m in row["members"] if m in by_slug]
                    count = sum(
                        m.get("total_models", len(m.get("models", []))) for m in members
                    )
                    label = f"{row['label']} ▸ ({count})"
                    if any(m.get("is_current") for m in members):
                        label = f"✓ {label}"
                    buttons.append(
                        InlineKeyboardButton(label, callback_data=f"mpg:{row['group_id']}")
                    )
                else:
                    p = by_slug.get(row["slug"])
                    if p is not None:
                        buttons.append(_provider_button(p))
        else:
            for p in providers:
                buttons.append(_provider_button(p))

        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("✗ Cancel", callback_data="mx")])
        return InlineKeyboardMarkup(rows)

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
        """Handle model picker inline keyboard callbacks (mp:/mm:/mc:/mb:/mx:/mg:)."""
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
                text=self.format_message(
                    (
                        f"⚙ *Model Configuration*\n\n"
                        f"Provider: *{pname}*{page_info}\n"
                        f"Select a model:{extra}"
                    )
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
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
                text=self.format_message(
                    (
                        f"⚙ *Model Configuration*\n\n"
                        f"Provider: *{pname}*{page_info}\n"
                        f"Select a model:{extra}"
                    )
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data.startswith("mc:"):
            # --- Expensive model confirmed: perform the switch ---
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

            switch_failed = False
            try:
                result_text = await callback(chat_id, model_id, provider_slug)
            except Exception as exc:
                logger.error("Model picker switch failed: %s", exc)
                result_text = f"Error switching model: {exc}"
                switch_failed = True

            try:
                await query.edit_message_text(
                    text=self.format_message(result_text),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=None,
                )
            except Exception:
                try:
                    await query.edit_message_text(
                        text=result_text,
                        parse_mode=None,
                        reply_markup=None,
                    )
                except Exception:
                    pass
            await query.answer(
                text="Switch failed." if switch_failed else "Model switched!"
            )
            self._model_picker_state.pop(chat_id, None)

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
                from hermes_cli.model_cost_guard import expensive_model_warning

                # Pricing lookup can hit models.dev / a /models endpoint on a
                # cache miss — keep it off the event loop.
                warning = await asyncio.to_thread(
                    expensive_model_warning,
                    model_id,
                    provider=provider_slug,
                )
            except Exception:
                warning = None
            if warning is not None:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Switch anyway", callback_data=f"mc:{idx}")],
                    [
                        InlineKeyboardButton("◀ Back", callback_data="mb"),
                        InlineKeyboardButton("✗ Cancel", callback_data="mx"),
                    ],
                ])
                await query.edit_message_text(
                    text=self.format_message(
                        f"⚠ *Expensive Model Warning*\n\n{warning.message}"
                    ),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard,
                )
                await query.answer(text="Confirm expensive model")
                return

            switch_failed = False
            try:
                result_text = await callback(chat_id, model_id, provider_slug)
            except Exception as exc:
                logger.error("Model picker switch failed: %s", exc)
                result_text = f"Error switching model: {exc}"
                switch_failed = True

            # Edit message to show confirmation, remove buttons
            try:
                await query.edit_message_text(
                    text=self.format_message(result_text),
                    parse_mode=ParseMode.MARKDOWN_V2,
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
            await query.answer(
                text="Switch failed." if switch_failed else "Model switched!"
            )

            # Clean up state
            self._model_picker_state.pop(chat_id, None)

        elif data.startswith("mpg:"):
            # --- Provider group selected: show member providers ---
            group_id = data[4:]
            try:
                from hermes_cli.models import PROVIDER_GROUPS
                _label, _desc, member_slugs = PROVIDER_GROUPS.get(group_id, ("", "", []))
            except Exception:
                _label, member_slugs = "", []

            by_slug = {p["slug"]: p for p in state["providers"]}
            members = [by_slug[m] for m in member_slugs if m in by_slug]
            if not members:
                await query.answer(text="Group not found.")
                return

            buttons = []
            for p in members:
                count = p.get("total_models", len(p.get("models", [])))
                label = f"{p['name']} ({count})"
                if p.get("is_current"):
                    label = f"✓ {label}"
                buttons.append(
                    InlineKeyboardButton(label, callback_data=f"mp:{p['slug']}")
                )
            rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
            rows.append([
                InlineKeyboardButton("◀ Back", callback_data="mb"),
                InlineKeyboardButton("✗ Cancel", callback_data="mx"),
            ])
            keyboard = InlineKeyboardMarkup(rows)

            await query.edit_message_text(
                text=self.format_message(
                    (
                        f"⚙ *Model Configuration*\n\n"
                        f"Provider family: *{_label or group_id}*\n\n"
                        f"Select a provider:"
                    )
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data == "mb":
            # --- Back to provider list (folds groups) ---
            keyboard = self._build_provider_keyboard(state["providers"])

            try:
                provider_label = get_label(state["current_provider"])
            except Exception:
                provider_label = state["current_provider"]

            await query.edit_message_text(
                text=self.format_message(
                    (
                        f"⚙ *Model Configuration*\n\n"
                        f"Current model: `{state['current_model'] or 'unknown'}`\n"
                        f"Provider: {provider_label}\n\n"
                        f"Select a provider:"
                    )
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
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

    def _gptprof_paths(self) -> dict[str, Path]:
        """Return gptprof storage paths, with env overrides for tests/profiles."""
        hermes_home = Path(os.getenv("HERMES_HOME", "/home/hermes/.hermes"))
        return {
            "auth": Path(os.getenv("GPTPROF_AUTH_PATH", str(hermes_home / "auth.json"))),
            "config": Path(os.getenv("GPTPROF_CONFIG_PATH", str(hermes_home / "config.yaml"))),
            "hcp": Path(os.getenv("GPTPROF_HCP_DIR", str(hermes_home / "skills" / "chip" / "hcp"))),
            "cache": Path(os.getenv("GPTPROF_CACHE_PATH", "/tmp/gptprof_usage_cache.json")),
            "send_buttons": Path(os.getenv("GPTPROF_SEND_BUTTONS", str(hermes_home / "skills" / "chip" / "gptprof" / "send_buttons.py"))),
        }

    @staticmethod
    def _gptprof_load_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    @staticmethod
    def _gptprof_save_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _gptprof_atomic_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _gptprof_switch_profile(self, slug: str, model: str) -> str:
        """Switch the active Codex OAuth profile used by gptprof buttons."""
        paths = self._gptprof_paths()
        profile_path = paths["hcp"] / f"{slug}.json"
        profile = self._gptprof_load_json(profile_path, {})
        if not isinstance(profile, dict) or not profile.get("access_token"):
            raise FileNotFoundError(f"Profile token not found: {slug}")

        auth = self._gptprof_load_json(paths["auth"], {})
        if not isinstance(auth, dict):
            auth = {}
        codex = dict(auth.get("codex") or {})
        codex.update({
            "profile": slug,
            "email": profile.get("email"),
            "plan": profile.get("plan"),
            "access_token": profile.get("access_token"),
            "refresh_token": profile.get("refresh_token"),
        })
        auth["codex"] = codex

        providers = auth.setdefault("providers", {})
        provider_state = providers.setdefault("openai-codex", {})
        provider_tokens = dict(provider_state.get("tokens") or {})
        provider_tokens.update({
            "profile": slug,
            "email": profile.get("email"),
            "plan": profile.get("plan"),
            "access_token": profile.get("access_token"),
            "refresh_token": profile.get("refresh_token"),
        })
        provider_state["tokens"] = provider_tokens
        provider_state["auth_mode"] = "chatgpt"
        provider_state.pop("last_auth_error", None)
        auth["active_provider"] = "openai-codex"

        pool_root = auth.setdefault("credential_pool", {})
        pool = pool_root.setdefault("openai-codex", [])
        if not isinstance(pool, list):
            pool = []
            pool_root["openai-codex"] = pool
        source = f"gptprof:{slug}"
        selected_entry = {
            "source": source,
            "profile": slug,
            "label": slug,
            "provider": "openai-codex",
            "email": profile.get("email"),
            "plan": profile.get("plan"),
            "access_token": profile.get("access_token"),
            "refresh_token": profile.get("refresh_token"),
            "priority": 0,
            "last_status": "ok",
            "last_status_at": time.time(),
        }
        remaining_pool = []
        for item in pool:
            if not isinstance(item, dict):
                continue
            item_source = str(item.get("source") or "")
            item_profile = str(item.get("profile") or item.get("label") or "")
            if item_source in {source, "device_code"} or item_profile == slug:
                continue
            if item.get("priority") == 0:
                item = {**item, "priority": 10}
            remaining_pool.append(item)
        pool_root["openai-codex"] = [selected_entry, *remaining_pool]
        self._gptprof_save_json(paths["auth"], auth)

        # Persist the model route so a gateway restart keeps the selected GPT profile on 5.5.
        try:
            import yaml
            config = yaml.safe_load(paths["config"].read_text(encoding="utf-8")) or {}
            if isinstance(config, dict):
                model_cfg = config.setdefault("model", {})
                if isinstance(model_cfg, dict):
                    model_cfg["provider"] = "openai-codex"
                    model_cfg["default"] = model
                    self._gptprof_atomic_text(paths["config"], yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
        except Exception as exc:
            logger.warning("Failed to persist gptprof model route: %s", exc)

        try:
            if paths["cache"].exists():
                cache = self._gptprof_load_json(paths["cache"], {})
                if isinstance(cache, dict):
                    cache.pop(slug, None)
                    self._gptprof_save_json(paths["cache"], cache)
        except Exception:
            pass
        return str(profile.get("email") or slug)

    def _gptprof_post_json(self, url: str, payload: dict[str, Any], *, form: bool = False) -> dict[str, Any]:
        data = (urllib.parse.urlencode(payload).encode("utf-8") if form else json.dumps(payload).encode("utf-8"))
        headers = {
            "Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Hermes gptprof)",
            "Origin": "https://auth.openai.com",
            "Referer": "https://auth.openai.com/codex/device",
        }
        last_exc: Exception | None = None
        for attempt in range(3):
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=20) as response:
                    return json.loads(response.read().decode("utf-8", "replace"))
            except (urllib.error.HTTPError, urllib.error.URLError) as exc:
                last_exc = exc
                code = getattr(exc, "code", None)
                if code not in {429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 530}:
                    raise
                if attempt >= 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def _gptprof_save_new_auth_tokens(self, slug: str, access_token: str, refresh_token: str) -> None:
        paths = self._gptprof_paths()
        profile_path = paths["hcp"] / f"{slug}.json"
        profile = self._gptprof_load_json(profile_path, {})
        if not isinstance(profile, dict):
            profile = {}
        profile.update({
            "profile": slug,
            "email": profile.get("email") or f"{slug}@gmail.com",
            "plan": profile.get("plan") or "OpenAI",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "updated_at": time.time(),
            "source": "gptprof:new_auth_device_code",
        })
        profile.pop("_refresh_error", None)
        self._gptprof_save_json(profile_path, profile)
        self._gptprof_switch_profile(slug, "gpt-5.5")

    async def _gptprof_poll_device_auth(self, slug: str, user_code: str, device_auth_id: str, interval: int, chat_id: int | str | None) -> None:
        issuer = "https://auth.openai.com"
        client_id = "app_EMoamEEZ73f0CkXaXp7hrann"
        deadline = time.monotonic() + 15 * 60

        def poll_once() -> dict[str, Any] | None:
            try:
                return self._gptprof_post_json(
                    f"{issuer}/api/accounts/deviceauth/token",
                    {"device_auth_id": device_auth_id, "user_code": user_code},
                )
            except urllib.error.HTTPError as exc:
                if exc.code in {403, 404}:
                    return None
                raise

        try:
            code_resp = None
            while time.monotonic() < deadline:
                await asyncio.sleep(max(3, int(interval or 5)))
                code_resp = await asyncio.to_thread(poll_once)
                if code_resp:
                    break
            if not code_resp:
                if chat_id and self._bot:
                    await self._bot.send_message(chat_id=chat_id, text=f"gptprof auth timeout for {slug}")
                return
            token_data = await asyncio.to_thread(
                self._gptprof_post_json,
                f"{issuer}/oauth/token",
                {
                    "grant_type": "authorization_code",
                    "code": code_resp.get("authorization_code", ""),
                    "code_verifier": code_resp.get("code_verifier", ""),
                    "client_id": client_id,
                    "redirect_uri": f"{issuer}/deviceauth/callback",
                },
                form=True,
            )
            await asyncio.to_thread(
                self._gptprof_save_new_auth_tokens,
                slug,
                token_data["access_token"],
                token_data.get("refresh_token") or "",
            )
            await asyncio.to_thread(self._gptprof_send_card)
            if chat_id and self._bot:
                await self._bot.send_message(chat_id=chat_id, text=f"✅ gptprof auth saved for {slug}")
        except Exception as exc:
            logger.exception("gptprof device auth failed for %s: %s", slug, exc)
            if chat_id and self._bot:
                await self._bot.send_message(chat_id=chat_id, text=f"⚠️ gptprof auth failed for {slug}: {type(exc).__name__}")

    def _gptprof_send_card(self) -> None:
        script = self._gptprof_paths()["send_buttons"]
        if script.exists():
            subprocess.run([sys.executable, str(script)], check=False, timeout=90)

    async def _handle_gptprof_callback(self, query, data: str, query_chat_id: int | str | None) -> None:
        """Handle Chip's GPT profile switcher callbacks."""
        caller_id = str(getattr(query.from_user, "id", ""))
        if caller_id != os.getenv("GPTPROF_ALLOWED_USER", "617744661"):
            await query.answer(text="⛔ Not authorized.")
            return

        if data in {"gptprof:refresh", "gptprof:check_auth"}:
            await query.answer(text="Refreshing gptprof…")
            await asyncio.to_thread(self._gptprof_send_card)
            return

        if data == "gptprof:new_auth" or data.startswith("gptprof:new_auth:"):
            paths = self._gptprof_paths()
            auth = self._gptprof_load_json(paths["auth"], {})
            if data.startswith("gptprof:new_auth:"):
                slug = data.rsplit(":", 1)[-1].strip()
            else:
                slug = str(((auth.get("codex") or {}) if isinstance(auth, dict) else {}).get("profile") or "markov495")
            issuer = "https://auth.openai.com"
            client_id = "app_EMoamEEZ73f0CkXaXp7hrann"
            try:
                device = await asyncio.to_thread(
                    self._gptprof_post_json,
                    f"{issuer}/api/accounts/deviceauth/usercode",
                    {"client_id": client_id},
                )
                code = device["user_code"]
                interval = int(device.get("interval") or 5)
                asyncio.create_task(self._gptprof_poll_device_auth(slug, code, device["device_auth_id"], interval, query_chat_id))
                await query.answer(text=f"Auth code for {slug}: {code}")
                await query.edit_message_text(
                    text=(
                        f"➕ New auth for {_html.escape(slug)}\n\n"
                        f"Open: {issuer}/codex/device\n"
                        f"Code: <code>{_html.escape(code)}</code>\n\n"
                        "Log into the matching ChatGPT account, then return here."
                    ),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                logger.exception("Failed to start gptprof device auth: %s", exc)
                await query.answer(text="Failed to start auth.")
            return

        parts = data.split(":", 2)
        if len(parts) == 3 and parts[0] == "gptprof":
            slug, model = parts[1], parts[2]
            try:
                email = await asyncio.to_thread(self._gptprof_switch_profile, slug, model)
                await query.answer(text=f"Switched to {slug}")
                try:
                    await query.edit_message_text(text=f"✅ GPT profile switched to {slug}\n{email}\nModel: {model}\n\nUse /new for a fresh session.")
                except Exception:
                    pass
                await asyncio.to_thread(self._gptprof_send_card)
            except Exception as exc:
                logger.exception("Failed to switch gptprof profile: %s", exc)
                await query.answer(text=f"Failed to switch {slug}.")
            return

        await query.answer(text="Unknown gptprof action.")

    @staticmethod
    def _extract_supergoal_body_from_callback_text(text: str) -> str:
        """Extract a durable Supergoal goal body embedded in a button prompt."""
        return goal_launch.extract_supergoal_body(text)

    def _build_supergoal_callback_event(
        self,
        query: Any,
        goal_body: str,
        *,
        session_key: Optional[str] = None,
    ) -> Optional[MessageEvent]:
        """Build a synthetic `/goal` message from a Telegram button callback."""
        query_message = getattr(query, "message", None)
        query_chat = getattr(query_message, "chat", None)
        if query_message is None or query_chat is None:
            return None

        chat_type_raw = getattr(query_chat, "type", None)
        chat_type_value = str(getattr(chat_type_raw, "value", chat_type_raw) or "").lower()
        if chat_type_value == "private":
            chat_type = "dm"
        elif chat_type_value == "supergroup":
            # Keep callback sources aligned with normal message ingestion.
            # Telegram topics are represented as chat_type="group" + thread_id
            # elsewhere in this adapter; using "forum" here fragments the
            # session key and starts /goal in an invisible sibling session.
            chat_type = "group"
        elif chat_type_value in {"group", "channel"}:
            chat_type = chat_type_value
        else:
            chat_type = "dm"

        user = getattr(query, "from_user", None)
        source = self.build_source(
            chat_id=str(getattr(query_message, "chat_id", getattr(query_chat, "id", ""))),
            chat_name=getattr(query_chat, "title", None) or getattr(query_chat, "full_name", None),
            chat_type=chat_type,
            user_id=str(getattr(user, "id", "")) if user is not None else None,
            user_name=getattr(user, "full_name", None) or getattr(user, "first_name", None),
            thread_id=(
                str(getattr(query_message, "message_thread_id"))
                if getattr(query_message, "message_thread_id", None) is not None else None
            ),
            message_id=str(getattr(query_message, "message_id", "")),
        )
        event = MessageEvent(
            text=f"/goal {goal_body}",
            message_type=MessageType.TEXT,
            source=source,
            raw_message=query_message,
            message_id=str(getattr(query_message, "message_id", "")),
        )
        if session_key:
            # Live clarify buttons are owned by the session that rendered the
            # prompt, not necessarily by the authorized user who clicks it in a
            # shared/group context. Preserve that owner for GoalManager lookup.
            setattr(event, "_session_key_override", session_key)
        return event

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

        # --- Chip GPT profile callbacks (gptprof:<slug>:<model>, new auth, refresh) ---
        if data.startswith("gptprof:"):
            await self._handle_gptprof_callback(query, data, query_chat_id)
            return

        # --- Model picker callbacks ---
        if data.startswith(("mp:", "mpg:", "mm:", "mc:", "mb", "mx", "mg:")):
            chat_id = str(query.message.chat_id) if query.message else None
            if chat_id:
                await self._handle_model_picker_callback(query, data, chat_id)
            return

        # --- Gmail-triage callbacks (gt:verb:arg) ---
        if data.startswith("gt:"):
            await self._handle_gmail_triage_callback(
                query,
                data,
                query_chat_id=query_chat_id,
                query_chat_type=query_chat_type,
                query_thread_id=query_thread_id,
                query_user_name=query_user_name,
            )
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
                    count = 0

                if not count:
                    label = "⌛ Approval expired"

                await query.answer(text=label)

                # Edit message to show decision, remove buttons
                try:
                    await query.edit_message_text(
                        text=self.format_message(f"{label} by {user_display}"),
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=None,
                    )
                except Exception:
                    pass  # non-fatal if edit fails

                # Resume the typing indicator — paused when the approval was
                # sent (gateway/run.py).  The text /approve and /deny paths
                # call resume_typing_for_chat here too; without it, typing
                # stays paused for the rest of the turn after an inline
                # button click.
                if count and query_chat_id is not None:
                    self.resume_typing_for_chat(str(query_chat_id))
            return

        # --- SUBCONSCIOUS v3 proposal feedback callbacks (subcv3:verb:proposal_id) ---
        if data.startswith("subcv3:"):
            parts = data.split(":", 2)
            action_map = {
                "a": ("accept", "Accepted"),
                "r": ("reject", "Rejected"),
                "k": ("skip", "Skipped"),
                "s": ("save", "Saved"),
                "m": ("mute", "Muted"),
                "d": ("deep_dive", "Deep dive saved"),
            }
            if len(parts) != 3 or parts[1] not in action_map or not re.fullmatch(r"prp_[a-f0-9]{16,64}", parts[2]):
                await query.answer(text="Invalid SUBCONSCIOUS v3 feedback data.")
                return

            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="⛔ You are not authorized to answer this proposal.")
                return

            action, label = action_map[parts[1]]
            proposal_id = parts[2]
            runtime = Path(os.getenv("SUBC_V3_RUNTIME") or "/home/hermes/.hermes/profiles/subc/v3")
            project = Path(os.getenv("SUBC_V3_PROJECT") or "/home/hermes/workspace/chip-subconscious-v3")
            actor_ref_hash = "sha256:" + hashlib.sha256(caller_id.encode("utf-8")).hexdigest()
            cmd = [
                sys.executable,
                str(project / "scripts" / "subc_v3_feedback.py"),
                "--state", str(runtime / "delivery-state.json"),
                "--proposal-id", proposal_id,
                "--action", action,
                "--actor-ref-hash", actor_ref_hash,
            ]
            try:
                completed = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=str(project))
                if completed.returncode != 0:
                    raise RuntimeError(completed.stderr or completed.stdout or "feedback command failed")
                payload = json.loads(completed.stdout or "{}")
                if not payload.get("ok") or payload.get("writes_canonical_memory") is not False:
                    raise RuntimeError("feedback command returned an unsafe result")
                await query.answer(text=label)
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
            except Exception as exc:
                logger.error("Failed to record SUBCONSCIOUS v3 feedback: %s", exc)
                await query.answer(text="Failed to record SUBCONSCIOUS v3 feedback.")
            return

        # --- Subconscious pending-intent callbacks (subc:y|n:token) ---
        if data.startswith("subc:"):
            parts = data.split(":", 2)
            if len(parts) != 3 or parts[1] not in {"y", "n"}:
                await query.answer(text="Invalid pending-intent data.")
                return

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

            decision = "approved" if parts[1] == "y" else "rejected"
            token = parts[2]
            room = Path(os.getenv("SUBC_ROOM") or SUBC_DEFAULT_ROOM)
            project = Path(os.getenv("SUBC_PROJECT") or SUBC_DEFAULT_PROJECT)
            state_path = room / "posted_pending_intents.json"
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                intent_id = state.get("tokens", {}).get(token)
                if not intent_id:
                    await query.answer(text="This pending intent has already been resolved.")
                    return
                entry = state.get("posted", {}).get(intent_id, {})
                intent_id = str(intent_id)
                source_message_id = str(getattr(query.message, "message_id", "") or entry.get("message_id") or "")
                approver = query_user_name or caller_id or "Chip"

                scripts = project / "scripts"
                transition_cmd = [
                    sys.executable,
                    str(scripts / "subc_transition.py"),
                    "--room", str(room),
                    "--intent-id", intent_id,
                    "--decision", decision,
                    "--approver", approver,
                ]
                if source_message_id:
                    transition_cmd.extend(["--source-message-id", source_message_id])

                commands = [transition_cmd]
                if decision == "approved":
                    commands.extend([
                        [sys.executable, str(scripts / "subc_build_packet.py"), "--room", str(room), "--intent-id", intent_id],
                        [sys.executable, str(scripts / "subc_shaw_enqueue.py"), "--room", str(room), "--project", str(project), "--intent-id", intent_id],
                    ])

                payload = {}
                for cmd in commands:
                    completed = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=str(project))
                    if completed.stdout:
                        try:
                            payload.update(json.loads(completed.stdout))
                        except json.JSONDecodeError:
                            pass
                    if completed.returncode != 0:
                        raise RuntimeError(completed.stderr or completed.stdout or f"command failed: {completed.args}")

                entry["decision"] = decision
                if payload.get("artifact"):
                    entry["path"] = payload["artifact"]
                if payload.get("build_packet"):
                    entry["build_packet"] = payload["build_packet"]
                if payload.get("shaw_run"):
                    entry["shaw_run"] = payload["shaw_run"]
                state.setdefault("posted", {})[intent_id] = entry
                state.setdefault("tokens", {}).pop(token, None)
                state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                await query.answer(text="Approved" if decision == "approved" else "Rejected")
                await query.edit_message_text(
                    text=self.format_message(f"Pending intent {decision} by {query_user_name or 'User'}"),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=None,
                )
            except Exception as exc:
                logger.error("Failed to resolve Subconscious pending-intent callback: %s", exc)
                await query.answer(text="Failed to resolve pending intent.")
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
                        text=self.format_message(f"{label} by {user_display}"),
                        parse_mode=ParseMode.MARKDOWN_V2,
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
                            "text": self.format_message(result_text),
                            "parse_mode": ParseMode.MARKDOWN_V2,
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
                                    reply_to_mode=self._reply_to_mode
                                )
                            )
                        elif thread_id is not None:
                            send_kwargs.update(
                                self._thread_kwargs_for_send(
                                    str(query.message.chat_id),
                                    str(thread_id),
                                    {"thread_id": str(thread_id)},
                                    reply_to_mode=self._reply_to_mode
                                )
                            )
                        await self._send_message_with_thread_fallback(**send_kwargs)
                except Exception as exc:
                    logger.error("[%s] slash-confirm callback failed: %s", self.name, exc, exc_info=True)
            return

        # --- Clarify callbacks (cl:clarify_id:idx | cl:clarify_id:other) ---
        if data.startswith("cl:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                clarify_id = parts[1]
                choice_token = parts[2]

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

                session_key = self._clarify_state.get(clarify_id)
                embedded_supergoal_body = self._extract_supergoal_body_from_callback_text(
                    getattr(query.message, "text", "") if query.message else ""
                )
                if not session_key:
                    # Durable Supergoal fallback: clarify state is in-memory and
                    # can expire or disappear on gateway restart while Telegram
                    # keeps showing old buttons. If the prompt embeds the official
                    # Supergoal body and the user clicked Start/choice 1, synthesize
                    # the same `/goal <body>` command instead of dead-ending.
                    if choice_token == "0" and embedded_supergoal_body:
                        event = self._build_supergoal_callback_event(query, embedded_supergoal_body)
                        if event is not None:
                            await query.answer(text="Starting SuperGoal…")
                            try:
                                await query.edit_message_text(
                                    text=f"❓ {_html.escape(query.message.text or '')}\n\n<b>SuperGoal:</b> starting via /goal",
                                    parse_mode=ParseMode.HTML,
                                    reply_markup=None,
                                )
                            except Exception:
                                pass
                            handler = getattr(self, "_message_handler", None)
                            if callable(handler):
                                maybe_result = handler(event)
                                if asyncio.iscoroutine(maybe_result):
                                    await maybe_result
                            else:
                                await self.handle_message(event)
                            return
                    await query.answer(text="This prompt has already been resolved.")
                    return

                user_display = getattr(query.from_user, "first_name", "User")

                if choice_token == "other":
                    # Flip into text-capture mode and tell the user to type
                    # their answer.  The gateway's text-intercept will pick
                    # up the next message in this session and resolve the
                    # clarify.  Do NOT pop _clarify_state yet — we still
                    # need it if the user is slow to respond and the entry
                    # is cleared by something else.
                    try:
                        from tools.clarify_gateway import mark_awaiting_text
                        marked = mark_awaiting_text(clarify_id)
                    except Exception as exc:
                        logger.warning("[%s] mark_awaiting_text failed: %s", self.name, exc)
                        marked = False
                    if not marked:
                        self._clarify_state.pop(clarify_id, None)
                        await query.answer(text="This prompt expired. Ask me again if needed.", show_alert=True)
                        try:
                            await query.edit_message_text(
                                text=f"❓ {query.message.text or ''}\n\n<i>This prompt expired. Use /retry to ask again.</i>",
                                parse_mode=ParseMode.HTML,
                                reply_markup=None,
                            )
                        except Exception:
                            pass
                        return

                    await query.answer(text="✏️ Type your answer in the chat.")
                    try:
                        await query.edit_message_text(
                            text=f"❓ {query.message.text or ''}\n\n<i>Awaiting typed response from {_html.escape(user_display)}…</i>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=None,
                        )
                    except Exception:
                        pass
                    return

                # Numeric choice → resolve immediately with the chosen text
                try:
                    idx = int(choice_token)
                except (ValueError, TypeError):
                    await query.answer(text="Invalid choice.")
                    return

                # Look up the choice text from the entry registered in the
                # clarify primitive.  Fall back to the index if the entry
                # has been cleaned up (race with timeout / session reset).
                resolved_text: Optional[str] = None
                try:
                    from tools.clarify_gateway import _entries as _clarify_entries  # type: ignore
                    entry = _clarify_entries.get(clarify_id)
                    if entry and entry.choices and 0 <= idx < len(entry.choices):
                        resolved_text = entry.choices[idx]
                except Exception:
                    resolved_text = None

                if resolved_text is None:
                    # Race: entry vanished. Echo the index as a number so
                    # the agent at least sees an intentional response
                    # rather than nothing.
                    resolved_text = f"choice {idx + 1}"

                # Durable Supergoal start: for a prompt that embeds an official
                # `SUPERGOAL_GOAL_BODY`, clicking choice 1 starts the real
                # GoalManager goal immediately via GatewayRunner. We still
                # resolve the clarify so the current agent turn unblocks, but
                # the pre-goal planning answer is not judged as a goal turn.
                if idx == 0 and embedded_supergoal_body:
                    event = self._build_supergoal_callback_event(
                        query,
                        embedded_supergoal_body,
                        session_key=session_key,
                    )
                    if event is not None:
                        started = False
                        try:
                            runner = getattr(getattr(self, "_message_handler", None), "__self__", None)
                            start_goal = getattr(runner, "_start_goal_from_callback_event", None)
                            if callable(start_goal):
                                maybe_started = start_goal(event)
                                if asyncio.iscoroutine(maybe_started):
                                    maybe_started = await maybe_started
                                started = bool(maybe_started)
                        except Exception:
                            logger.debug("Supergoal callback direct /goal start failed", exc_info=True)

                        if not started:
                            logger.warning(
                                "Supergoal callback: direct /goal start was unavailable or failed; "
                                "not queueing slash fallback because queued slash commands are discarded by design"
                            )

                # Pop state and resolve
                self._clarify_state.pop(clarify_id, None)
                try:
                    from tools.clarify_gateway import resolve_gateway_clarify
                    resolved = resolve_gateway_clarify(clarify_id, resolved_text)
                except Exception as exc:
                    logger.error("[%s] resolve_gateway_clarify failed: %s", self.name, exc)
                    resolved = False

                if not resolved:
                    await query.answer(text="This prompt expired. Ask me again if needed.", show_alert=True)
                    try:
                        await query.edit_message_text(
                            text=f"❓ {_html.escape(query.message.text or '')}\n\n<i>This prompt expired. Use /retry to ask again.</i>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=None,
                        )
                    except Exception:
                        pass
                    logger.warning(
                        "Telegram clarify button: resolve_gateway_clarify returned False (id=%s)",
                        clarify_id,
                    )
                    return

                await query.answer(text=f"✓ {resolved_text[:60]}")
                try:
                    await query.edit_message_text(
                        text=f"❓ {_html.escape(query.message.text or '')}\n\n<b>{_html.escape(user_display)}:</b> {_html.escape(resolved_text)}",
                        parse_mode=ParseMode.HTML,
                        reply_markup=None,
                    )
                except Exception:
                    pass

                if resolved:
                    logger.info(
                        "Telegram clarify button resolved (id=%s, choice=%r, user=%s)",
                        clarify_id, resolved_text, user_display,
                    )
                else:
                    logger.warning(
                        "Telegram clarify button: resolve_gateway_clarify returned False (id=%s)",
                        clarify_id,
                    )
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
                text=self.format_message(f"⚕ Update prompt answered: *{label}*"),
                parse_mode=ParseMode.MARKDOWN_V2,
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

    # Maps `gt:<verb>` -> (script-name, extra-args, success-label, is_state).
    # Scripts live in ~/.hermes/scripts/gmail-triage/. `arg` from the callback
    # data is always passed as the first positional arg.
    # is_state=True means the verb is a sticky sender-rule change (mute, trust,
    # vip) that should leave the keyboard tappable for follow-on actions.
    # is_state=False is a per-email one-shot (send, archive, draft, spam) that
    # strips the keyboard on success.
    _GT_VERB_DISPATCH = {
        "send":         ("send-draft.sh",      [],         "✓ sent draft",         False),
        "archive":      ("archive.sh",         [],         "✓ archived",           False),
        "draft":        ("draft-blank.sh",     [],         "✓ drafted reply",      False),
        "spam":         ("spam.sh",            [],         "✓ marked spam",        False),
        "mute":         ("mute-add.sh",        ["email"],  "✓ muted",              True),
        "mute-domain":  ("mute-add.sh",        ["domain"], "✓ muted domain",       True),
        "trust":        ("trusted-ops-add.sh", ["email"],  "✓ trusted",            True),
        "trust-domain": ("trusted-ops-add.sh", ["domain"], "✓ trusted domain",     True),
        "vip":          ("vip-add.sh",         ["email"],  "✓ marked VIP",         True),
        "vip-domain":   ("vip-add.sh",         ["domain"], "✓ marked VIP domain",  True),
    }

    async def _handle_gmail_triage_callback(
        self,
        query,
        data: str,
        *,
        query_chat_id,
        query_chat_type,
        query_thread_id,
        query_user_name,
    ) -> None:
        """Dispatch a gmail-triage inline-button callback (gt:verb:arg)."""
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer(text="Invalid gmail-triage data.")
            return
        verb, arg = parts[1], parts[2]

        caller_id = str(getattr(query.from_user, "id", ""))
        if not self._is_callback_user_authorized(
            caller_id,
            chat_id=query_chat_id,
            chat_type=str(query_chat_type) if query_chat_type is not None else None,
            thread_id=str(query_thread_id) if query_thread_id is not None else None,
            user_name=query_user_name,
        ):
            await query.answer(text="⛔ You are not authorized to act on this email.")
            return

        entry = self._GT_VERB_DISPATCH.get(verb)
        if not entry:
            await query.answer(text=f"Unknown verb: {verb}")
            return
        script_name, extra_args, success_label, is_state_verb = entry

        script_path = _Path.home() / ".hermes" / "scripts" / "gmail-triage" / script_name
        if not script_path.exists():
            await query.answer(text=f"❌ {script_name} missing")
            logger.error("[%s] gmail-triage script missing: %s", self.name, script_path)
            return

        cmd = [str(script_path), arg, *extra_args]
        success = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=60,
            )
            if proc.returncode == 0:
                label = success_label
                success = True
                logger.info(
                    "[%s] gmail-triage callback ok: verb=%s arg=%s",
                    self.name, verb, arg,
                )
            else:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                last_line = stderr_text.splitlines()[-1] if stderr_text else f"exit {proc.returncode}"
                label = f"❌ {verb} failed: {last_line[:80]}"
                logger.error(
                    "[%s] gmail-triage callback failed: verb=%s arg=%s rc=%s stderr=%s",
                    self.name, verb, arg, proc.returncode, stderr_text,
                )
        except asyncio.TimeoutError:
            label = f"❌ {verb} timed out"
            logger.error("[%s] gmail-triage callback timed out: verb=%s arg=%s", self.name, verb, arg)
        except Exception as exc:
            label = f"❌ {verb} error: {exc}"
            logger.error(
                "[%s] gmail-triage callback exception: verb=%s arg=%s err=%s",
                self.name, verb, arg, exc, exc_info=True,
            )

        await query.answer(text=label)
        if not success:
            return

        user_display = getattr(query.from_user, "first_name", "User")
        original_text = (query.message.text or "") if query.message else ""
        appended = f"{original_text}\n— {label} by {user_display}"
        try:
            if is_state_verb:
                # Sticky state change: append confirmation, KEEP keyboard so
                # the user can stack further actions on this email.
                await query.edit_message_text(text=appended)
            else:
                # Per-email one-shot: strip keyboard so the action can't fire twice.
                await query.edit_message_text(text=appended, reply_markup=None)
        except Exception:
            pass

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

    def _telegram_media_too_large_note(self, label: str, file_size: Any, max_bytes: int) -> str:
        limit_mb = max(1, max_bytes // (1024 * 1024))
        try:
            size_mb = int(file_size or 0) / (1024 * 1024)
            size_text = f"{size_mb:.1f} MB"
        except (TypeError, ValueError):
            size_text = "unknown size"
        return (
            f"[Telegram {label} skipped: file size {size_text} exceeds the "
            f"{limit_mb} MB limit. Ask the user to send a shorter voice note "
            "or a smaller audio file.]"
        )

    def _telegram_media_size_allowed(self, source: Any, label: str) -> tuple[bool, Optional[str]]:
        """Validate Telegram media size before downloading into memory."""
        max_bytes = int(getattr(self, "_max_doc_bytes", 20 * 1024 * 1024) or 20 * 1024 * 1024)
        file_size = getattr(source, "file_size", None)
        try:
            size = int(file_size or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            return True, None
        if size <= max_bytes:
            return True, None
        return False, self._telegram_media_too_large_note(label, size, max_bytes)

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
                if ext in {".ogg", ".opus"}:
                    _voice_thread = self._metadata_thread_id(metadata)
                    reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
                    voice_thread_kwargs = self._thread_kwargs_for_send(
                        chat_id,
                        _voice_thread,
                        metadata,
                        reply_to_message_id=reply_to_id,
                        reply_to_mode=self._reply_to_mode
                    )
                    msg = await self._send_with_dm_topic_reply_anchor_retry(
                        self._bot.send_voice,
                        {
                            "chat_id": int(chat_id),
                            "voice": audio_file,
                            "caption": caption[:1024] if caption else None,
                            "reply_to_message_id": reply_to_id,
                            **voice_thread_kwargs,
                            **self._notification_kwargs(metadata),
                        },
                        metadata,
                        reply_to_id,
                        "voice",
                        reset_media=lambda: audio_file.seek(0),
                    )
                elif ext in {".mp3", ".m4a"}:
                    # Telegram's Bot API sendAudio only accepts MP3 / M4A.
                    _audio_thread = self._metadata_thread_id(metadata)
                    reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
                    audio_thread_kwargs = self._thread_kwargs_for_send(
                        chat_id,
                        _audio_thread,
                        metadata,
                        reply_to_message_id=reply_to_id,
                        reply_to_mode=self._reply_to_mode
                    )
                    msg = await self._send_with_dm_topic_reply_anchor_retry(
                        self._bot.send_audio,
                        {
                            "chat_id": int(chat_id),
                            "audio": audio_file,
                            "caption": caption[:1024] if caption else None,
                            "reply_to_message_id": reply_to_id,
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
                reply_to_id = self._reply_to_message_id_for_send(None, metadata, reply_to_mode=self._reply_to_mode)
                thread_kwargs = self._thread_kwargs_for_send(
                    chat_id,
                    _thread,
                    metadata,
                    reply_to_message_id=reply_to_id,
                    reply_to_mode=self._reply_to_mode
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
                        **self._business_connection_kwargs(metadata),
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
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _thread,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=self._reply_to_mode
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
                        **self._business_connection_kwargs(metadata),
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
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _thread,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=self._reply_to_mode
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
                        **thread_kwargs,
                        **self._business_connection_kwargs(metadata),
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "document",
                    reset_media=lambda: f.seek(0),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] Failed to send document: %s", self.name, e, exc_info=True)
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
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
            thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _thread,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=self._reply_to_mode
            )
            with open(video_path, "rb") as f:
                msg = await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_video,
                    {
                        "chat_id": int(chat_id),
                        "video": f,
                        "caption": caption[:1024] if caption else None,
                        "reply_to_message_id": reply_to_id,
                        **thread_kwargs,
                        **self._business_connection_kwargs(metadata),
                        **self._notification_kwargs(metadata),
                    },
                    metadata,
                    reply_to_id,
                    "video",
                    reset_media=lambda: f.seek(0),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] Failed to send video: %s", self.name, e, exc_info=True)
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
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
            photo_thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _photo_thread,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=self._reply_to_mode
            )
            msg = await self._send_with_dm_topic_reply_anchor_retry(
                self._bot.send_photo,
                {
                    "chat_id": int(chat_id),
                    "photo": image_url,
                    "caption": caption[:1024] if caption else None,
                    "reply_to_message_id": reply_to_id,
                    **photo_thread_kwargs,
                    **self._business_connection_kwargs(metadata),
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
                    reply_to_mode=self._reply_to_mode
                )
                msg = await self._send_with_dm_topic_reply_anchor_retry(
                    self._bot.send_photo,
                    {
                        "chat_id": int(chat_id),
                        "photo": image_data,
                        "caption": caption[:1024] if caption else None,
                        "reply_to_message_id": reply_to_id,
                        **upload_thread_kwargs,
                        **self._business_connection_kwargs(metadata),
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
            reply_to_id = self._reply_to_message_id_for_send(reply_to, metadata, reply_to_mode=self._reply_to_mode)
            animation_thread_kwargs = self._thread_kwargs_for_send(
                chat_id,
                _anim_thread,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=self._reply_to_mode
            )
            msg = await self._send_with_dm_topic_reply_anchor_retry(
                self._bot.send_animation,
                {
                    "chat_id": int(chat_id),
                    "animation": animation_url,
                    "caption": caption[:1024] if caption else None,
                    "reply_to_message_id": reply_to_id,
                    **animation_thread_kwargs,
                    **self._business_connection_kwargs(metadata),
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
            _is_dm_topic: bool = False
            message_thread_id: Optional[int] = None
            try:
                _typing_thread = self._metadata_thread_id(metadata)
                _is_dm_topic = bool(metadata and metadata.get("telegram_dm_topic_reply_fallback"))
                message_thread_id = self._message_thread_id_for_typing(_typing_thread)
                await self._bot.send_chat_action(
                    chat_id=int(chat_id),
                    action="typing",
                    message_thread_id=message_thread_id,
                )
            except Exception as e:
                # For DM topic lanes, Telegram may reject message_thread_id.
                # Fall back to sending typing without thread_id so the typing
                # indicator at least appears in the main DM view.
                if _is_dm_topic and message_thread_id is not None:
                    try:
                        await self._bot.send_chat_action(
                            chat_id=int(chat_id),
                            action="typing",
                        )
                        return
                    except Exception:
                        pass
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
                return configured.lower() in {"true", "1", "yes", "on"}
            return bool(configured)
        return os.getenv("TELEGRAM_REQUIRE_MENTION", "false").lower() in {"true", "1", "yes", "on"}

    def _telegram_require_mention_chats(self) -> set[str]:
        raw = self.config.extra.get("require_mention_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_REQUIRE_MENTION_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_require_mention_topics(self) -> set[tuple[str, str]]:
        raw = self.config.extra.get("require_mention_topics")
        if raw is None:
            raw = os.getenv("TELEGRAM_REQUIRE_MENTION_TOPICS", "")
        values = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
        topics: set[tuple[str, str]] = set()
        for value in values:
            text = str(value).strip()
            if not text or ":" not in text:
                continue
            chat_id, thread_id = text.rsplit(":", 1)
            chat_id = chat_id.strip()
            thread_id = thread_id.strip() or self._GENERAL_TOPIC_THREAD_ID
            if chat_id and thread_id:
                topics.add((chat_id, thread_id))
        return topics

    def _telegram_topic_requires_mention(self, chat_id: str, thread_id: Optional[int]) -> bool:
        topic_id = str(thread_id) if thread_id is not None else self._GENERAL_TOPIC_THREAD_ID
        return (str(chat_id), topic_id) in self._telegram_require_mention_topics()

    def _telegram_chat_requires_mention(self, chat_id: str) -> bool:
        if str(chat_id) in self._telegram_require_mention_chats():
            return True
        return self._telegram_require_mention()

    def _telegram_observe_unmentioned_group_messages(self) -> bool:
        """Return whether skipped unmentioned group messages are stored as context.

        When enabled with ``require_mention``, Telegram matches the Yuanbao /
        OpenClaw-style group UX: observe ordinary group chatter in the session
        transcript, but only dispatch the agent when the bot is explicitly
        addressed.
        """
        configured = self.config.extra.get("observe_unmentioned_group_messages")
        if configured is None:
            configured = self.config.extra.get("ingest_unmentioned_group_messages")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in {"true", "1", "yes", "on"}
            return bool(configured)
        return os.getenv("TELEGRAM_OBSERVE_UNMENTIONED_GROUP_MESSAGES", "false").lower() in {"true", "1", "yes", "on"}

    def _telegram_guest_mode(self) -> bool:
        """Return whether non-allowlisted groups may trigger via direct @mention."""
        configured = self.config.extra.get("guest_mode")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in {"true", "1", "yes", "on"}
            return bool(configured)
        return os.getenv("TELEGRAM_GUEST_MODE", "false").lower() in {"true", "1", "yes", "on"}

    def _telegram_exclusive_bot_mentions(self) -> bool:
        """Return whether explicit @...bot mentions exclusively route group messages."""
        configured = self.config.extra.get("exclusive_bot_mentions")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in {"true", "1", "yes", "on"}
            return bool(configured)
        return os.getenv("TELEGRAM_EXCLUSIVE_BOT_MENTIONS", "true").lower() in {"true", "1", "yes", "on"}

    def _telegram_free_response_chats(self) -> set[str]:
        raw = self.config.extra.get("free_response_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_FREE_RESPONSE_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_free_response_topics(self) -> set[tuple[str, str]]:
        raw = self.config.extra.get("free_response_topics")
        if raw is None:
            raw = os.getenv("TELEGRAM_FREE_RESPONSE_TOPICS", "")
        values = raw if isinstance(raw, list) else str(raw).split(",")
        topics: set[tuple[str, str]] = set()
        for value in values:
            text = str(value).strip()
            if not text or ":" not in text:
                continue
            chat_id, thread_id = text.rsplit(":", 1)
            chat_id = chat_id.strip()
            thread_id = thread_id.strip() or self._GENERAL_TOPIC_THREAD_ID
            if chat_id and thread_id:
                topics.add((chat_id, thread_id))
        return topics

    def _telegram_topic_is_free_response(self, chat_id: str, thread_id: Optional[int]) -> bool:
        topic_id = str(thread_id) if thread_id is not None else self._GENERAL_TOPIC_THREAD_ID
        return (str(chat_id), topic_id) in self._telegram_free_response_topics()

    @staticmethod
    def _telegram_chat_id_set(raw: Any) -> set[str]:
        if raw is None:
            return set()
        if isinstance(raw, (list, tuple, set)):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_private_chats(self) -> set[str]:
        raw = self.config.extra.get("private_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_PRIVATE_CHATS", "")
        private = self._telegram_chat_id_set(raw)
        # Backward-compatible migration path: legacy free-response chats are
        # private by policy, unless the new explicit lists are absent entirely.
        private.update(self._telegram_free_response_chats())
        return private

    def _telegram_public_chats(self) -> set[str]:
        raw = self.config.extra.get("public_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_PUBLIC_CHATS", "")
        public = self._telegram_chat_id_set(raw)
        public.update(self._telegram_require_mention_chats())
        return public

    def _telegram_has_explicit_chat_policy(self) -> bool:
        private_raw = self.config.extra.get("private_chats")
        public_raw = self.config.extra.get("public_chats")
        if private_raw is not None or public_raw is not None:
            return bool(self._telegram_chat_id_set(private_raw) or self._telegram_chat_id_set(public_raw))
        return bool(os.getenv("TELEGRAM_PRIVATE_CHATS", "").strip() or os.getenv("TELEGRAM_PUBLIC_CHATS", "").strip())

    def _telegram_public_triggered(self, message: Message, *, guest_mention: Optional[bool] = None) -> bool:
        if self._is_reply_to_bot(message):
            return True
        if guest_mention is True:
            return True
        if guest_mention is None and self._message_mentions_bot(message):
            return True
        return self._message_matches_mention_patterns(message)

    def _telegram_business_config(self) -> Dict[str, Any]:
        config = getattr(self, "config", None)
        extra = getattr(config, "extra", {}) if config is not None else {}
        raw = extra.get("business") if isinstance(extra, dict) else None
        return raw if isinstance(raw, dict) else {}

    def _telegram_business_enabled(self) -> bool:
        value = self._telegram_business_config().get("enabled", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _is_telegram_business_message(self, message: Message) -> bool:
        return bool(getattr(message, "business_connection_id", None))

    def _is_business_bot_dialog_mirror(self, message: Any) -> bool:
        """True for reflected copies of the user's direct dialog with this bot.

        Telegram can surface the same owner-authored DM twice: once as the normal
        bot DM (`chat.id == from_user.id`) and once through the Business inbox
        with `business_connection_id` and `chat.id == this bot's id`. Processing
        both makes Hermes answer twice; the Business reply can render in Telegram
        as if it came from the connected account. Some reflected text updates omit
        `business_connection_id`, but a legitimate user DM can never have a chat id
        equal to the receiving bot's own id. Keep third-party Business concierge
        chats alive, but drop this bot-dialog mirror regardless of that optional
        field.
        """
        bot = getattr(self, "_bot", None)
        bot_id = str(getattr(bot, "id", "") or "")
        chat_id = str(getattr(getattr(message, "chat", None), "id", "") or "")
        if not bot_id or not chat_id:
            return False
        return chat_id == bot_id

    def _telegram_business_free_response_chats(self) -> set[str]:
        raw = self._telegram_business_config().get("free_response_chats", [])
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_business_ignored_user_ids(self) -> set[str]:
        raw = self._telegram_business_config().get("ignore_user_ids")
        if raw is None:
            raw = os.getenv("TELEGRAM_BUSINESS_IGNORE_USER_IDS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_business_auto_transcribe_voice_enabled(self) -> bool:
        """Return whether Telegram Business inbox voice notes are transcribed directly.

        Voice notes normally carry no text, so a `Sigurd` wake word cannot be
        present. When enabled, delegated-inbox voice/audio messages are handled
        as a deterministic STT hook and never enter the LLM transcript.
        """
        value = self._telegram_business_config().get("auto_transcribe_voice", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _should_auto_transcribe_business_voice(self, message: Any) -> bool:
        """True for configured Telegram Business voice/audio notes."""
        if not self._telegram_business_enabled():
            return False
        if not self._telegram_business_auto_transcribe_voice_enabled():
            return False
        if not self._is_telegram_business_message(message):
            return False
        if self._is_business_bot_dialog_mirror(message):
            return False
        if not (getattr(message, "voice", None) or getattr(message, "audio", None)):
            return False
        user = getattr(message, "from_user", None)
        if getattr(user, "is_bot", False):
            return False
        return True

    async def _handle_business_voice_transcription_hook(self, message: Any) -> bool:
        """Transcribe a Telegram Business voice/audio message and echo text back.

        Returns True when this hook claimed the message. The transcript is sent
        directly to the delegated inbox using the same business_connection_id;
        raw private audio/text is not routed through the agent or persisted to
        the LLM session transcript.
        """
        if not self._should_auto_transcribe_business_voice(message):
            return False

        source = getattr(message, "voice", None) or getattr(message, "audio", None)
        if source is None:
            return False

        chat_id = str(getattr(getattr(message, "chat", None), "id", "") or "")
        if not chat_id:
            return False

        allowed, note = self._telegram_media_size_allowed(source, "business voice message")
        business_connection_id = getattr(message, "business_connection_id", None)
        metadata = {"business_connection_id": str(business_connection_id)} if business_connection_id else None
        reply_to = str(getattr(message, "message_id", "") or "") or None
        if not allowed:
            await self.send(
                chat_id,
                note or "Голосовое слишком большое, не смог транскрибировать.",
                reply_to=reply_to,
                metadata=metadata,
            )
            return True

        try:
            file_obj = await source.get_file()
            audio_bytes = await file_obj.download_as_bytearray()
            ext = ".ogg" if getattr(message, "voice", None) else ".mp3"
            cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=ext)

            from tools.transcription_tools import transcribe_audio

            result = await asyncio.to_thread(transcribe_audio, cached_path)
            if not result.get("success"):
                error = str(result.get("error") or "unknown STT error")
                logger.warning("[Telegram] Business voice transcription failed: %s", error)
                await self.send(
                    chat_id,
                    f"🎙️ Не смог транскрибировать голосовое: {error}",
                    reply_to=reply_to,
                    metadata=metadata,
                )
                return True

            transcript = str(result.get("transcript") or "").strip()
            if not transcript:
                await self.send(
                    chat_id,
                    "🎙️ Голосовое распознано как пустое или тишина.",
                    reply_to=reply_to,
                    metadata=metadata,
                )
                return True

            await self.send(
                chat_id,
                f"🎙️ Транскрипт:\n{transcript}",
                reply_to=reply_to,
                metadata=metadata,
            )
            logger.info(
                "[Telegram] Business voice transcribed and echoed: chat=%s message=%s provider=%s",
                chat_id,
                getattr(message, "message_id", None),
                result.get("provider"),
            )
            return True
        except Exception as exc:
            logger.warning("[Telegram] Business voice transcription hook failed: %s", exc, exc_info=True)
            await self.send(
                chat_id,
                f"🎙️ Не смог транскрибировать голосовое: {exc}",
                reply_to=reply_to,
                metadata=metadata,
            )
            return True

    def _message_matches_business_trigger(self, message: Message) -> bool:
        if not self._telegram_business_enabled():
            return False

        user = getattr(message, "from_user", None)
        user_id = str(getattr(user, "id", "") or "")
        if user_id and user_id in self._telegram_business_ignored_user_ids():
            return False

        allowed_owner_ids = self._telegram_chat_id_set(self.config.extra.get("allow_from"))
        env_allowed = os.getenv("TELEGRAM_ALLOWED_USERS", "")
        allowed_owner_ids.update(self._telegram_chat_id_set(env_allowed))
        chat_id = str(getattr(getattr(message, "chat", None), "id", "") or "")
        free_response = self._telegram_business_free_response_chats()

        mentions_this_bot = self._message_mentions_bot(message)
        text = (getattr(message, "text", None) or getattr(message, "caption", None) or "")
        raw_words = self._telegram_business_config().get("trigger_words", [])
        if isinstance(raw_words, str):
            trigger_words = [part.strip() for part in re.split(r"[\n,]+", raw_words) if part.strip()]
        elif isinstance(raw_words, list):
            trigger_words = [str(part).strip() for part in raw_words if str(part).strip()]
        else:
            trigger_words = []

        has_wake_word = False
        for word in trigger_words:
            if re.search(rf"(?i)(?<![\w@]){re.escape(word)}(?![\w@])", text):
                has_wake_word = True
                break

        allow_reply = self._telegram_business_config().get("allow_reply_trigger", False)
        if isinstance(allow_reply, str):
            allow_reply = allow_reply.strip().lower() in {"1", "true", "yes", "on"}
        has_reply_trigger = bool(
            allow_reply
            and (self._is_reply_to_bot(message) or self._is_reply_to_own_outbound_text(message))
        )

        if user_id and user_id in allowed_owner_ids:
            # Telegram Business owner/account echoes are often agent output
            # reflected through the delegated human inbox. Keep plain echoes
            # fail-closed even if the legacy knob ignore_owner_echoes:false is
            # present, but allow explicit owner wake commands: Chip intentionally
            # uses "Sigurd/Сигурд" in third-party Business DMs as a concierge
            # trigger, and that must dispatch via the official Business route.
            return bool(mentions_this_bot or has_wake_word or has_reply_trigger)

        if user_id in free_response or chat_id in free_response:
            return True
        return bool(mentions_this_bot or has_wake_word or has_reply_trigger)


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

    def _telegram_group_allowed_chats(self) -> set[str]:
        """Return Telegram chats authorized at group scope."""
        raw = self.config.extra.get("group_allowed_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_GROUP_ALLOWED_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_observe_allowed_chats(self) -> set[str]:
        """Chats where observed group context may use a shared source.

        ``group_allowed_chats`` is the gateway authorization allowlist for
        user-less group sources.  ``allowed_chats`` remains an optional response
        gate; when set, observed context must satisfy both lists.
        """
        group_allowed = self._telegram_group_allowed_chats()
        if not group_allowed:
            return set()
        response_allowed = self._telegram_allowed_chats()
        if response_allowed:
            return group_allowed & response_allowed
        return group_allowed

    def _telegram_allowed_topics(self) -> set[str]:
        """Return the whitelist of Telegram forum topic IDs this bot handles.

        When non-empty, group/supergroup messages from other topics are
        silently ignored. DMs are never filtered by topic. Telegram may omit
        ``message_thread_id`` for the forum General topic, so ``None`` is
        treated as topic ``1`` for matching purposes.
        """
        raw = self.config.extra.get("allowed_topics")
        if raw is None:
            raw = os.getenv("TELEGRAM_ALLOWED_TOPICS", "")
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
        adapter_name = getattr(getattr(self, "platform", None), "value", "Telegram").title()
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
                adapter_name,
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
                logger.warning("[%s] Invalid Telegram mention pattern %r: %s", adapter_name, pattern, exc)
        if compiled:
            logger.info("[%s] Loaded %d Telegram mention pattern(s)", adapter_name, len(compiled))
        return compiled

    def _is_group_chat(self, message: Message) -> bool:
        chat = getattr(message, "chat", None)
        if not chat:
            return False
        chat_type = str(getattr(chat, "type", "")).split(".")[-1].lower()
        return chat_type in {"group", "supergroup"}

    def _is_reply_to_bot(self, message: Message) -> bool:
        if not self._bot or not getattr(message, "reply_to_message", None):
            return False
        reply_user = getattr(message.reply_to_message, "from_user", None)
        return bool(reply_user and getattr(reply_user, "id", None) == getattr(self._bot, "id", None))

    def _is_reply_to_own_outbound_text(self, message: Any) -> bool:
        """True when the reply target is a message this adapter previously sent.

        Telegram Business messages can render as the connected human account in
        Telethon / reply previews, so ``from_user == bot`` is not reliable for
        Chip's third-party DM concierge flow. Use our send-time message-id/text
        index first, then the recent text echo ledger as a best-effort fallback.
        """
        replied = getattr(message, "reply_to_message", None)
        if not replied:
            return False
        chat_id = str(getattr(getattr(message, "chat", None), "id", "") or "")
        reply_to_id = str(getattr(replied, "message_id", "") or "")
        if not chat_id:
            return False

        reply_to_text = None
        if reply_to_id:
            try:
                from gateway import rich_sent_store

                reply_to_text = rich_sent_store.lookup(chat_id, reply_to_id)
            except Exception:
                reply_to_text = None
            if reply_to_text:
                # The durable sent-message index is authoritative: only adapter
                # outbound sends are recorded under this exact chat/message id.
                # Do not require the short self-echo TTL as a second proof, or a
                # legitimate reply stops triggering after fifteen minutes.
                return True
        if not reply_to_text:
            reply_to_text = self._message_text_with_hidden_links(replied) or None
        if not reply_to_text:
            reply_to_text = self._rich_reply_text_from_message(replied)
        return self._is_recent_outbound_text_quote(chat_id, reply_to_text)

    @staticmethod
    def _extract_bot_mention_usernames(message: Message) -> set[str]:
        """Extract explicit Telegram bot usernames mentioned in text/captions.

        Telegram bot usernames are 5-32 characters and must end in "bot".
        Entity mentions are authoritative. The raw-text fallback is intentionally narrow so
        entity-less mobile/client variants still work without treating email
        addresses or arbitrary substrings as bot mentions.
        """
        mentioned_bot_usernames: set[str] = set()

        def _iter_sources():
            yield getattr(message, "text", None) or "", getattr(message, "entities", None) or []
            yield getattr(message, "caption", None) or "", getattr(message, "caption_entities", None) or []

        for source_text, entities in _iter_sources():
            for entity in entities:
                entity_type = str(getattr(entity, "type", "")).split(".")[-1].lower()
                if entity_type not in {"mention", "bot_command"}:
                    continue
                offset = int(getattr(entity, "offset", -1))
                length = int(getattr(entity, "length", 0))
                if offset < 0 or length <= 0:
                    continue

                entity_text = source_text[offset:offset + length].strip()
                if entity_type == "mention":
                    handle = entity_text.lstrip("@").lower()
                    if re.fullmatch(r"[a-z0-9_]{2,29}bot", handle, re.IGNORECASE):
                        mentioned_bot_usernames.add(handle)
                    continue

                # Telegram emits /cmd@botname as one bot_command entity, not as
                # a separate mention entity. Treat that suffix as an explicit
                # bot address for exclusive multi-bot routing even when the
                # group has require_mention/free-response disabled.
                at_index = entity_text.find("@")
                if at_index < 0:
                    continue
                command_target = entity_text[at_index + 1:].strip().lower()
                if re.fullmatch(r"[a-z0-9_]{2,29}bot", command_target, re.IGNORECASE):
                    mentioned_bot_usernames.add(command_target)

        # Entity-less fallback for older/client-specific updates. If Telegram
        # supplied entities for a source, trust them and do not regex-rescue
        # malformed/URL/code spans that the server did not mark as mentions.
        for raw_text, entities in _iter_sources():
            if not raw_text or entities:
                continue
            for match in re.finditer(r"(?i)(?<![A-Za-z0-9_`/])@([A-Za-z0-9_]{2,29}bot)\b", raw_text):
                mentioned_bot_usernames.add(match.group(1).lower())

        return mentioned_bot_usernames

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
        # a user without a public username). Those entities are authoritative:
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
        if bot_username and re.fullmatch(r"[a-z0-9_]{2,29}bot", bot_username, re.IGNORECASE):
            return bot_username in self._extract_bot_mention_usernames(message)
        return False

    def _explicit_bot_mentions_exclude_self(self, message: Message) -> bool:
        """Return True when explicit bot handles target other bots, not this one.

        Telegram groups can contain several Hermes bot profiles. A message like
        ``@bot3 hi @bot4`` must not wake ``@bot1`` through reply/wake-word
        fallbacks. Treat explicit bot-handle mentions as an exclusive routing
        hint: if at least one @...bot username is present and none matches this
        adapter's own bot username, this adapter should ignore the message.

        MessageEntity values are preferred, but some Telegram clients expose
        selected bot handles as plain text in group messages. The raw-text
        fallback is intentionally limited to usernames ending in "bot", which
        Telegram requires for bot accounts.
        """
        if not self._bot:
            return False

        bot_username = (getattr(self._bot, "username", None) or "").lstrip("@").lower()
        if not bot_username:
            return False

        mentioned_bot_usernames = self._extract_bot_mention_usernames(message)
        return bool(mentioned_bot_usernames) and bot_username not in mentioned_bot_usernames

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

    def _cache_observed_chat_type(self, chat_id: str, chat_type: str) -> None:
        chat_type = str(chat_type or "").strip().lower()
        if not chat_type:
            return
        cache = getattr(self, "_chat_type_cache", None)
        if cache is None:
            cache = {}
            self._chat_type_cache = cache
        cache[str(chat_id)] = chat_type

    def _outbound_chat_type(
        self,
        chat_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
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
        if not self._business_connection_id_from_metadata(metadata):
            return reply_markup
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

    def _should_observe_unmentioned_group_message(self, message: Message) -> bool:
        """Return True when a group message should be stored but not dispatched."""
        if not self._telegram_observe_unmentioned_group_messages():
            return False
        if not self._is_group_chat(message):
            return False

        thread_id = getattr(message, "message_thread_id", None)
        allowed_topics = self._telegram_allowed_topics()
        if allowed_topics:
            topic_id = str(thread_id) if thread_id is not None else self._GENERAL_TOPIC_THREAD_ID
            if topic_id not in allowed_topics:
                return False

        if thread_id is not None:
            try:
                if int(thread_id) in self._telegram_ignored_threads():
                    return False
            except (TypeError, ValueError):
                return False

        chat_id_str = str(getattr(getattr(message, "chat", None), "id", ""))
        if self._telegram_exclusive_bot_mentions() and self._explicit_bot_mentions_exclude_self(message):
            return False

        allowed = self._telegram_observe_allowed_chats()
        # Observed context is shared at chat/topic scope so a later trigger from
        # another user can see it.  Require an explicit chat allowlist; that
        # keeps shared observed history limited to operator-approved groups and
        # lets gateway authorization pass even after the shared session source
        # drops the per-sender user_id.
        if not allowed or chat_id_str not in allowed:
            return False

        # Only observe messages skipped by the require_mention gate.  If the
        # message would be processed normally, let the dispatcher handle it;
        # if require_mention is disabled, every group message is a request.
        topic_requires_mention = self._telegram_topic_requires_mention(chat_id_str, thread_id)
        if not topic_requires_mention:
            if chat_id_str in self._telegram_free_response_chats():
                return False
            if self._telegram_topic_is_free_response(chat_id_str, thread_id):
                return False
            if not self._telegram_chat_requires_mention(chat_id_str):
                return False
        if self._is_reply_to_bot(message):
            return False
        if self._message_mentions_bot(message):
            return False
        if self._message_matches_mention_patterns(message):
            return False
        return True

    def _telegram_group_observe_shared_source(self, source):
        """Return a chat/topic-scoped source for observed Telegram group context."""
        return dataclasses.replace(source, user_id=None, user_name=None, user_id_alt=None)

    def _telegram_group_observe_attributed_text(self, event: MessageEvent) -> str:
        user_id = event.source.user_id or "unknown"
        sender = event.source.user_name or user_id
        return f"[{sender}|{user_id}]\n{event.text or ''}"

    def _telegram_group_observe_channel_prompt(self) -> str:
        username = getattr(getattr(self, "_bot", None), "username", None) or "unknown"
        bot_id = getattr(getattr(self, "_bot", None), "id", None) or "unknown"
        return (
            "You are handling a Telegram group chat message.\n"
            f"- Your identity: user_id={bot_id}, @-mention name in this group=@{username}\n"
            "- observed Telegram group context may be provided in a separate context-only block "
            "before the current message; it is not necessarily addressed to you.\n"
            "- Treat only the current new message as a request explicitly directed at you, "
            "and use observed context only when the current message asks for it."
        )

    def _apply_telegram_group_observe_attribution(self, event: MessageEvent) -> MessageEvent:
        """Align triggered group turns with observed-history attribution."""
        if not self._telegram_observe_unmentioned_group_messages():
            return event
        raw_message = getattr(event, "raw_message", None)
        if not raw_message or not self._is_group_chat(raw_message):
            return event
        chat_id_str = str(getattr(getattr(raw_message, "chat", None), "id", ""))
        allowed = self._telegram_observe_allowed_chats()
        if not allowed or chat_id_str not in allowed:
            return event
        shared_source = self._telegram_group_observe_shared_source(event.source)
        observe_prompt = self._telegram_group_observe_channel_prompt()
        channel_prompt = f"{event.channel_prompt}\n\n{observe_prompt}" if event.channel_prompt else observe_prompt
        if event.message_type == MessageType.COMMAND:
            return dataclasses.replace(
                event,
                source=shared_source,
                channel_prompt=channel_prompt,
            )
        return dataclasses.replace(
            event,
            text=self._telegram_group_observe_attributed_text(event),
            source=shared_source,
            channel_prompt=channel_prompt,
        )

    def _media_message_type(self, msg: Message) -> MessageType:
        """Classify a Telegram media message into a MessageType."""
        if msg.sticker:
            return MessageType.STICKER
        if msg.photo:
            return MessageType.PHOTO
        if msg.video:
            return MessageType.VIDEO
        if msg.audio:
            return MessageType.AUDIO
        if msg.voice:
            return MessageType.VOICE
        return MessageType.DOCUMENT

    async def _cache_observed_media(self, msg: Message, event: MessageEvent) -> None:
        """Cache an unmentioned group attachment and annotate the observed text.

        Passive group traffic, so downloads are bounded by the same
        ``_max_doc_bytes`` limit as the addressed document path. Oversized or
        unsupported attachments are noted in the transcript without downloading.
        """
        from gateway.platforms.base import cache_media_bytes

        source, filename, mime, kind = self._observed_media_source(msg)
        if source is None:
            return

        if getattr(msg, "document", None) is not None:
            ext = os.path.splitext(filename or "")[1].lower()
            supported_exts = (
                set(SUPPORTED_DOCUMENT_TYPES)
                | set(SUPPORTED_IMAGE_DOCUMENT_TYPES)
                | set(SUPPORTED_VIDEO_TYPES)
            )
            if ext not in supported_exts:
                event.text = self._append_observed_note(
                    event.text,
                    "[Observed Telegram attachment: unsupported type, not cached.]",
                )
                return

        max_bytes = getattr(self, "_max_doc_bytes", 20 * 1024 * 1024)
        file_size = getattr(source, "file_size", None)
        try:
            size = int(file_size or 0)
        except (TypeError, ValueError):
            size = 0
        if not (0 < size <= max_bytes):
            limit_mb = max_bytes // (1024 * 1024)
            event.text = self._append_observed_note(
                event.text,
                f"[Observed Telegram attachment too large or unverifiable. Maximum: {limit_mb} MB.]",
            )
            logger.info("[Telegram] Observed group attachment skipped (size=%s)", file_size)
            return

        try:
            file_obj = await source.get_file()
            data = bytes(await file_obj.download_as_bytearray())
            if not filename:
                filename = os.path.basename(getattr(file_obj, "file_path", "") or "")
            cached = cache_media_bytes(data, filename=filename, mime_type=mime, default_kind=kind)
        except Exception as exc:
            logger.warning("[Telegram] Failed to cache observed group media: %s", exc, exc_info=True)
            return

        if cached is None:
            event.text = self._append_observed_note(
                event.text, "[Observed Telegram attachment: unsupported type, not cached.]"
            )
            return

        event.media_urls = [cached.path]
        event.media_types = [cached.media_type]
        if cached.kind == "image":
            event.message_type = MessageType.PHOTO
        elif cached.kind == "video":
            event.message_type = MessageType.VIDEO
        event.text = self._append_observed_note(event.text, cached.context_note())
        logger.info("[Telegram] Cached observed group %s at %s", cached.kind, cached.path)

    async def _cache_replied_media(self, msg: Any, event: MessageEvent) -> None:
        """Cache media from the message this turn replies to, if any."""
        from gateway.platforms.base import cache_media_bytes

        reply_msg = getattr(msg, "reply_to_message", None)
        if reply_msg is None:
            return
        source, filename, mime, kind = self._observed_media_source(reply_msg)
        if source is None:
            return

        max_bytes = getattr(self, "_max_doc_bytes", 20 * 1024 * 1024)
        file_size = getattr(source, "file_size", None)
        try:
            size = int(file_size or 0)
        except (TypeError, ValueError):
            size = 0
        if not (0 < size <= max_bytes):
            return

        try:
            file_obj = await source.get_file()
            data = bytes(await file_obj.download_as_bytearray())
            if not filename:
                filename = os.path.basename(getattr(file_obj, "file_path", "") or "")
            cached = cache_media_bytes(data, filename=filename, mime_type=mime, default_kind=kind)
        except Exception as exc:
            logger.warning("[Telegram] Failed to cache replied-to media: %s", exc, exc_info=True)
            return

        if cached is None:
            return

        event.media_urls.append(cached.path)
        event.media_types.append(cached.media_type)
        if len(event.media_urls) == 1:
            if cached.kind == "image":
                event.message_type = MessageType.PHOTO
            elif cached.kind == "video":
                event.message_type = MessageType.VIDEO
        event.text = self._append_observed_note(
            event.text,
            f"[Replied-to {cached.kind} '{cached.display_name}' saved at: {cached.path}]",
        )
        logger.info("[Telegram] Cached replied-to %s at %s", cached.kind, cached.path)

    def _observed_media_source(self, msg: Message):
        """Return (telegram_file_source, filename, mime, default_kind) or Nones."""
        if msg.photo:
            return msg.photo[-1], "", "", "image"
        if msg.video:
            return msg.video, "", "video/mp4", "video"
        if msg.voice:
            return msg.voice, "voice.ogg", "audio/ogg", "audio"
        if msg.audio:
            return msg.audio, getattr(msg.audio, "file_name", "") or "", "", "audio"
        if msg.document:
            doc = msg.document
            return doc, doc.file_name or "", (doc.mime_type or "").lower(), None
        return None, "", "", None

    @staticmethod
    def _append_observed_note(existing: Optional[str], note: str) -> str:
        if not note:
            return existing or ""
        if not existing:
            return note
        return f"{existing}\n\n{note}"

    def _observe_unmentioned_group_message(
        self,
        message: Message,
        msg_type: MessageType,
        update_id: Optional[int] = None,
        event: Optional[MessageEvent] = None,
    ) -> None:
        """Append skipped group chatter to the target session without dispatching."""
        store = getattr(self, "_session_store", None)
        if not store:
            return
        try:
            event = event or self._build_message_event(message, msg_type, update_id=update_id)
            shared_source = self._telegram_group_observe_shared_source(event.source)
            session_entry = store.get_or_create_session(shared_source)
            entry = {
                "role": "user",
                "content": self._telegram_group_observe_attributed_text(event),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "observed": True,
            }
            if event.message_id:
                entry["message_id"] = str(event.message_id)
            store.append_to_transcript(session_entry.session_id, entry)
            adapter_name = getattr(self, "name", "telegram")
            logger.info(
                "[%s] Telegram group message observed (no bot trigger): chat=%s from=%s",
                adapter_name,
                getattr(getattr(message, "chat", None), "id", "unknown"),
                event.source.user_id or "unknown",
            )
        except Exception as exc:
            adapter_name = getattr(self, "name", "telegram")
            logger.warning("[%s] Failed to observe Telegram group message: %s", adapter_name, exc)

    def _should_process_message(self, message: Message, *, is_command: bool = False) -> bool:
        """Apply Telegram group trigger rules.

        DMs remain unrestricted. Group/supergroup messages are accepted when:
        - the chat passes the ``allowed_chats`` whitelist (when set), or
          ``guest_mode`` is enabled and the bot is explicitly mentioned
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
        thread_id = getattr(message, "message_thread_id", None)

        # Telegram should never dispatch our own bot-authored messages back
        # into the agent. If an adapter/webhook/polling edge surfaces one,
        # drop it before auth/session handling so it cannot appear as Chip.
        if self._is_self_bot_message(message):
            return False

        # Telegram Business may mirror an owner-authored DM back with this bot's
        # own id as the private chat id, sometimes without business_connection_id.
        # Drop it before normal-DM handling; otherwise one user message becomes a
        # second agent turn and Hermes answers twice.
        if self._is_business_bot_dialog_mirror(message):
            return False

        # Check ignored_threads first — applies to both groups and DM topics
        if thread_id is not None:
            try:
                if int(thread_id) in self._telegram_ignored_threads():
                    return False
            except (TypeError, ValueError):
                logger.warning("[%s] Ignoring non-numeric Telegram message_thread_id: %r", self.name, thread_id)

        chat_id_str = str(getattr(getattr(message, "chat", None), "id", ""))
        explicit_policy = self._telegram_has_explicit_chat_policy()
        private_chats = self._telegram_private_chats() if explicit_policy else set()
        public_chats = self._telegram_public_chats() if explicit_policy else set()

        if not self._is_group_chat(message):
            if self._is_telegram_business_message(message):
                if explicit_policy and chat_id_str not in private_chats:
                    user = getattr(message, "from_user", None)
                    user_id = str(getattr(user, "id", "") or "")
                    if user_id not in private_chats:
                        allow_reply = self._telegram_business_config().get(
                            "allow_reply_trigger", False
                        )
                        if isinstance(allow_reply, str):
                            allow_reply = allow_reply.strip().lower() in {
                                "1", "true", "yes", "on"
                            }
                        if not (
                            allow_reply
                            and (
                                self._is_reply_to_bot(message)
                                or self._is_reply_to_own_outbound_text(message)
                            )
                        ):
                            return False
                return self._message_matches_business_trigger(message)
            # Root DM (non-topic): ignore if ignore_root_dm is configured
            if thread_id is None and self.config.extra.get("ignore_root_dm", False):
                if not is_command and chat_id_str in self._dm_topic_chat_ids:
                    return False
            if explicit_policy:
                user = getattr(message, "from_user", None)
                user_id = str(getattr(user, "id", "") or "")
                return chat_id_str in private_chats or user_id in private_chats
            return True


        allowed_topics = self._telegram_allowed_topics()
        if allowed_topics:
            topic_id = str(thread_id) if thread_id is not None else self._GENERAL_TOPIC_THREAD_ID
            if topic_id not in allowed_topics:
                return False

        if self._telegram_exclusive_bot_mentions() and self._explicit_bot_mentions_exclude_self(message):
            return False

        # Resolve guest-mode mention bypass once so _message_mentions_bot
        # is not called redundantly in the normal flow below.
        guest_mention = self._is_guest_mention(message)

        if explicit_policy:
            if self._telegram_topic_requires_mention(chat_id_str, thread_id):
                return self._telegram_public_triggered(
                    message,
                    guest_mention=guest_mention if self._telegram_guest_mode() else None,
                )
            if self._telegram_topic_is_free_response(chat_id_str, thread_id):
                return True
            if chat_id_str in private_chats:
                return True
            if chat_id_str in public_chats:
                return self._telegram_public_triggered(
                    message,
                    guest_mention=guest_mention if self._telegram_guest_mode() else None,
                )
            return False

        # allowed_chats check (whitelist). When set, group messages from chats
        # outside the whitelist are ignored unless guest_mode permits this
        # exact message as an explicit direct mention. DMs are excluded above.
        allowed = self._telegram_allowed_chats()
        if allowed and chat_id_str not in allowed:
            return guest_mention

        if guest_mention:
            return True
        if self._telegram_topic_requires_mention(chat_id_str, thread_id):
            return self._telegram_public_triggered(
                message,
                guest_mention=guest_mention if self._telegram_guest_mode() else None,
            )
        if self._telegram_topic_is_free_response(chat_id_str, thread_id):
            return True
        if chat_id_str in self._telegram_free_response_chats():
            return True
        if not self._telegram_chat_requires_mention(chat_id_str):
            return True
        return self._telegram_public_triggered(
            message,
            guest_mention=guest_mention if self._telegram_guest_mode() else None,
        )

    async def _ensure_forum_commands(self, message) -> None:
        """Lazy-register bot commands for forum supergroups.

        Forum topics don't inherit AllGroupChats scope — Telegram resolves
        via BotCommandScopeChat(chat_id).  Register on first message so the
        command menu works in topic views.
        """
        async with self._forum_lock:
            try:
                chat = getattr(message, "chat", None)
                if not chat or not getattr(chat, "is_forum", False):
                    return
                chat_id = int(chat.id)
                if chat_id in self._forum_command_registered:
                    return
                from telegram import BotCommand, BotCommandScopeChat
                from hermes_cli.commands import telegram_menu_commands
                menu_commands, _ = telegram_menu_commands(max_commands=MAX_COMMANDS_PER_SCOPE)
                bot_commands = [BotCommand(name, desc) for name, desc in menu_commands]
                await self._bot.set_my_commands(bot_commands, scope=BotCommandScopeChat(chat_id=chat_id))
                self._forum_command_registered.add(chat_id)
                logger.info("[%s] Lazy-registered %d commands for forum chat %s", self.name, len(bot_commands), chat_id)
            except Exception as e:
                logger.warning("[%s] Forum command lazy-registration failed: %s", self.name, e)

    def _effective_update_message(self, update: Update) -> Optional[Message]:
        """Return the message-like payload for normal messages and channel posts.

        Telegram exposes channel broadcasts as ``update.channel_post`` rather
        than ``update.message``.  MessageHandler filters can still dispatch
        those updates, so handlers must use ``effective_message`` to avoid
        consuming channel posts without ever building a gateway event.
        """
        return getattr(update, "effective_message", None) or getattr(update, "message", None)

    async def _handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages.

        Telegram clients split long messages into multiple updates.  Buffer
        rapid successive text messages from the same user/chat and aggregate
        them into a single MessageEvent before dispatching.
        """
        msg = self._effective_update_message(update)
        if not msg or not msg.text:
            return
        if self._is_native_voice_transcript_followup(msg):
            chat_id = getattr(getattr(msg, "chat", None), "id", "unknown")
            message_id = getattr(msg, "message_id", "unknown")
            deleted = False
            try:
                if chat_id != "unknown" and message_id != "unknown":
                    deleted = await self.delete_message(str(chat_id), str(message_id))
            except Exception as exc:
                logger.debug("[%s] Native Telegram voice transcript delete failed: %s", self.name, exc)
            logger.info(
                "[%s] Ignoring native Telegram voice transcript follow-up: chat=%s msg=%s deleted=%s",
                self.name,
                chat_id,
                message_id,
                deleted,
            )
            return
        if self._is_recent_outbound_text_echo(msg):
            logger.info(
                "[%s] Ignoring reflected outbound Telegram text echo: chat=%s msg=%s",
                self.name,
                getattr(getattr(msg, "chat", None), "id", "unknown"),
                getattr(msg, "message_id", "unknown"),
            )
            return
        if not self._should_process_message(msg):
            if self._should_observe_unmentioned_group_message(msg):
                self._observe_unmentioned_group_message(msg, MessageType.TEXT, update_id=update.update_id)
            return
        await self._ensure_forum_commands(update.message)

        event = self._build_message_event(msg, MessageType.TEXT, update_id=update.update_id)
        stripped_followup = self._strip_recent_outbound_text_echo_prefix(msg)
        if stripped_followup:
            logger.info(
                "[%s] Stripped reflected outbound Telegram prefix; preserving user suffix: chat=%s msg=%s",
                self.name,
                getattr(getattr(msg, "chat", None), "id", "unknown"),
                getattr(msg, "message_id", "unknown"),
            )
            event.text = stripped_followup
        event.text = self._clean_bot_trigger_text(event.text)
        await self._hydrate_reply_to_document_text(event, msg)
        if not event.media_urls:
            await self._cache_replied_media(msg, event)
        await self._recover_transcribe_route_tme_link_via_telegram_chip(event, msg.chat.id)
        event = self._apply_telegram_group_observe_attribution(event)
        self._enqueue_text_event(event)

    @staticmethod
    def _voice_transcript_followup_text(text: str) -> bool:
        body = str(text or "").strip()
        if not body:
            return False
        # Telegram's native transcript UI is localized; the Russian client
        # currently emits this heading when it is surfaced as text. Keep this
        # narrow so ordinary user text still reaches Hermes.
        return bool(re.match(r"^🎙\s*Расшифровка голосового\s*:\s*\S", body, re.IGNORECASE | re.DOTALL))

    @staticmethod
    def _voice_transcript_key_from_message(msg: Any) -> tuple[str, str]:
        chat_id = str(getattr(getattr(msg, "chat", None), "id", "") or "")
        user_id = str(getattr(getattr(msg, "from_user", None), "id", "") or "")
        return chat_id, user_id

    def _remember_recent_voice_message(self, msg: Any) -> None:
        recent = getattr(self, "_recent_voice_message_keys", None)
        if recent is None:
            recent = {}
            self._recent_voice_message_keys = recent
        now = time.monotonic()
        recent[self._voice_transcript_key_from_message(msg)] = now
        if len(recent) > 256:
            cutoff = now - 60.0
            for key, ts in list(recent.items()):
                if ts < cutoff:
                    recent.pop(key, None)

    def _is_native_voice_transcript_followup(self, msg: Any) -> bool:
        if not self._voice_transcript_followup_text(getattr(msg, "text", "") or ""):
            return False
        recent = getattr(self, "_recent_voice_message_keys", None) or {}
        ts = recent.get(self._voice_transcript_key_from_message(msg))
        return bool(ts and (time.monotonic() - ts) <= 10.0)

    async def _handle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming command messages."""
        msg = self._effective_update_message(update)
        if not msg or not msg.text:
            return
        if not self._should_process_message(msg, is_command=True):
            return
        await self._ensure_forum_commands(msg)

        event = self._build_message_event(msg, MessageType.COMMAND, update_id=update.update_id)
        event.text = self._clean_bot_trigger_text(event.text)
        await self._hydrate_reply_to_document_text(event, msg)
        if not event.media_urls:
            await self._cache_replied_media(msg, event)
        event = self._apply_telegram_group_observe_attribution(event)
        event = self._prepare_recent_visible_context(event)
        await self.handle_message(event)

    async def _handle_location_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming location/venue pin messages."""
        msg = self._effective_update_message(update)
        if not msg:
            return
        if not self._should_process_message(msg):
            if self._should_observe_unmentioned_group_message(msg):
                self._observe_unmentioned_group_message(msg, MessageType.LOCATION, update_id=update.update_id)
            return

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
        event = self._apply_telegram_group_observe_attribution(event)
        event = self._prepare_recent_visible_context(event)
        await self.handle_message(event)

    # ------------------------------------------------------------------
    # Text message aggregation (handles Telegram client-side splits)
    # ------------------------------------------------------------------

    def _text_batch_key(self, event: MessageEvent) -> str:
        """Session-scoped key for text message batching.

        Applies the installed topic-recovery hook first so DM-topic batches
        coalesce on (and dispatch to) the recovered lane rather than the
        raw inbound ``message_thread_id`` Telegram may have attached.
        """
        from gateway.session import build_session_key
        self._apply_topic_recovery(event)
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
            # Adaptive delay tiers:
            #  - last chunk ≥ _SPLIT_THRESHOLD: a continuation is almost
            #    certain → wait the longer split delay.
            #  - total accumulated text ≤ _TEXT_BATCH_FAST_LEN (~320 cp):
            #    short message → cap delay at _TEXT_BATCH_FAST_DELAY_S
            #    so the agent sees the text near-instantly.
            #  - total ≤ _TEXT_BATCH_SHORT_LEN (~1024 cp):
            #    medium → cap at _TEXT_BATCH_SHORT_DELAY_S.
            #  - otherwise: use the configured cap.
            # Tiers compose with operator overrides via the env-var-driven
            # ``_text_batch_delay_seconds`` (e.g. an operator who sets the
            # cap below 0.18s gets that lower number on every tier).
            pending = self._pending_text_batches.get(key)
            last_len = getattr(pending, "_last_chunk_len", 0) if pending else 0
            total_len = len(getattr(pending, "text", "") or "") if pending else 0
            if last_len >= self._SPLIT_THRESHOLD:
                delay = self._text_batch_split_delay_seconds
            elif total_len <= self._TEXT_BATCH_FAST_LEN:
                delay = min(self._text_batch_delay_seconds, self._TEXT_BATCH_FAST_DELAY_S)
            elif total_len <= self._TEXT_BATCH_SHORT_LEN:
                delay = min(self._text_batch_delay_seconds, self._TEXT_BATCH_SHORT_DELAY_S)
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
            event = self._prepare_recent_visible_context(event)
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
            event = self._prepare_recent_visible_context(event)
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
        if await self._handle_business_voice_transcription_hook(update.message):
            return
        if not self._should_process_message(update.message):
            if self._should_observe_unmentioned_group_message(update.message):
                _m = update.message
                _observe_type = self._media_message_type(_m)
                _event = self._build_message_event(_m, _observe_type, update_id=update.update_id)
                if _m.caption:
                    _event.text = self._clean_bot_trigger_text(_m.caption)
                await self._cache_observed_media(_m, _event)
                self._observe_unmentioned_group_message(
                    _m, _event.message_type, update_id=update.update_id, event=_event
                )
            return

        msg = update.message

        msg_type = self._media_message_type(msg)

        event = self._build_message_event(msg, msg_type, update_id=update.update_id)

        # Add caption as text
        if msg.caption:
            event.text = self._clean_bot_trigger_text(msg.caption)

        # Handle stickers: describe via vision tool with caching
        if msg.sticker:
            await self._handle_sticker(msg, event)
            event = self._apply_telegram_group_observe_attribution(event)
            event = self._prepare_recent_visible_context(event)
            await self.handle_message(event)
            return

        # Apply observe attribution after caption is set; sticker is handled above
        # because _handle_sticker overwrites event.text with its vision description.
        event = self._apply_telegram_group_observe_attribution(event)

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
                allowed, note = self._telegram_media_size_allowed(msg.voice, "voice message")
                if not allowed:
                    recovered = await self._recover_transcribe_route_media_via_telegram_chip(
                        msg,
                        event,
                        ext=".ogg",
                        mime_type="audio/ogg",
                        message_type=MessageType.VOICE,
                        reason=note or "voice message exceeds Bot API download limit",
                    )
                    if recovered:
                        event = self._prepare_recent_visible_context(event)
                        await self.handle_message(event)
                        return
                    event.text = self._append_observed_note(event.text, note or "")
                    logger.info("[Telegram] Skipped oversized user voice (size=%s)", getattr(msg.voice, "file_size", None))
                    await self.handle_message(event)
                    return
                file_obj = await msg.voice.get_file()
                audio_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=".ogg")
                event.media_urls = [cached_path]
                event.media_types = ["audio/ogg"]
                self._remember_recent_voice_message(msg)
                logger.info("[Telegram] Cached user voice at %s", cached_path)
            except Exception as e:
                logger.warning("[Telegram] Failed to cache voice: %s", e, exc_info=True)
        elif msg.audio:
            try:
                allowed, note = self._telegram_media_size_allowed(msg.audio, "audio file")
                if not allowed:
                    recovered = await self._recover_transcribe_route_media_via_telegram_chip(
                        msg,
                        event,
                        ext=".mp3",
                        mime_type="audio/mpeg",
                        message_type=MessageType.AUDIO,
                        reason=note or "audio file exceeds Bot API download limit",
                    )
                    if recovered:
                        event = self._prepare_recent_visible_context(event)
                        await self.handle_message(event)
                        return
                    event.text = self._append_observed_note(event.text, note or "")
                    logger.info("[Telegram] Skipped oversized user audio (size=%s)", getattr(msg.audio, "file_size", None))
                    await self.handle_message(event)
                    return
                file_obj = await msg.audio.get_file()
                audio_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=".mp3")
                event.media_urls = [cached_path]
                event.media_types = ["audio/mp3"]
                if not event.text and self._is_transcribe_route_chat(msg.chat.id):
                    event.text = (
                        "[Telegram transcribe-route media recovered via telegram-chip. "
                        "Transcribe this file now and return the transcript first.]"
                    )
                logger.info("[Telegram] Cached user audio at %s", cached_path)
            except Exception as e:
                logger.warning("[Telegram] Failed to cache audio: %s", e, exc_info=True)
                await self._recover_transcribe_route_media_via_telegram_chip(
                    msg,
                    event,
                    ext=".mp3",
                    mime_type="audio/mpeg",
                    message_type=MessageType.AUDIO,
                    reason=str(e),
                )

        elif msg.video:
            try:
                allowed, note = self._telegram_media_size_allowed(msg.video, "video file")
                if not allowed:
                    event.text = self._append_observed_note(event.text, note or "")
                    logger.info("[Telegram] Skipped oversized user video (size=%s)", getattr(msg.video, "file_size", None))
                    await self.handle_message(event)
                    return
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
                await self._recover_transcribe_route_media_via_telegram_chip(
                    msg,
                    event,
                    ext=".mp4",
                    mime_type="video/mp4",
                    message_type=MessageType.VIDEO,
                    reason=str(e),
                )

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
                if not doc.file_size or doc.file_size > self._max_doc_bytes:
                    recovered = False
                    doc_kind_mime = doc_mime or SUPPORTED_DOCUMENT_TYPES.get(ext, "application/octet-stream")
                    doc_message_type = MessageType.DOCUMENT
                    if doc_kind_mime.startswith("audio/") or ext in {".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac", ".opus"}:
                        doc_message_type = MessageType.AUDIO
                        doc_kind_mime = doc_kind_mime if doc_kind_mime.startswith("audio/") else "audio/mpeg"
                    elif doc_kind_mime.startswith("video/") or ext in SUPPORTED_VIDEO_TYPES:
                        doc_message_type = MessageType.VIDEO
                        doc_kind_mime = doc_kind_mime if doc_kind_mime.startswith("video/") else SUPPORTED_VIDEO_TYPES.get(ext, "video/mp4")
                    if ext or doc_kind_mime.startswith(("audio/", "video/")):
                        recovered = await self._recover_transcribe_route_media_via_telegram_chip(
                            msg,
                            event,
                            ext=ext or (".mp4" if doc_message_type == MessageType.VIDEO else ".mp3" if doc_message_type == MessageType.AUDIO else ".bin"),
                            mime_type=doc_kind_mime,
                            message_type=doc_message_type,
                            reason=f"document too large or size unknown: {doc.file_size}",
                        )
                    if recovered:
                        event = self._prepare_recent_visible_context(event)
                        await self.handle_message(event)
                        return
                    limit_mb = self._max_doc_bytes // (1024 * 1024)
                    event.text = (
                        "The document is too large or its size could not be verified. "
                        f"Maximum: {limit_mb} MB."
                    )
                    logger.info("[Telegram] Document too large: %s bytes", doc.file_size)
                    event = self._prepare_recent_visible_context(event)
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
                        event = self._prepare_recent_visible_context(event)
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

                if not ext and doc.mime_type:
                    # SUPPORTED_IMAGE_DOCUMENT_TYPES has duplicate values (.jpg + .jpeg
                    # both map to image/jpeg); keep the first ext we encounter.
                    image_mime_to_ext: dict[str, str] = {}
                    for _ext, _mime in SUPPORTED_IMAGE_DOCUMENT_TYPES.items():
                        image_mime_to_ext.setdefault(_mime, _ext)
                    ext = image_mime_to_ext.get(doc.mime_type, "")

                if ext in SUPPORTED_VIDEO_TYPES:
                    file_obj = await doc.get_file()
                    video_bytes = await file_obj.download_as_bytearray()
                    cached_path = cache_video_from_bytes(bytes(video_bytes), ext=ext)
                    event.media_urls = [cached_path]
                    event.media_types = [SUPPORTED_VIDEO_TYPES[ext]]
                    event.message_type = MessageType.VIDEO
                    logger.info("[Telegram] Cached user video document at %s", cached_path)
                    event = self._prepare_recent_visible_context(event)
                    await self.handle_message(event)
                    return

                # NOTE: image-document handling is performed earlier in this
                # function (ext in _TELEGRAM_IMAGE_EXTENSIONS or image/* mime),
                # which returns before reaching here.  Any subsequent
                # ext-in-SUPPORTED_IMAGE_DOCUMENT_TYPES branch would be dead
                # code — the extension sets are identical.

                # Check if supported
                if ext not in SUPPORTED_DOCUMENT_TYPES:
                    supported_list = ", ".join(sorted(SUPPORTED_DOCUMENT_TYPES.keys()))
                    event.text = (
                        f"Unsupported document type '{ext or 'unknown'}'. "
                        f"Supported types: {supported_list}"
                    )
                    logger.info("[Telegram] Unsupported document type: %s", ext or "unknown")
                    event = self._prepare_recent_visible_context(event)
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

        event = self._prepare_recent_visible_context(event)
        await self.handle_message(event)

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
                event = self._prepare_recent_visible_context(event)
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
                # Clear both config and precomputed set when all topics are removed
                self._dm_topics_config = []
                self._dm_topic_chat_ids = set()
                return

            # Update in-memory config and cache any new thread_ids
            self._dm_topics_config = dm_topics
            # Rebuild the chat_id set for O(1) root-DM ignore lookup
            self._dm_topic_chat_ids = {
                str(chat_entry["chat_id"]) for chat_entry in dm_topics if "chat_id" in chat_entry
            }
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

    @staticmethod
    def _inject_telegram_text_links(text: str, entities: Optional[List[Any]]) -> str:
        """Append hidden Telegram text-link URLs to text sent to the agent.

        Telegram can render a label like ``YouTube`` while the actual URL lives
        only in ``MessageEntityTextUrl`` / Bot API ``text_link`` metadata. If we
        pass only ``message.text`` to the LLM, the agent sees a label with no URL
        and asks the user to resend a link that was already present. Keep the
        visible text intact and add a compact link appendix.
        """
        if not text or not entities:
            return text

        links: List[tuple[str, str]] = []
        seen: Set[tuple[str, str]] = set()
        for entity in entities:
            url = getattr(entity, "url", None)
            if not url or url in text:
                continue
            entity_type = str(getattr(entity, "type", "")).lower()
            if not any(marker in entity_type for marker in ("text_link", "texturl")):
                continue
            try:
                offset = int(getattr(entity, "offset", 0) or 0)
                length = int(getattr(entity, "length", 0) or 0)
            except (TypeError, ValueError):
                offset = 0
                length = 0
            label = text[offset: offset + length].strip() if length > 0 else "link"
            label = label or "link"
            key = (label, url)
            if key in seen:
                continue
            seen.add(key)
            links.append(key)

        if not links:
            return text
        appendix = "\n".join(f"- {label}: {url}" for label, url in links)
        return f"{text.rstrip()}\n\n[Telegram links]\n{appendix}"

    @classmethod
    def _message_text_with_hidden_links(cls, message: Any) -> str:
        """Return message text/caption with hidden text-link entity URLs exposed."""
        text = getattr(message, "text", None)
        if text:
            return cls._inject_telegram_text_links(text, getattr(message, "entities", None))
        caption = getattr(message, "caption", None)
        if caption:
            return cls._inject_telegram_text_links(caption, getattr(message, "caption_entities", None))
        return ""

    async def _hydrate_reply_to_document_text(self, event: MessageEvent, message: Any) -> None:
        """Cache replied document media and expose safe text content.

        This preserves the existing "reply to a document and run /summ" path
        while also making bare `/goal` replies to SuperGoal `.md` files work:
        the command text stays `/goal`, and the replied document body is added
        to `reply_to_text` for GoalManager extraction.
        """
        replied = getattr(message, "reply_to_message", None)
        doc = getattr(replied, "document", None) if replied is not None else None
        if doc is None:
            return

        filename = getattr(doc, "file_name", "") or ""
        mime_type = (getattr(doc, "mime_type", "") or "").lower()
        ext = os.path.splitext(filename)[1].lower()
        if not ext and mime_type:
            mime_to_ext = {v: k for k, v in SUPPORTED_DOCUMENT_TYPES.items()}
            ext = mime_to_ext.get(mime_type, "")

        size = None
        try:
            size = getattr(doc, "file_size", None)
            size_int = int(size) if size is not None else None
        except (TypeError, ValueError):
            size_int = None
        if size_int is None or size_int > self._max_doc_bytes:
            logger.info(
                "[Telegram] Skipping replied document hydration: %s bytes for %s",
                size,
                filename or mime_type or "unknown",
            )
            return

        try:
            file_obj = await doc.get_file()
            raw_bytes = bytes(await file_obj.download_as_bytearray())
        except Exception as exc:
            logger.warning("[Telegram] Failed to download replied document: %s", exc, exc_info=True)
            return

        if len(raw_bytes) > self._max_doc_bytes:
            logger.info(
                "[Telegram] Skipping replied document hydration after download: %s bytes for %s",
                len(raw_bytes),
                filename or mime_type or "unknown",
            )
            return

        cached = cache_media_bytes(raw_bytes, filename=filename, mime_type=mime_type, default_kind="document")
        if cached is not None:
            event.media_urls = list(event.media_urls or []) + [cached.path]
            event.media_types = list(event.media_types or []) + [cached.media_type]
            if cached.kind == "image":
                event.message_type = MessageType.PHOTO
            elif cached.kind == "video":
                event.message_type = MessageType.VIDEO
            elif cached.kind == "audio":
                event.message_type = MessageType.AUDIO
            else:
                event.message_type = MessageType.DOCUMENT

        is_text_document = ext in TEXT_DOCUMENT_EXTENSIONS or mime_type.startswith("text/")
        if (
            not is_text_document
            or size_int is None
            or size_int > _SUPERGOAL_REPLY_DOCUMENT_MAX_BYTES
            or len(raw_bytes) > _SUPERGOAL_REPLY_DOCUMENT_MAX_BYTES
        ):
            return

        try:
            text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            logger.warning("[Telegram] Replied text document is not valid UTF-8: %s", filename or mime_type)
            return

        display_name = re.sub(r'[^\w.\- ]', '_', filename or f"document{ext or '.txt'}")
        doc_text = f"[Content of replied-to {display_name}]:\n{text}"

        # Bare `/goal` replies must stay command-only. GoalManager reads the
        # replied document from `reply_to_text`; appending the document body to
        # `event.text` makes Bot API command parsing treat the whole file as
        # `/goal <args>` and bypasses SuperGoal body extraction.
        command_text = (event.text or "").strip()
        is_bare_goal_reply = bool(re.fullmatch(r"/goal(?:@[A-Za-z0-9_]+)?", command_text))
        if not is_bare_goal_reply:
            if event.text:
                event.text = f"{event.text}\n\n{doc_text}"
            else:
                event.text = doc_text
        if event.reply_to_text:
            event.reply_to_text = f"{event.reply_to_text}\n\n{doc_text}"
        else:
            event.reply_to_text = doc_text

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

        # Determine chat type.  Normalize through ``str`` so tests/mocks and
        # python-telegram-bot enum values both work (``ChatType.CHANNEL`` is
        # string-like, but mocks often provide plain strings).
        telegram_chat_type = str(getattr(chat, "type", "")).split(".")[-1].lower()
        chat_id_text = str(getattr(chat, "id", "") or "")
        chat_type = "dm"
        if telegram_chat_type in {"group", "supergroup"} or (
            chat_id_text.startswith("-") and telegram_chat_type not in {"channel", "private"}
        ):
            chat_type = "group"
        elif telegram_chat_type == "channel":
            chat_type = "channel"

        # Resolve Telegram topic name and skill binding.
        # Only preserve message_thread_id when Telegram marks the message as
        # a real topic/forum message. Telegram can also populate
        # message_thread_id for ordinary reply UI anchors; treating those as
        # durable session threads fragments workflows such as CAPTCHA/login
        # handoffs where the user later replies "done" in the same group.
        # Private chats have the same pitfall: only real DM topic messages
        # (is_topic_message=True) should keep the thread id, otherwise sends
        # can hit Telegram's 'Message thread not found' error (#3206).
        thread_id_raw = message.message_thread_id
        is_topic_message = bool(getattr(message, "is_topic_message", False))
        is_forum_group = getattr(chat, "is_forum", False) is True
        thread_id_str = None
        if thread_id_raw is not None:
            if chat_type == "group" and (is_topic_message or is_forum_group):
                thread_id_str = str(thread_id_raw)
            elif chat_type == "dm" and is_topic_message:
                thread_id_str = str(thread_id_raw)
        # For forum groups without an explicit topic, default to the
        # General-topic id so the gateway routes back to the General topic
        # rather than dropping into the bot's main channel (#22423).
        if chat_type == "group" and thread_id_str is None and is_forum_group:
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
            # Accept both the canonical list shape and the legacy/operator-edited
            # mapping shape while ignoring malformed entries fail-closed.
            group_topics_config = self.config.extra.get("group_topics", [])
            if isinstance(group_topics_config, dict):
                group_topics_iter = [
                    {"chat_id": cfg_chat_id, "topics": topics}
                    for cfg_chat_id, topics in group_topics_config.items()
                ]
            elif isinstance(group_topics_config, list):
                group_topics_iter = [
                    entry for entry in group_topics_config if isinstance(entry, dict)
                ]
            else:
                group_topics_iter = []
            for chat_entry in group_topics_iter:
                if str(chat_entry.get("chat_id", "")) == str(chat.id):
                    topics = chat_entry.get("topics", [])
                    if not isinstance(topics, list):
                        topics = []
                    for topic in topics:
                        if not isinstance(topic, dict):
                            continue
                        tid = topic.get("thread_id")
                        if tid is not None and str(tid) == thread_id_str:
                            chat_topic = topic.get("name")
                            topic_skill = topic.get("skill")
                            break
                    break

        # Build source
        business_connection_id = getattr(message, "business_connection_id", None)
        if not business_connection_id and getattr(message, "reply_to_message", None):
            business_connection_id = getattr(
                message.reply_to_message, "business_connection_id", None
            )
        if business_connection_id:
            self._remember_business_connection_id(chat_id_text, business_connection_id)
        elif self._is_reply_to_own_outbound_text(message):
            # Owner-authored replies in a delegated Business inbox can arrive as
            # ordinary ``message`` updates with no connection id. Recover only
            # for replies to an indexed assistant message in this exact chat.
            business_connection_id = self._known_business_connection_id(chat_id_text)
        source = self.build_source(
            chat_id=str(chat.id),
            chat_name=getattr(chat, "title", None) or (getattr(chat, "full_name", None) if hasattr(chat, "full_name") else None),
            chat_type=chat_type,
            user_id=(
                str(user.id)
                if user
                else (str(chat.id) if chat_type in {"dm", "channel"} else None)
            ),
            user_name=(
                user.full_name
                if user
                else (
                    getattr(chat, "full_name", None)
                    if hasattr(chat, "full_name") and chat_type == "dm"
                    else (getattr(chat, "title", None) if chat_type == "channel" else None)
                )
            ),
            thread_id=thread_id_str,
            chat_topic=chat_topic,
            message_id=str(message.message_id),
        )
        if business_connection_id:
            source.business_connection_id = str(business_connection_id)
            source.external_safe_mode = True
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
        reply_to_author_id = None
        reply_to_author_name = None
        reply_to_is_own = False
        if message.reply_to_message:
            replied = message.reply_to_message
            reply_to_id = str(replied.message_id)
            replied_user = getattr(replied, "from_user", None)
            if replied_user is not None:
                reply_to_author_id = str(getattr(replied_user, "id", "") or "") or None
                reply_to_author_name = getattr(replied_user, "full_name", None) or getattr(replied_user, "username", None)
                reply_to_is_own = bool(
                    self._bot
                    and getattr(replied_user, "id", None) == getattr(self._bot, "id", None)
                )
            quote = getattr(message, "quote", None)
            quote_text = getattr(quote, "text", None) if quote is not None else None
            if quote_text:
                reply_to_text = quote_text
            else:
                reply_to_text = self._message_text_with_hidden_links(replied) or None
                if not reply_to_text:
                    reply_to_text = self._rich_reply_text_from_message(replied)
                if not reply_to_text:
                    try:
                        from gateway import rich_sent_store

                        reply_to_text = rich_sent_store.lookup(str(chat.id), reply_to_id)
                    except Exception:
                        reply_to_text = None
            if reply_to_text and not reply_to_is_own:
                reply_to_is_own = self._is_recent_outbound_text_quote(str(chat.id), reply_to_text)

        # Per-channel/topic ephemeral prompt
        from gateway.platforms.base import resolve_channel_prompt
        _chat_id_str = str(chat.id)
        _channel_prompt = resolve_channel_prompt(
            self.config.extra,
            thread_id_str or _chat_id_str,
            _chat_id_str if thread_id_str else None,
        )

        return MessageEvent(
            text=self._message_text_with_hidden_links(message),
            message_type=msg_type,
            source=source,
            raw_message=message,
            message_id=str(message.message_id),
            platform_update_id=update_id,
            reply_to_message_id=reply_to_id,
            reply_to_text=reply_to_text,
            reply_to_author_id=reply_to_author_id,
            reply_to_author_name=reply_to_author_name,
            reply_to_is_own_message=reply_to_is_own,
            auto_skill=topic_skill,
            channel_prompt=_channel_prompt,
            timestamp=message.date,
        )

    @staticmethod
    def _rich_block_to_text(block: Any) -> str:
        if isinstance(block, str):
            return block
        if isinstance(block, list):
            return "".join(TelegramAdapter._rich_block_to_text(item) for item in block)
        if not isinstance(block, dict):
            return ""
        typ = str(block.get("type") or "")
        if "text" in block:
            return TelegramAdapter._rich_block_to_text(block.get("text"))
        if typ == "list":
            lines = []
            for item in block.get("items") or []:
                if isinstance(item, dict):
                    label = str(item.get("label") or "-")
                    body = TelegramAdapter._rich_block_to_text(item.get("blocks") or item.get("text") or "").strip()
                    lines.append(f"{label} {body}".strip())
            return "\n".join(line for line in lines if line)
        if "blocks" in block:
            return "\n".join(
                part for part in (TelegramAdapter._rich_block_to_text(b).strip() for b in (block.get("blocks") or [])) if part
            )
        return ""

    @classmethod
    def _rich_reply_text_from_message(cls, message: Any) -> Optional[str]:
        api_kwargs = getattr(message, "api_kwargs", None)
        getter = getattr(api_kwargs, "get", None)
        rich = getter("rich_message") if callable(getter) else None
        if not isinstance(rich, dict):
            return None
        blocks = rich.get("blocks")
        if not blocks:
            return None
        text = "\n".join(
            part for part in (cls._rich_block_to_text(block).strip() for block in blocks) if part
        )
        return text or None

    # ── Message reactions (processing lifecycle) ──────────────────────────

    def _reactions_enabled(self) -> bool:
        """Check if message reactions are enabled via config/env."""
        return os.getenv("TELEGRAM_REACTIONS", "false").lower() not in {"false", "0", "no"}

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

    async def _clear_reactions(self, chat_id: str, message_id: str) -> bool:
        """Clear all reactions from a Telegram message.

        Calling ``set_message_reaction`` with ``reaction=None`` (or an empty
        sequence) is the documented Bot API way to remove all bot-set
        reactions on a message — equivalent to Bot API 10.0's
        ``deleteMessageReaction`` but supported in PTB 22.6 already.
        """
        if not self._bot:
            return False
        try:
            await self._bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=int(message_id),
                reaction=None,
            )
            return True
        except Exception as e:
            logger.debug("[%s] clear reactions failed: %s", self.name, e)
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

        On CANCELLED outcomes (e.g. the user runs ``/stop``, or a session is
        interrupted mid-flight), we explicitly clear the 👀 in-progress
        reaction so it doesn't linger on the user's message indefinitely.
        Without this clear, the only way to remove the 👀 was to wait for
        another agent run to swap it to 👍/👎 — which never happens if the
        cancellation was the last activity in the chat.
        """
        if not self._reactions_enabled():
            return
        chat_id = getattr(event.source, "chat_id", None)
        message_id = getattr(event, "message_id", None)
        if not (chat_id and message_id):
            return
        if outcome == ProcessingOutcome.CANCELLED:
            await self._clear_reactions(chat_id, message_id)
        else:
            await self._set_reaction(
                chat_id,
                message_id,
                "\U0001f44d" if outcome == ProcessingOutcome.SUCCESS else "\U0001f44e",
            )
