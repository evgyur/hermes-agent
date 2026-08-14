"""Strict local context/skill attribution audit.

``hermes context audit --local`` is dispatched before dotenv loading, file
logging, plugin discovery, hooks, MCP initialization, or agent construction.
It reads only local profile files and SQLite in URI ``mode=ro`` and emits a
privacy-safe structural receipt.
"""

from __future__ import annotations

import argparse
import builtins
import contextlib
import hashlib
import io
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml

SCHEMA_VERSION = 1
_SECRET_RE = re.compile(
    r"(?i)(?:(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S{6,})|"
    r"(?:bearer\s+[A-Za-z0-9._-]{8,})|"
    r"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{12,})|"
    r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{12,})"
)
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.:/+ -]{1,160}$")
_SKILL_LINE_PREFIX = "    - "
_NAMES_ONLY_RE = re.compile(r"^  .+ \[names only\]: (?P<names>.+)$")
_CODE_TOOLS = {
    "execute_code", "patch", "read_file", "search_files", "terminal", "write_file"
}
_WRITE_TOOLS = {"patch", "write_file", "skill_manage"}
_VERIFY_TOOLS = {
    "execute_code", "read_file", "search_files", "terminal", "vision_analyze", "web_extract"
}
_WRITE_FLAGS = (
    os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
)


class SideEffectBlocked(RuntimeError):
    """Raised when strict-local code attempts a protected side effect."""


