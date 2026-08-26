"""Successful-final classification shared by gateway recovery and approvals."""

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence


_VISIBLE_FINAL_SOURCES = Literal["content", "codex_final_answer"]
_SYNTHETIC_DISPLAY_KINDS = {"internal_notification", "hidden"}
_SUCCESS_EXIT_REASONS = {"", "text_response(finish_reason=stop)"}


@dataclass(frozen=True)
class VisibleFinal:
    row_id: int | None
    text: str
    source: _VISIBLE_FINAL_SOURCES


def _normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


def _row_is_visible(row: Mapping[str, Any]) -> bool:
    return not (
        row.get("hidden") is True
        or row.get("visible") is False
        or row.get("observed") is True
        or row.get("internal") is True
        or _normalized(row.get("display_kind")) in _SYNTHETIC_DISPLAY_KINDS
    )


def _codex_final_text(row: Mapping[str, Any]) -> tuple[bool, str]:
    items = row.get("codex_message_items")
    if not isinstance(items, list):
        return False, ""

    found = False
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if _normalized(item.get("phase")) != "final_answer":
            continue
        found = True
        if (
            item.get("type") != "message"
            or item.get("role") != "assistant"
            or item.get("phase") != "final_answer"
            or item.get("status") != "completed"
            or not isinstance(item.get("content"), list)
        ):
            return False, ""
        for content in item["content"]:
            if (
                not isinstance(content, dict)
                or content.get("type") != "output_text"
                or not isinstance(content.get("text"), str)
            ):
                return False, ""
            parts.append(content["text"])
    return found, "".join(parts)


def is_successful_final(row: Mapping[str, Any]) -> bool:
    if not isinstance(row, Mapping) or row.get("role") != "assistant":
        return False
    if not _row_is_visible(row):
        return False
    if row.get("tool_calls"):
        return False
    if _normalized(row.get("finish_reason")) != "stop":
        return False
    if row.get("turn_failed") in (1, True):
        return False
    if row.get("turn_interrupted") in (1, True):
        return False
    if _normalized(row.get("turn_exit_reason")) not in _SUCCESS_EXIT_REASONS:
        return False

    items = row.get("codex_message_items")
    if items is not None:
        valid, _text = _codex_final_text(row)
        return valid
    content = row.get("content")
    return isinstance(content, str) and bool(content.strip())


def _real_user_row(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("role") == "user"
        and row.get("observed") is not True
        and row.get("internal") is not True
        and _normalized(row.get("display_kind"))
        not in _SYNTHETIC_DISPLAY_KINDS
    )


def _visible_text(row: Mapping[str, Any]) -> tuple[str, _VISIBLE_FINAL_SOURCES]:
    if row.get("codex_message_items") is not None:
        valid, text = _codex_final_text(row)
        return (text, "codex_final_answer") if valid else ("", "codex_final_answer")
    return str(row.get("content") or ""), "content"


def successful_final_text(row: Mapping[str, Any]) -> str | None:
    """Return the exact persisted final text, or ``None`` if not terminal.

    Unlike the bounded conversational-context extractor below, delivery
    recovery must never truncate a response: this value is the durable wire
    payload that was authorized by the completed turn.
    """
    if not is_successful_final(row):
        return None
    text, _source = _visible_text(row)
    return text


def extract_bounded_visible_final(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_raw_rows: int = 256,
    max_visible_finals: int = 24,
    max_chars: int = 2048,
) -> VisibleFinal | None:
    if max_raw_rows <= 0 or max_visible_finals <= 0 or max_chars < 0:
        return None

    bounded = list(rows or ())[-max_raw_rows:]
    real_turn = -1
    eligible: list[tuple[Mapping[str, Any], int]] = []
    for raw_row in bounded:
        if not isinstance(raw_row, Mapping):
            continue
        if _real_user_row(raw_row):
            real_turn += 1
            continue
        if real_turn >= 0 and is_successful_final(raw_row):
            eligible.append((raw_row, real_turn))

    inspected = 0
    for row, _turn in reversed(eligible):
        if inspected >= max_visible_finals:
            break
        inspected += 1
        text, source = _visible_text(row)
        if not text:
            continue
        raw_id = row.get("id")
        row_id = raw_id if type(raw_id) is int else None
        return VisibleFinal(row_id=row_id, text=text[:max_chars], source=source)
    return None