def _safe_name(value: Any) -> str:
    text = str(value or "")
    if _SAFE_NAME_RE.fullmatch(text) and not _SECRET_RE.search(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"[REDACTED:{digest}]"


def _safe_rel(path: Path) -> str:
    parts = [_safe_name(part) for part in path.parts]
    return "/".join(parts)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _seconds(start: Any, end: Any) -> float | None:
    a, b = _parse_ts(start), _parse_ts(end)
    if not a or not b:
        return None
    return round(max(0.0, (b - a).total_seconds()), 3)


_MANIFEST_RELATIVE_PATHS = (
    ".env",
    "active_profile",
    "config.yaml",
    "state.db",
    "state.db-wal",
    "state.db-shm",
    ".skills_prompt_snapshot.json",
    "skills/.skills_prompt_snapshot.json",
    "skills/.usage.json",
    "logs/agent.log",
    "logs/errors.log",
)


def _profile_manifest(root: Path) -> dict[str, tuple[int, int, int]]:
    """Manifest only files this diagnostic or regular CLI bootstrap may touch."""
    out: dict[str, tuple[int, int, int]] = {}
    for relative in _MANIFEST_RELATIVE_PATHS:
        path = root / relative
        try:
            stat = path.lstat()
        except OSError:
            continue
        out[relative] = (stat.st_mode, stat.st_size, stat.st_mtime_ns)
    return out


@contextlib.contextmanager
def _strict_local_guard() -> Iterator[dict[str, Any]]:
    """Block Python-level writes, network, and background-thread starts."""
    attempts = {"filesystem_write": 0, "network": 0, "thread_starts": 0}
    before_threads = {thread.ident for thread in threading.enumerate()}
    original_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_thread_start = threading.Thread.start

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
            attempts["filesystem_write"] += 1
            raise SideEffectBlocked("filesystem write blocked")
        return original_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        if int(flags) & _WRITE_FLAGS:
            attempts["filesystem_write"] += 1
            raise SideEffectBlocked("filesystem write blocked")
        return original_os_open(path, flags, *args, **kwargs)

    def blocked_network(*args, **kwargs):
        attempts["network"] += 1
        raise SideEffectBlocked("network access blocked")

    def blocked_thread_start(*args, **kwargs):
        attempts["thread_starts"] += 1
        raise SideEffectBlocked("background thread start blocked")

    builtins.open = guarded_open
    io.open = guarded_open
    os.open = guarded_os_open
    socket.create_connection = blocked_network
    socket.getaddrinfo = blocked_network
    socket.socket.connect = blocked_network
    socket.socket.connect_ex = blocked_network
    threading.Thread.start = blocked_thread_start
    try:
        yield attempts
    finally:
        builtins.open = original_open
        io.open = original_io_open
        os.open = original_os_open
        socket.create_connection = original_create_connection
        socket.getaddrinfo = original_getaddrinfo
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        threading.Thread.start = original_thread_start
        time.sleep(0.02)
        after = [
            thread.name
            for thread in threading.enumerate()
            if thread.ident not in before_threads and thread.is_alive()
        ]
        attempts["surviving_new_threads"] = len(after)


def _rooted_skill_sources(profile_home: Path) -> list[tuple[str, Path]]:
    from agent.skill_utils import get_all_skills_dirs

    sources: list[tuple[str, Path]] = []
    profile_skills = (profile_home / "skills").resolve()
    external_index = 0
    for raw in get_all_skills_dirs():
        path = Path(raw)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        if resolved == profile_skills:
            label = "profile"
        else:
            label = f"external:{external_index}"
            external_index += 1
        sources.append((label, path))
    return sources


def _root_bytes(skill_root: Path, *, file_cap: int = 2000) -> tuple[int, int, bool]:
    total = 0
    files = 0
    complete = True
    try:
        candidates = sorted(skill_root.rglob("*"))
    except OSError:
        return 0, 0, False
    for path in candidates:
        if files >= file_cap:
            complete = False
            break
        try:
            if path.is_symlink() or not path.is_file():
                continue
            total += path.stat().st_size
            files += 1
        except OSError:
            complete = False
    return total, files, complete


def _compact_categories(profile_home: Path, platform: str) -> frozenset[str] | None:
    """Resolve only the skill-compaction keys without loading mutable config."""
    categories: set[str] = set()
    config: dict[str, Any] = {}
    config_path = profile_home / "config.yaml"
    try:
        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        config = parsed if isinstance(parsed, dict) else {}
        skills = config.get("skills") or {}
        global_categories = skills.get("compact_categories") or []
        if isinstance(global_categories, str):
            global_categories = [global_categories]
        categories.update(str(value).strip() for value in global_categories if str(value).strip())
        per_platform = skills.get("platform_compact_categories") or {}
        platform_categories = per_platform.get((platform or "").lower().strip(), [])
        if isinstance(platform_categories, str):
            platform_categories = [platform_categories]
        categories.update(str(value).strip() for value in platform_categories if str(value).strip())
    except Exception:
        pass

    # Focus posture adds canonical coding-category demotion. The resolver is
    # local-only (bounded git/file probes); the strict guard blocks regressions.
    try:
        from agent.coding_context import coding_compact_skill_categories

        categories.update(
            coding_compact_skill_categories(
                platform=platform,
                cwd=Path.cwd(),
                config=config,
            )
        )
    except Exception:
        pass
    return frozenset(categories) or None


def _available_tool_surface(config: dict[str, Any], platform: str) -> tuple[set[str], set[str]]:
    """Resolve built-in platform toolsets without plugin/MCP discovery."""
    try:
        from hermes_cli.tools_config import (
            CONFIGURABLE_TOOLSETS,
            PLATFORMS,
            _DEFAULT_OFF_TOOLSETS,
            _RECENTLY_SHIPPED_TOOLSETS,
            _TOOLSET_PLATFORM_RESTRICTIONS,
        )
        from toolsets import TOOLSETS, resolve_toolset

        configured = (config.get("platform_toolsets") or {}).get(platform)
        default_name = (PLATFORMS.get(platform) or {}).get(
            "default_toolset", f"hermes-{platform}"
        )
        names = [str(value) for value in configured] if isinstance(configured, list) else [default_name]
        configurable = {str(item[0]) for item in CONFIGURABLE_TOOLSETS}
        explicitly_configured = any(name in configurable for name in names)

        composite_tools: set[str] = set()
        for name in names:
            if name in TOOLSETS:
                composite_tools.update(resolve_toolset(name, include_registry=False))

        if explicitly_configured:
            enabled = {name for name in names if name in configurable}
        else:
            enabled = set()
            for name in configurable:
                allowed = _TOOLSET_PLATFORM_RESTRICTIONS.get(name)
                if allowed is not None and platform not in allowed:
                    continue
                members = set(resolve_toolset(name, include_registry=False))
                if members and members.issubset(composite_tools):
                    enabled.add(name)
            enabled -= set(_DEFAULT_OFF_TOOLSETS)
            for name in _RECENTLY_SHIPPED_TOOLSETS:
                members = set(resolve_toolset(name, include_registry=False))
                if members and members.issubset(composite_tools):
                    enabled.add(name)

        disabled = {str(value) for value in ((config.get("agent") or {}).get("disabled_toolsets") or [])}
        enabled -= disabled
        tools: set[str] = set()
        for toolset in enabled:
            tools.update(resolve_toolset(toolset, include_registry=False))
        return tools, enabled
    except Exception:
        return set(), set()


def _catalog(profile_home: Path, platform: str) -> dict[str, Any]:
    from agent.prompt_builder import build_skills_system_prompt
    from agent.skill_utils import iter_skill_index_files, parse_frontmatter

    by_name: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    physical_labels: dict[str, str] = {}
    for source_label, source_root in _rooted_skill_sources(profile_home):
        if not source_root.is_dir():
            continue
        for skill_md in iter_skill_index_files(source_root, "SKILL.md"):
            try:
                physical = str(skill_md.resolve())
            except OSError:
                physical = str(skill_md.absolute())
            rel = skill_md.relative_to(source_root)
            rooted = f"{source_label}:{_safe_rel(rel)}"
            canonical_label = physical_labels.setdefault(physical, rooted)
            try:
                raw = skill_md.read_text(encoding="utf-8")
                frontmatter, _ = parse_frontmatter(raw)
            except Exception:
                frontmatter = {}
            name = _safe_name(frontmatter.get("name") or skill_md.parent.name)
            by_name[name].setdefault(
                physical,
                {
                    "canonical_skill_path": canonical_label,
                    "body_bytes": skill_md.stat().st_size,
                    "root_bytes": None,
                    "root_files": None,
                    "root_complete": None,
                    "skill_root": skill_md.parent,
                    "skill_md": skill_md,
                },
            )

    try:
        parsed_config = yaml.safe_load((profile_home / "config.yaml").read_text(encoding="utf-8")) or {}
        audit_config = parsed_config if isinstance(parsed_config, dict) else {}
    except Exception:
        audit_config = {}
    available_tools, available_toolsets = _available_tool_surface(audit_config, platform)
    rendered = build_skills_system_prompt(
        available_tools=available_tools,
        available_toolsets=available_toolsets,
        compact_categories=_compact_categories(profile_home, platform),
        persist_snapshot=False,
    )
    block_match = re.search(
        r"<available_skills>.*?</available_skills>", rendered, re.DOTALL
    )
    block = block_match.group(0) if block_match else ""
    index_names: list[str] = []
    for line in block.splitlines():
        compact = _NAMES_ONLY_RE.match(line)
        if compact:
            index_names.extend(
                _safe_name(name.strip())
                for name in compact.group("names").split(",")
                if name.strip()
            )
        elif line.startswith(_SKILL_LINE_PREFIX):
            name = line[len(_SKILL_LINE_PREFIX):].partition(": ")[0].strip()
            if name:
                index_names.append(_safe_name(name))

    index_counts = Counter(index_names)
    duplicates = [
        {
            "name": name,
            "index_occurrences": index_counts[name],
            "candidate_count": len(candidates),
            "candidates": [
                {key: value for key, value in item.items() if key not in {"skill_root", "skill_md"}}
                for item in candidates.values()
            ],
        }
        for name, candidates in sorted(by_name.items())
        if index_counts[name] > 1 or len(candidates) > 1
    ]
    return {
        "skills_index": {
            "chars": len(block),
            "bytes": len(block.encode("utf-8")),
            "entries": len(index_names),
            "unique_names": len(set(index_names)),
        },
        "duplicate_names": duplicates,
        "duplicate_name_count": len(duplicates),
        "_by_name": by_name,
    }


def _normalize_tool_name(value: Any) -> str:
    name = str(value or "")
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    if "__" in name:
        name = name.rsplit("__", 1)[-1]
    return _safe_name(name)


def _tool_calls(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("tool_calls") or payload.get("calls") or [payload]
    if not isinstance(payload, list):
        return []
    out = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        candidate_function = item.get("function")
        function: dict[str, Any] = candidate_function if isinstance(candidate_function, dict) else item
        name = _normalize_tool_name(function.get("name") or item.get("name"))
        args = function.get("arguments", item.get("arguments", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        out.append({"id": _safe_name(item.get("id") or ""), "name": name, "args": args})
    return out


def _result_status(content: Any) -> tuple[str, bool | None, int | None]:
    if not isinstance(content, str):
        return "missing_result", None, None
    if "[SKILL_PRUNED" in content:
        return "pruned", None, None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return "full", True, None
    if not isinstance(payload, dict):
        return "full", True, None
    if payload.get("dedup") is True or payload.get("content_returned") is False:
        return "dedup", False, 0
    body = payload.get("content")
    if isinstance(body, str):
        return "full", True, len(body.encode("utf-8"))
    return "full", True, None


def _resolve_skill(
    requested: str,
    file_path: str | None,
    catalog_by_name: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    safe_requested = _safe_name(requested)
    candidates = list(catalog_by_name.get(safe_requested, {}).values())
    if not candidates and ":" in safe_requested:
        return {
            "canonical_skill_path": None,
            "resolution_status": "plugin_uninspected",
            "candidates": [],
            "body_bytes": None,
            "root_bytes": None,
        }
    if not candidates:
        return {
            "canonical_skill_path": None,
            "resolution_status": "missing",
            "candidates": [],
            "body_bytes": None,
            "root_bytes": None,
        }
    public_candidates = [item["canonical_skill_path"] for item in candidates]
    if len(candidates) != 1:
        return {
            "canonical_skill_path": None,
            "resolution_status": "ambiguous",
            "candidates": public_candidates,
            "body_bytes": None,
            "root_bytes": None,
        }
    item = candidates[0]
    if item["root_bytes"] is None:
        root_total, root_files, complete = _root_bytes(item["skill_root"])
        item["root_bytes"] = root_total
        item["root_files"] = root_files
        item["root_complete"] = complete
    body_bytes = item["body_bytes"]
    if file_path:
        rel = Path(file_path)
        if rel.is_absolute() or ".." in rel.parts:
            body_bytes = None
        else:
            target = item["skill_root"] / rel
            try:
                body_bytes = target.stat().st_size if target.is_file() else None
            except OSError:
                body_bytes = None
    return {
        "canonical_skill_path": item["canonical_skill_path"],
        "resolution_status": "unique",
        "candidates": public_candidates,
        "body_bytes": body_bytes,
        "root_bytes": item["root_bytes"],
    }


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _history(
    profile_home: Path,
    catalog_by_name: dict[str, dict[str, dict[str, Any]]],
    *,
    session_limit: int,
    task_limit: int,
) -> dict[str, Any]:
    db_path = profile_home / "state.db"
    if not db_path.is_file():
        return {"available": False, "reason": "state_db_missing", "loads": [], "tasks": []}
    connection = _connect_ro(db_path)
    try:
        sessions = connection.execute(
            "SELECT id FROM sessions ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT ?",
            (session_limit,),
        ).fetchall()
        session_ids = [row["id"] for row in sessions]
        if not session_ids:
            return {"available": True, "loads": [], "tasks": [], "sessions_scanned": 0}
        placeholders = ",".join("?" for _ in session_ids)
        rows = connection.execute(
            f"SELECT id, session_id, role, tool_call_id, tool_calls, tool_name, "
            f"timestamp, content FROM messages WHERE session_id IN ({placeholders}) "
            f"ORDER BY session_id, id",
            session_ids,
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["session_id"])].append(row)

    task_candidates: list[dict[str, Any]] = []
    for session_order, session_id in enumerate(session_ids, 1):
        messages = grouped.get(str(session_id), [])
        result_by_id = {
            str(row["tool_call_id"]): row["content"]
            for row in messages
            if row["role"] == "tool" and row["tool_call_id"]
        }
        current: dict[str, Any] | None = None
        for message_index, row in enumerate(messages):
            if row["role"] == "user":
                if current:
                    task_candidates.append(current)
                current = {
                    "session_order": session_order,
                    "started_at": row["timestamp"],
                    "completion_at": None,
                    "events": [],
                    "code_tools": set(),
                    "message_rows": messages,
                }
                continue
            if current is None or row["role"] != "assistant":
                continue
            calls = _tool_calls(row["tool_calls"])
            if not calls:
                current["completion_at"] = row["timestamp"]
                continue
            for call_index, call in enumerate(calls):
                name = call["name"]
                if name in _CODE_TOOLS:
                    current["code_tools"].add(name)
                current["events"].append(
                    {
                        "timestamp": row["timestamp"],
                        "message_index": message_index,
                        "call_index": call_index,
                        "sibling_names": [c["name"] for c in calls],
                        "call": call,
                        "result": result_by_id.get(call["id"]),
                    }
                )
        if current:
            task_candidates.append(current)

    eligible = [
        task for task in task_candidates
        if task["code_tools"] and task["completion_at"] is not None
    ]
    eligible.sort(key=lambda task: str(task["started_at"] or ""), reverse=True)
    selected = list(reversed(eligible[:task_limit]))

    loads: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    repeat_counts: Counter[tuple[int, str, str]] = Counter()
    for task_number, task in enumerate(selected, 1):
        task_ref = f"task-{task_number:06d}"
        session_ref = f"session-{task['session_order']:06d}"
        task_loads: list[dict[str, Any]] = []
        event_names = [event["call"]["name"] for event in task["events"]]
        for event_index, event in enumerate(task["events"]):
            call = event["call"]
            if call["name"] != "skill_view":
                continue
            requested = _safe_name(call["args"].get("name"))
            raw_file = call["args"].get("file_path")
            requested_file = _safe_name(raw_file) if raw_file else "SKILL.md"
            resolved = _resolve_skill(requested, raw_file, catalog_by_name)
            repeat_key = (
                task["session_order"],
                resolved["canonical_skill_path"] or requested,
                requested_file,
            )
            repeat_counts[repeat_key] += 1
            status, content_returned, returned_bytes = _result_status(event["result"])
            if event["call_index"] + 1 < len(event["sibling_names"]):
                next_action = {
                    "kind": "parallel_sibling",
                    "tool": event["sibling_names"][event["call_index"] + 1],
                }
            else:
                following = event_names[event_index + 1:event_index + 2]
                next_action = (
                    {"kind": "tool_call", "tool": following[0]}
                    if following else {"kind": "assistant_response", "tool": None}
                )
            effective = returned_bytes
            if status == "full" and effective is None:
                effective = resolved["body_bytes"]
            row_out = {
                "session_ref": session_ref,
                "task_ref": task_ref,
                "requested_skill": requested,
                "requested_file": requested_file,
                **resolved,
                "trigger_reason": "unknown",
                "load_mode": "skill_view_support_file" if raw_file else "skill_view_root",
                "content_status": status,
                "content_returned": content_returned,
                "effective_body_bytes": effective,
                "repeat_ordinal": repeat_counts[repeat_key],
                "repeated": repeat_counts[repeat_key] > 1,
                "next_action": next_action,
            }
            task_loads.append(row_out)
            loads.append(row_out)

        writes = [event for event in task["events"] if event["call"]["name"] in _WRITE_TOOLS]
        first_write = writes[0] if writes else None
        verifications = [
            event for event in task["events"]
            if event["call"]["name"] in _VERIFY_TOOLS
            and (first_write is None or str(event["timestamp"]) >= str(first_write["timestamp"]))
        ]
        max_cascade = 0
        current_cascade = 0
        for name in event_names:
            if name == "skill_view":
                current_cascade += 1
                max_cascade = max(max_cascade, current_cascade)
            else:
                current_cascade = 0
        task_rows.append(
            {
                "task_ref": task_ref,
                "session_ref": session_ref,
                "inclusion": "completed user-turn with at least one coding tool",
                "skill_loads": len(task_loads),
                "unique_skill_roots": len({row["canonical_skill_path"] or row["requested_skill"] for row in task_loads}),
                "repeat_loads": sum(1 for row in task_loads if row["repeated"]),
                "effective_body_bytes": sum(row["effective_body_bytes"] or 0 for row in task_loads),
                "unknown_body_bytes": sum(1 for row in task_loads if row["effective_body_bytes"] is None),
                "cascade_depth": max_cascade,
                "latency_to_first_write_seconds": _seconds(task["started_at"], first_write["timestamp"] if first_write else None),
                "latency_to_first_verification_seconds": _seconds(task["started_at"], verifications[0]["timestamp"] if verifications else None),
                "latency_to_completion_seconds": _seconds(task["started_at"], task["completion_at"]),
            }
        )

    by_session: dict[str, dict[str, int]] = defaultdict(lambda: {"loads": 0, "repeats": 0, "effective_body_bytes": 0})
    for row in loads:
        aggregate = by_session[row["session_ref"]]
        aggregate["loads"] += 1
        aggregate["repeats"] += int(row["repeated"])
        aggregate["effective_body_bytes"] += row["effective_body_bytes"] or 0
    return {
        "available": True,
        "selection": {
            "corpus": "coding",
            "session_limit": session_limit,
            "task_limit": task_limit,
            "inclusion": "completed user-turn with at least one coding tool",
            "exclusion": "unfinished turns and turns without coding tools",
            "task_identity": "derived_user_turn",
        },
        "sessions_scanned": len(session_ids),
        "tasks": task_rows,
        "loads": loads,
        "session_rollups": [
            {"session_ref": key, **value} for key, value in sorted(by_session.items())
        ],
        "limitations": [
            "historical model-issued skill_view calls have trigger_reason=unknown",
            "preloaded/slash/bundle skill bodies are not persisted as structured load events",
            "terminal arguments are not inspected, so terminal writes are not classified as first_write",
            "pruned or missing tool results keep effective_body_bytes unknown",
        ],
    }


def run_local_audit(
    profile_home: Path,
    *,
    platform: str = "telegram",
    session_limit: int = 250,
    task_limit: int = 20,
    include_history: bool = True,
) -> tuple[dict[str, Any], int]:
    before = _profile_manifest(profile_home)
    error_type: str | None = None
    data: dict[str, Any] = {}
    with _strict_local_guard() as guard:
        try:
            catalog = _catalog(profile_home, platform)
            by_name = catalog.pop("_by_name")
            history = (
                _history(
                    profile_home,
                    by_name,
                    session_limit=session_limit,
                    task_limit=task_limit,
                )
                if include_history else {"available": False, "reason": "disabled"}
            )
            data = {"catalog": catalog, "history": history}
        except Exception as exc:  # error text can carry paths/secrets; type only
            error_type = type(exc).__name__
    after = _profile_manifest(profile_home)
    changed = sorted(set(before) ^ set(after) | {key for key in before.keys() & after.keys() if before[key] != after[key]})
    guard_receipt = {
        "network_attempts": guard.get("network", 0),
        "filesystem_write_attempts": guard.get("filesystem_write", 0),
        "thread_start_attempts": guard.get("thread_starts", 0),
        "surviving_new_threads": guard.get("surviving_new_threads", 0),
        "profile_manifest_unchanged": not changed,
        "manifest_scope": "audit-and-bootstrap-writable-surfaces",
        "changed_profile_paths": changed[:20],
        "changed_profile_path_count": len(changed),
        "sqlite_mode": "ro+query_only",
    }
    ok = error_type is None and all(
        (
            guard_receipt["network_attempts"] == 0,
            guard_receipt["filesystem_write_attempts"] == 0,
            guard_receipt["thread_start_attempts"] == 0,
            guard_receipt["surviving_new_threads"] == 0,
        )
    )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed_closed",
        "mode": "strict_local_read_only",
        "platform": _safe_name(platform),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "guards": guard_receipt,
        **data,
    }
    if error_type:
        receipt["error_type"] = error_type
    return receipt, 0 if ok else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes context audit")
    parser.add_argument("--local", action="store_true", help="Required: prohibit network and writes")
    parser.add_argument("--json", action="store_true", help="Emit JSON receipt")
    parser.add_argument("--platform", default="telegram")
    parser.add_argument("--session-limit", type=int, default=250)
    parser.add_argument("--task-limit", type=int, default=20)
    parser.add_argument("--no-history", action="store_true")
    return parser


def early_cli_main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv or []))
    if not args.local:
        parser.error("--local is required; context audit is intentionally local-only")
    if not 1 <= args.session_limit <= 1000:
        parser.error("--session-limit must be between 1 and 1000")
    if not 1 <= args.task_limit <= 100:
        parser.error("--task-limit must be between 1 and 100")
    profile_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    receipt, code = run_local_audit(
        profile_home,
        platform=args.platform,
        session_limit=args.session_limit,
        task_limit=args.task_limit,
        include_history=not args.no_history,
    )
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    else:
        catalog = receipt.get("catalog", {})
        index = catalog.get("skills_index", {})
        history = receipt.get("history", {})
        print(f"Context audit: {receipt['status']} ({receipt['mode']})")
        print(f"Skills index: {index.get('bytes', 0):,} bytes / {index.get('entries', 0)} entries")
        print(f"Duplicate names: {catalog.get('duplicate_name_count', 0)}")
        print(f"Tasks sampled: {len(history.get('tasks', []))}")
        print(f"Skill loads attributed: {len(history.get('loads', []))}")
    return code


if __name__ == "__main__":
    raise SystemExit(early_cli_main(sys.argv[1:]))
