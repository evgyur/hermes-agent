#!/usr/bin/env python3
"""
Async (background) delegation registry.

Backs ``delegate_task(background=true)``: the parent agent dispatches a
subagent that runs on a module-level daemon executor and returns a handle
immediately, so the user and the model can keep working while the child runs.

When the child finishes, a completion event is pushed onto the SHARED
``process_registry.completion_queue`` with ``type="async_delegation"``. The
CLI (``cli.py`` process_loop) and gateway (``_run_process_watcher`` /
``completion_queue`` drain) already poll that queue while the agent is idle
and forge a fresh user/internal turn from each event. We deliberately reuse
that rail rather than reaching into a running agent loop:

  - completions surface as a NEW turn when the agent is idle, never spliced
    between a tool result and an assistant message. That keeps strict
    message-role alternation legal and the prompt cache intact (hard
    invariant: never mutate past context).
  - we inherit the queue's de-dup, crash-recovery checkpoint, and the
    existing CLI + gateway drain wiring for free — no new drain loops in the
    two largest files in the repo.

The completion payload carries a RICH, self-contained task-source block (the
original goal, the context the parent supplied, toolsets, model, dispatch
time, status, and the full result summary). When the result re-enters the
conversation the parent may be deep in unrelated context and won't remember
why the subagent existed; the block lets it either use the result or
re-dispatch if the world has moved on.

This module owns ONLY the async lifecycle. The actual child build + run is
delegated back to ``delegate_tool._run_single_child`` via an injected
runner, so all the credential leasing, heartbeat, timeout, and result-shaping
logic stays in one place.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from hermes_constants import get_hermes_home
from tools.daemon_pool import DaemonThreadPoolExecutor
from tools.thread_context import propagate_context_to_thread

logger = logging.getLogger(__name__)

_DELEGATION_CONTRACT_VERSION = 2
_MAX_RESTART_ATTEMPTS = 3
_RESTART_POLICY = "gateway_owned_v1"
_CURRENT_DELEGATION_ID: ContextVar[str] = ContextVar(
    "HERMES_CURRENT_ASYNC_DELEGATION_ID", default=""
)
_CURRENT_EXECUTION_GENERATION: ContextVar[int] = ContextVar(
    "HERMES_CURRENT_ASYNC_DELEGATION_GENERATION", default=-1
)
_LAST_PERSIST_REJECTION: ContextVar[str] = ContextVar(
    "HERMES_ASYNC_DELEGATION_PERSIST_REJECTION", default=""
)


class TrustedRestartEvent(dict):
    """Internal wake envelope; durable nonce/CAS checks provide authenticity."""

# Back-compat alias — the daemon executor now lives in tools.daemon_pool so
# other subsystems (tool_executor, memory_manager, delegate_tool, skills_hub)
# can share it. Existing imports of ``_DaemonThreadPoolExecutor`` keep working.
_DaemonThreadPoolExecutor = DaemonThreadPoolExecutor


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
# A persistent daemon executor (NOT a `with ThreadPoolExecutor()` block, which
# would join on exit and defeat the whole point of async). Workers are daemon
# threads so a hard process exit doesn't hang on an in-flight child.
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_executor_max_workers: int = 0

_records_lock = threading.Lock()
# delegation_id -> record dict. Kept for the lifetime of the run plus a short
# tail after completion so `list_async_delegations()` can show recent results.
_records: Dict[Any, Dict[str, Any]] = {}


def _record_home(record: Optional[Dict[str, Any]] = None) -> str:
    return str((record or {}).get("_state_home") or _db_path().parent.resolve())


def _get_record_locked(
    delegation_id: str, state_home: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Resolve a record by durable home while preserving legacy string keys."""
    home = str(state_home or _db_path().parent.resolve())
    record = _records.get((home, delegation_id))
    if record is not None:
        return record
    legacy = _records.get(delegation_id)
    if legacy is not None and _record_home(legacy) == home:
        return legacy
    return None


def _store_record_locked(delegation_id: str, record: Dict[str, Any]) -> None:
    home = _record_home(record)
    existing = _records.get(delegation_id)
    if existing is None or _record_home(existing) == home:
        _records[delegation_id] = record
    else:
        _records[(home, delegation_id)] = record


def _pop_record_locked(delegation_id: str, state_home: Optional[str] = None):
    home = str(state_home or _db_path().parent.resolve())
    compound = (home, delegation_id)
    if compound in _records:
        return _records.pop(compound, None)
    legacy = _records.get(delegation_id)
    if legacy is not None and _record_home(legacy) == home:
        return _records.pop(delegation_id, None)
    return None

_DEFAULT_MAX_ASYNC_CHILDREN = 3
# How many completed records to retain for status queries before pruning.
_MAX_RETAINED_COMPLETED = 50
_DURABLE_RETENTION_SECONDS = 7 * 24 * 60 * 60
_MAX_DURABLE_PENDING = 1000
_MAX_DURABLE_PENDING_BYTES = 128 * 1024 * 1024
# A pending completion whose delivery keeps failing is retried across claim
# cycles (and across restarts via restore_undelivered_completions). Cap the
# attempts so an unroutable row converges to a terminal 'dropped' state
# instead of replaying on every restart forever.
_MAX_DELIVERY_ATTEMPTS = 8
# Staleness cap for restart replay: a pending completion older than this is
# terminally dropped instead of re-run as a fresh full-context turn (see
# restore_undelivered_completions). 48h keeps overnight/weekend results
# deliverable while stopping weeks-old sessions from replaying after upgrades.
_MAX_COMPLETION_REPLAY_AGE_S = 48 * 3600.0
_MAX_DURABLE_JSON_BYTES = 2 * 1024 * 1024
_MAX_DURABLE_JSON_DEPTH = 32
_MAX_DURABLE_JSON_ITEMS = 10_000
_DB_LOCK = threading.Lock()
_COMPLETION_PERSIST_RETRY_ATTEMPTS = 4
_COMPLETION_PERSIST_RETRY_BASE_SECONDS = 0.025
_MAX_SPILL_CLEANUP_BATCH = 256
_STATE_HOME_OVERRIDE: ContextVar[str] = ContextVar(
    "HERMES_ASYNC_DELEGATION_STATE_HOME", default=""
)

# ---------------------------------------------------------------------------
# Stale-delegation detection (progress-based, on by default)
# ---------------------------------------------------------------------------
# A detached runner that wedges before returning (e.g. stuck inside its first
# model API call — #60203) never reaches its ``finally`` finalizer, so no
# completion event is ever published: the delegation shows "dispatched"
# forever and the owning session looks silent until a process restart. We do
# NOT fix this with a wall-clock timeout — legitimate heavy subagent work
# (deep reviews, research fan-outs, slow reasoning models) must never be
# killed for taking long (see delegate_tool.DEFAULT_CHILD_TIMEOUT rationale).
# Instead a single monitor thread watches per-dispatch PROGRESS (api-call
# count + current tool, via an injected ``progress_fn``): a child that is
# advancing is left alone forever; a child with NO progress past the stale
# threshold is interrupted, given a grace window to unwind and deliver its
# partial results through the normal finalize path, and only force-finalized
# with a terminal ``stalled`` event if it never returns.
#
# Thresholds mirror the sync-path heartbeat staleness monitor in
# delegate_tool: idle (not inside a tool) stays tight so a wedged first API
# call is caught quickly; in-tool is much higher so legitimately slow tools
# (long terminal commands, big fetches) get time to finish.
_STALE_CHECK_INTERVAL = 30.0  # seconds between monitor sweeps
_STALE_IDLE_SECONDS = 450.0  # no progress, no current tool → stalled
_STALE_IN_TOOL_SECONDS = 1200.0  # no progress while inside a tool → stalled
_STALL_GRACE_SECONDS = 120.0  # after interrupt, time for the runner to return

_monitor_lock = threading.Lock()
_monitor_thread: Optional[threading.Thread] = None
_monitor_stop = threading.Event()


def _db_path():
    scoped_home = _STATE_HOME_OVERRIDE.get()
    return Path(scoped_home) / "state.db" if scoped_home else get_hermes_home() / "state.db"


@contextmanager
def state_home_scope(home: "str | Path") -> Iterator[None]:
    """Bind durable delegation operations to one immutable profile home."""
    token = _STATE_HOME_OVERRIDE.set(str(Path(home).resolve()))
    try:
        yield
    finally:
        _STATE_HOME_OVERRIDE.reset(token)


def _decode_durable_json(value: Any, default: Any) -> tuple[bool, Any, str]:
    """Decode one typed, size/depth/item-bounded durable JSON value."""
    if not value:
        return True, default, ""
    if not isinstance(value, str):
        return False, default, "not_text"
    if len(value.encode("utf-8", errors="replace")) > _MAX_DURABLE_JSON_BYTES:
        return False, default, "oversize"
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return False, default, "invalid_json"
    if not isinstance(decoded, type(default)):
        return False, default, "wrong_type"

    item_count = 0
    stack: list[tuple[Any, int]] = [(decoded, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > _MAX_DURABLE_JSON_DEPTH:
            return False, default, "too_deep"
        if isinstance(current, dict):
            item_count += len(current)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            item_count += len(current)
            stack.extend((item, depth + 1) for item in current)
        if item_count > _MAX_DURABLE_JSON_ITEMS:
            return False, default, "too_many_items"
    return True, decoded, ""


def _load_durable_json(value: Any, default: Any) -> Any:
    """Decode persisted JSON without letting corrupt rows strand recovery."""
    return _decode_durable_json(value, default)[1]


def _encode_durable_json(value: Any) -> Optional[str]:
    """Return compact bounded JSON, or ``None`` when it exceeds the contract."""
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except (TypeError, ValueError):
        return None
    valid, _decoded, _reason = _decode_durable_json(
        encoded, {} if isinstance(value, dict) else []
    )
    return encoded if valid else None


def _persist_rejection_error(default: str) -> str:
    reason = _LAST_PERSIST_REJECTION.get()
    if reason == "pending_capacity":
        return (
            "Durable async delegation capacity is full. Existing undelivered "
            "results were retained; wait for delivery or run synchronously."
        )
    if reason in {"payload_oversize", "payload_invalid"}:
        return "Async delegation contract is too large or invalid to persist safely"
    return default


def _bound_completion_payload(
    event: Dict[str, Any], result: Dict[str, Any]
) -> Optional[str]:
    """Keep hot SQLite payloads bounded, spilling oversized full fidelity."""
    if _encode_durable_json(event) is not None and _encode_durable_json(result) is not None:
        return None
    full_payload = json.dumps(
        {"event": event, "result": result},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    payload_bytes = full_payload.encode("utf-8")
    digest = hashlib.sha256(payload_bytes).hexdigest()
    delegation_id = "".join(
        ch for ch in str(event.get("delegation_id") or "unknown") if ch.isalnum() or ch in "-_"
    )[:100]
    spill_dir = _db_path().parent / "archive" / "async-delegation-payloads"
    spill_dir.mkdir(parents=True, exist_ok=True)
    target = spill_dir / f"{delegation_id}-{digest[:16]}.json"
    temp = spill_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
    temp.write_bytes(payload_bytes)
    os.replace(temp, target)
    spill = {
        "path": str(target),
        "sha256": digest,
        "bytes": len(payload_bytes),
    }
    safe_result = {
        "status": str(result.get("status") or event.get("status") or "completed"),
        "summary": None,
        "error": "Oversized completion archived outside hot state.",
        "payload_spill": spill,
    }
    safe_event = {
        key: event.get(key)
        for key in (
            "type", "delegation_id", "session_key", "origin_ui_session_id",
            "origin_session_id", "parent_session_id", "execution_generation",
            "status", "dispatched_at", "completed_at", "scope_id", "user_id",
            "user_name",
        )
        if key in event
    }
    safe_event.update(
        {
            "type": "async_delegation",
            "summary": None,
            "error": safe_result["error"],
            "payload_spill": spill,
        }
    )
    event.clear()
    event.update(safe_event)
    result.clear()
    result.update(safe_result)
    return str(target)


def _spill_root() -> "Path":
    from pathlib import Path

    return _db_path().parent / "archive" / "async-delegation-payloads"


def _spill_paths_from_json(value: Any) -> set[str]:
    valid, decoded, _reason = _decode_durable_json(value, {})
    if not valid or not isinstance(decoded, dict):
        return set()
    spill = decoded.get("payload_spill")
    if not isinstance(spill, dict):
        return set()
    path = str(spill.get("path") or "")
    return {path} if path else set()


def _canonical_spill_path(path_value: Any) -> Optional["Path"]:
    from pathlib import Path

    if not path_value:
        return None
    root = _spill_root().resolve()
    candidate = Path(str(path_value))
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def _validate_payload_spill(event: Dict[str, Any]) -> tuple[bool, str]:
    spill = event.get("payload_spill")
    if spill is None:
        return True, ""
    if not isinstance(spill, dict):
        return False, "spill_invalid"
    from pathlib import Path

    raw_path = Path(str(spill.get("path") or ""))
    if raw_path.is_symlink():
        return False, "spill_symlink"
    path = _canonical_spill_path(raw_path)
    if path is None:
        return False, "spill_outside_root"
    try:
        if path.is_symlink() or not path.is_file():
            return False, "spill_missing"
        payload = path.read_bytes()
    except OSError:
        return False, "spill_unreadable"
    try:
        expected_bytes = int(spill.get("bytes"))
    except (TypeError, ValueError):
        return False, "spill_size_invalid"
    if len(payload) != expected_bytes:
        return False, "spill_size_mismatch"
    expected_digest = str(spill.get("sha256") or "")
    if not expected_digest or hashlib.sha256(payload).hexdigest() != expected_digest:
        return False, "spill_digest_mismatch"
    return True, ""


def _cleanup_unreferenced_spills(
    *, immediate_candidates: Optional[set[str]] = None
) -> None:
    """Unlink bounded, canonical, unreferenced payload files after DB commit."""
    root = _spill_root()
    if not root.exists():
        return
    reachable: set[str] = set()
    with _DB_LOCK, _transaction() as conn:
        for row in conn.execute(
            "SELECT event_json, result_json FROM async_delegations"
        ).fetchall():
            reachable.update(_spill_paths_from_json(row[0]))
            reachable.update(_spill_paths_from_json(row[1]))
        for (raw,) in conn.execute(
            "SELECT result_json FROM async_delegation_children"
        ).fetchall():
            reachable.update(_spill_paths_from_json(raw))
    reachable_canonical = {
        str(path)
        for value in reachable
        if (path := _canonical_spill_path(value)) is not None
    }
    immediate = {
        str(path)
        for value in (immediate_candidates or set())
        if (path := _canonical_spill_path(value)) is not None
    }
    cutoff = time.time() - _DURABLE_RETENTION_SECONDS
    removed = 0
    try:
        candidates = list(root.iterdir())
    except OSError:
        return
    for candidate in candidates:
        if removed >= _MAX_SPILL_CLEANUP_BATCH:
            break
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            canonical = candidate.resolve(strict=True)
            canonical.relative_to(root.resolve())
            canonical_text = str(canonical)
            if canonical_text in reachable_canonical:
                continue
            if canonical_text not in immediate and candidate.stat().st_mtime >= cutoff:
                continue
            candidate.unlink()
            removed += 1
        except (OSError, ValueError):
            continue


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        _initialize_schema(conn)
    except Exception:
        # A PRAGMA/DDL failure after a successful connect() must not leak the
        # just-opened connection back to the caller.
        conn.close()
        raise
    return conn


def initialize_storage() -> None:
    """Create/migrate delegation and parent-barrier tables for this home."""
    with _DB_LOCK, _transaction() as conn:
        conn.execute("SELECT 1")


def _initialize_schema(conn: sqlite3.Connection) -> None:
    from hermes_state import apply_wal_with_fallback
    from tools.parent_task_barrier import initialize_schema_on_connection

    apply_wal_with_fallback(conn, db_label="state.db (async_delegation)")
    initialize_schema_on_connection(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS async_delegations (
            delegation_id TEXT PRIMARY KEY,
            origin_session TEXT NOT NULL,
            origin_ui_session_id TEXT NOT NULL DEFAULT '',
            parent_session_id TEXT,
            state TEXT NOT NULL,
            dispatched_at REAL NOT NULL,
            completed_at REAL,
            updated_at REAL NOT NULL,
            event_json TEXT,
            result_json TEXT,
            delivery_state TEXT NOT NULL DEFAULT 'pending',
            delivery_attempts INTEGER NOT NULL DEFAULT 0,
            delivered_at REAL,
            owner_pid INTEGER,
            owner_started_at INTEGER,
            task_json TEXT,
            delivery_claim TEXT,
            delivery_claimed_at REAL,
            wire_accepted_at REAL,
            origin_session_id TEXT NOT NULL DEFAULT ''
        )"""
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(async_delegations)")}
    for name, sql_type in (
        ("owner_pid", "INTEGER"),
        ("owner_started_at", "INTEGER"),
        ("task_json", "TEXT"),
        ("delivery_claim", "TEXT"),
        ("delivery_claimed_at", "REAL"),
        ("wire_accepted_at", "REAL"),
        # Raw api_server session id (X-Hermes-Session-Id) of the ORIGINATING
        # request — the wake self-post target. Without persisting it,
        # completions recovered after a process restart are unroutable on
        # api_server (the in-memory record that carried it is gone).
        ("origin_session_id", "TEXT"),
        ("public_title", "TEXT NOT NULL DEFAULT ''"),
        ("progress_json", "TEXT"),
        ("heartbeat_at", "REAL"),
        ("api_calls", "INTEGER NOT NULL DEFAULT 0"),
        ("current_tool", "TEXT NOT NULL DEFAULT ''"),
        ("status_revision", "INTEGER NOT NULL DEFAULT 1"),
        ("child_session_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("child_capability_names_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("restart_policy", "TEXT NOT NULL DEFAULT ''"),
        ("restart_count", "INTEGER NOT NULL DEFAULT 0"),
        ("restart_reason", "TEXT NOT NULL DEFAULT ''"),
        ("restart_nonce", "TEXT NOT NULL DEFAULT ''"),
        ("runner_returned", "INTEGER NOT NULL DEFAULT 0"),
        ("contract_version", "INTEGER NOT NULL DEFAULT 2"),
        ("task_fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ("restart_budget", "INTEGER NOT NULL DEFAULT 3"),
        ("execution_generation", "INTEGER NOT NULL DEFAULT 0"),
        ("child_count", "INTEGER NOT NULL DEFAULT 0"),
        ("output_schema_fingerprints_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("quarantine_reason", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE async_delegations ADD COLUMN {name} {sql_type}")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS async_delegation_children (
            delegation_id TEXT NOT NULL,
            child_index INTEGER NOT NULL,
            child_session_id TEXT NOT NULL,
            capability_names_json TEXT NOT NULL DEFAULT '[]',
            capability_fingerprint TEXT NOT NULL DEFAULT '',
            output_schema_fingerprint TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'running',
            execution_generation INTEGER NOT NULL DEFAULT 0,
            replay_decision TEXT NOT NULL DEFAULT '',
            result_json TEXT,
            completed_at REAL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(delegation_id, child_index),
            FOREIGN KEY(delegation_id) REFERENCES async_delegations(delegation_id)
                ON DELETE CASCADE
        )"""
    )


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit/rollback on exit, and ALWAYS close it.

    ``sqlite3.Connection.__enter__``/``__exit__`` only commit or roll back the
    transaction; they do not close the connection. Using ``with _connect()``
    alone therefore leaks a connection — and its WAL/SHM file descriptors — on
    every durable dispatch, completion, and delivery-claim, deferring the close
    to the garbage collector. On a long-running gateway that exhausts
    ``RLIMIT_NOFILE`` (the cron-ledger sibling of this bug was #69567 / PR #69594).
    """
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _capture_routing_origin() -> Dict[str, Any]:
    """Snapshot the dispatching turn's routing origin for the completion event.

    Captured on the PARENT thread at dispatch time (the daemon worker doesn't
    carry the contextvars) and persisted with the durable record, so a
    completion replayed after a restart can reconstruct a full SessionSource
    even when the session-store origin and in-memory source cache are gone.
    scope_id matters most: on a relay-fronted deployment the connector's
    fail-closed egress guard needs the tenant discriminator (or a user
    binding) to route a scoped reply; without it, post-restart scoped
    completions bounce with "target not routed to an onboarded tenant"
    (staging 2026-08-09 defect #4). Best-effort — empty values are simply
    omitted so CLI/contextvar-unaware paths persist nothing new.
    """
    origin: Dict[str, Any] = {}
    try:
        from gateway.session_context import get_session_env

        for evt_key, env_name in (
            ("scope_id", "HERMES_SESSION_SCOPE_ID"),
            ("user_id", "HERMES_SESSION_USER_ID"),
            ("user_name", "HERMES_SESSION_USER_NAME"),
        ):
            value = get_session_env(env_name, "")
            if value:
                origin[evt_key] = value
    except Exception:  # noqa: BLE001 - routing origin is additive, never fatal
        pass
    return origin


def _public_title(record: Dict[str, Any]) -> str:
    """Return a bounded, single-line title safe for operator-facing status."""

    goal = record.get("goal")
    if not isinstance(goal, str):
        return ""
    first_line = next((line.strip() for line in goal.splitlines() if line.strip()), "")
    printable = "".join(ch for ch in first_line if ch.isprintable())
    return " ".join(printable.split())[:80]


def _persist_dispatch(record: Dict[str, Any]) -> bool:
    _LAST_PERSIST_REJECTION.set("")
    # Durable ownership follows the database selected at admission, not the
    # ambient home of the singleton stale-monitor thread that may settle it.
    record.setdefault("_state_home", str(_db_path().parent.resolve()))
    now = time.time()
    try:
        from gateway.status import get_process_start_time
        owner_started_at = get_process_start_time(__import__("os").getpid())
    except Exception:
        owner_started_at = None
    task_payload = {
        key: record.get(key)
        for key in (
            "goal", "goals", "tasks", "output_schemas", "context", "toolsets",
            "role", "model", "is_batch",
            # Routing origin (scope_id/user_id/user_name): persisted so a
            # restart-recovered completion can reconstruct a full
            # SessionSource — see _capture_routing_origin.
            "scope_id", "user_id", "user_name",
        )
        if key in record
    }
    task_json = _encode_durable_json(task_payload)
    if task_json is None:
        _LAST_PERSIST_REJECTION.set("payload_oversize")
        return False
    task_fingerprint = hashlib.sha256(task_json.encode("utf-8")).hexdigest()
    generation = int(record.get("execution_generation", 0) or 0)
    child_sessions = list(record.get("child_session_ids") or [])
    child_capabilities = list(record.get("child_capability_names") or [])
    output_fingerprints = list(record.get("output_schema_fingerprints") or [])
    child_sessions_json = _encode_durable_json(child_sessions)
    child_capabilities_json = _encode_durable_json(child_capabilities)
    output_fingerprints_json = _encode_durable_json(output_fingerprints)
    if None in (
        child_sessions_json,
        child_capabilities_json,
        output_fingerprints_json,
    ):
        _LAST_PERSIST_REJECTION.set("payload_oversize")
        return False
    planned_bytes = sum(
        len(value.encode("utf-8"))
        for value in (
            task_json,
            child_sessions_json,
            child_capabilities_json,
            output_fingerprints_json,
        )
    )
    with _DB_LOCK, _transaction() as conn:
        # Serialize admission across processes. The count/byte check and insert
        # are one SQLite write transaction, so two gateways cannot both claim
        # the final durable slot.
        if conn.in_transaction:
            conn.commit()  # finish idempotent schema migration work first
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT state, task_fingerprint, execution_generation "
            "FROM async_delegations WHERE delegation_id=?",
            (record["delegation_id"],),
        ).fetchone()
        if record.get("resume_claim"):
            if (
                not existing
                or existing[0] != "restarting"
                or str(existing[1] or "") != task_fingerprint
                or int(existing[2] or 0) != generation
            ):
                return False
            changed = conn.execute(
                """UPDATE async_delegations SET state='running', updated_at=?,
                   heartbeat_at=?, owner_pid=?, owner_started_at=?,
                   child_session_ids_json=?, child_capability_names_json=?,
                   restart_reason='', runner_returned=0
                   WHERE delegation_id=? AND state='restarting'""",
                (
                    now,
                    now,
                    __import__("os").getpid(),
                    owner_started_at,
                    child_sessions_json,
                    child_capabilities_json,
                    record["delegation_id"],
                ),
            )
            return changed.rowcount == 1
        if existing is not None:
            return False
        pending_count, pending_bytes = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(
                     LENGTH(CAST(COALESCE(task_json,'') AS BLOB))
                     + LENGTH(CAST(COALESCE(event_json,'') AS BLOB))
                     + LENGTH(CAST(COALESCE(result_json,'') AS BLOB))
                     + LENGTH(CAST(COALESCE(child_session_ids_json,'') AS BLOB))
                     + LENGTH(CAST(COALESCE(child_capability_names_json,'') AS BLOB))
                     + LENGTH(CAST(COALESCE(output_schema_fingerprints_json,'') AS BLOB))
                   ), 0)
               FROM async_delegations
               WHERE delivery_state != 'delivered'"""
        ).fetchone()
        if (
            int(pending_count or 0) >= int(_MAX_DURABLE_PENDING)
            or int(pending_bytes or 0) + planned_bytes
            > int(_MAX_DURABLE_PENDING_BYTES)
        ):
            _LAST_PERSIST_REJECTION.set("pending_capacity")
            return False
        conn.execute(
            """INSERT INTO async_delegations
               (delegation_id, origin_session, origin_ui_session_id,
                parent_session_id, state, dispatched_at, updated_at,
                delivery_state, delivery_attempts, owner_pid,
                owner_started_at, task_json, origin_session_id,
                public_title, heartbeat_at, status_revision)
               VALUES (?, ?, ?, ?, 'running', ?, ?, 'pending', 0, ?, ?, ?, ?, ?, ?, 1)""",
            (
                record["delegation_id"],
                record.get("session_key", ""),
                record.get("origin_ui_session_id", ""),
                record.get("parent_session_id"),
                record["dispatched_at"],
                now,
                __import__("os").getpid(),
                owner_started_at,
                task_json,
                record.get("origin_session_id", ""),
                _public_title(record),
                now,
            ),
        )
        conn.execute(
            """UPDATE async_delegations SET child_session_ids_json=?,
               child_capability_names_json=?, restart_policy=?, restart_count=?,
               restart_reason='', contract_version=?, task_fingerprint=?,
               restart_budget=?, execution_generation=?, child_count=?,
               output_schema_fingerprints_json=?
               WHERE delegation_id=?""",
            (
                child_sessions_json,
                child_capabilities_json,
                record.get("restart_policy", ""),
                int(record.get("restart_count", 0) or 0),
                _DELEGATION_CONTRACT_VERSION,
                task_fingerprint,
                int(record.get("restart_budget", _MAX_RESTART_ATTEMPTS) or 0),
                generation,
                len(child_sessions),
                output_fingerprints_json,
                record["delegation_id"],
            ),
        )
        for index, child_session_id in enumerate(child_sessions):
            names = child_capabilities[index] if index < len(child_capabilities) else []
            normalized_names = sorted({str(name) for name in names if str(name)})
            capability_json = json.dumps(normalized_names, separators=(",", ":"))
            capability_fingerprint = hashlib.sha256(
                capability_json.encode("utf-8")
            ).hexdigest()
            output_fingerprint = (
                str(output_fingerprints[index])
                if index < len(output_fingerprints)
                else ""
            )
            conn.execute(
                """INSERT INTO async_delegation_children(
                       delegation_id, child_index, child_session_id,
                       capability_names_json, capability_fingerprint,
                       output_schema_fingerprint, state, execution_generation,
                       updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)""",
                (
                    record["delegation_id"],
                    index,
                    str(child_session_id),
                    capability_json,
                    capability_fingerprint,
                    output_fingerprint,
                    generation,
                    now,
                ),
            )
    _prune_durable_records()
    return True


def _delete_durable_delegation(delegation_id: str) -> None:
    with _DB_LOCK, _transaction() as conn:
        conn.execute("DELETE FROM async_delegations WHERE delegation_id=?", (delegation_id,))


def _prune_durable_records() -> None:
    """Bound acknowledged history without deleting delivery obligations."""
    now = time.time()
    cutoff = now - _DURABLE_RETENTION_SECONDS
    deleted_spills: set[str] = set()
    with _DB_LOCK, _transaction() as conn:
        doomed = conn.execute(
            """SELECT delegation_id, event_json, result_json
               FROM async_delegations
               WHERE delivery_state='delivered' AND updated_at < ?""",
            (cutoff,),
        ).fetchall()
        for delegation_id, event_json, result_json in doomed:
            deleted_spills.update(_spill_paths_from_json(event_json))
            deleted_spills.update(_spill_paths_from_json(result_json))
            for (child_json,) in conn.execute(
                "SELECT result_json FROM async_delegation_children WHERE delegation_id=?",
                (delegation_id,),
            ).fetchall():
                deleted_spills.update(_spill_paths_from_json(child_json))
        conn.execute(
            "DELETE FROM async_delegations WHERE delivery_state='delivered' AND updated_at < ?",
            (cutoff,),
        )
        delivered_count = conn.execute(
            """SELECT COUNT(*) FROM async_delegations
               WHERE state NOT IN ('running','finalizing')
                 AND delivery_state='delivered'"""
        ).fetchone()[0]
        retained_count = conn.execute(
            """SELECT COUNT(*) FROM async_delegations
               WHERE state NOT IN ('running','finalizing')"""
        ).fetchone()[0]
        # Pending rows are obligations and are never pruning candidates, but
        # they still consume the retained-history budget. When the total is
        # above the cap, shed only acknowledged rows and leave every pending
        # row intact even if that means the database remains over budget.
        excess = min(
            delivered_count,
            max(0, retained_count - _MAX_RETAINED_COMPLETED),
        )
        if excess:
            excess_rows = conn.execute(
                """SELECT delegation_id, event_json, result_json
                   FROM async_delegations
                   WHERE state NOT IN ('running','finalizing')
                     AND delivery_state='delivered'
                   ORDER BY updated_at ASC LIMIT ?""",
                (excess,),
            ).fetchall()
            for delegation_id, event_json, result_json in excess_rows:
                deleted_spills.update(_spill_paths_from_json(event_json))
                deleted_spills.update(_spill_paths_from_json(result_json))
                for (child_json,) in conn.execute(
                    "SELECT result_json FROM async_delegation_children WHERE delegation_id=?",
                    (delegation_id,),
                ).fetchall():
                    deleted_spills.update(_spill_paths_from_json(child_json))
            conn.execute(
                """DELETE FROM async_delegations WHERE delegation_id IN (
                     SELECT delegation_id FROM async_delegations
                     WHERE state NOT IN ('running','finalizing')
                       AND delivery_state='delivered'
                     ORDER BY updated_at ASC LIMIT ?
                   )""",
                (excess,),
            )
        # Some legacy state databases were created with foreign-key
        # enforcement disabled on the pruning connection.  Do not let their
        # orphan child rows keep spill files reachable forever.
        conn.execute(
            """DELETE FROM async_delegation_children
               WHERE NOT EXISTS (
                 SELECT 1 FROM async_delegations parent
                 WHERE parent.delegation_id = async_delegation_children.delegation_id
               )"""
        )
    _cleanup_unreferenced_spills(immediate_candidates=deleted_spills)


def _record_parent_terminal_in_tx(
    conn: sqlite3.Connection,
    *,
    delegation_id: str,
    state: str,
    result: Dict[str, Any],
    now: float,
) -> None:
    has_barrier_schema = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='parent_task_children'"
    ).fetchone()
    if has_barrier_schema is None:
        return
    from tools.parent_task_barrier import record_child_terminal_in_tx

    record_child_terminal_in_tx(
        conn,
        task_id=str(delegation_id),
        state=str(state or "unknown"),
        result=result,
        now=now,
    )


def _persist_completion(event: Dict[str, Any], result: Dict[str, Any]) -> bool:
    """CAS one terminal outcome; lose cleanly to a concurrent restart defer."""
    now = time.time()
    generation = event.get("execution_generation")
    spill_path: Optional[str] = None
    try:
        spill_path = _bound_completion_payload(event, result)
        event_json = _encode_durable_json(event)
        result_json = _encode_durable_json(result)
        if event_json is None or result_json is None:
            raise ValueError("bounded completion payload could not be encoded")
        with _DB_LOCK, _transaction() as conn:
            if generation is None:
                row = conn.execute(
                    "SELECT execution_generation FROM async_delegations "
                    "WHERE delegation_id=?",
                    (event["delegation_id"],),
                ).fetchone()
                generation = int(row[0] or 0) if row is not None else -1
            changed = conn.execute(
                """UPDATE async_delegations SET state=?, completed_at=?, updated_at=?,
                   heartbeat_at=?, event_json=?, result_json=?, delivery_state='pending',
                   status_revision=status_revision+1
                   WHERE delegation_id=?
                     AND execution_generation=?
                     AND state IN ('running','stalling','finalizing')""",
                (
                    event.get("status", "completed"),
                    event.get("completed_at", now),
                    now,
                    now,
                    event_json,
                    result_json,
                    event["delegation_id"],
                    int(generation),
                ),
            ).rowcount
            if changed:
                # Parent closure atomically follows the authoritative terminal CAS.
                _record_parent_terminal_in_tx(
                    conn,
                    delegation_id=str(event["delegation_id"]),
                    state=str(event.get("status") or "unknown"),
                    result=result,
                    now=now,
                )
    except Exception:
        if spill_path:
            try:
                os.unlink(spill_path)
            except OSError:
                pass
        raise
    if not changed and spill_path:
        try:
            os.unlink(spill_path)
        except OSError:
            pass
    return bool(changed)


def _persist_completion_with_retry(
    event: Dict[str, Any], result: Dict[str, Any]
) -> bool:
    """Retry only transient SQLite contention around the terminal CAS.

    Each attempt re-enters ``_persist_completion`` and therefore re-runs the
    generation/state CAS.  If restart ownership wins while we are backing
    off, the next attempt returns ``False`` and no stale completion is
    published.
    """
    for attempt in range(_COMPLETION_PERSIST_RETRY_ATTEMPTS):
        try:
            return _persist_completion(event, result)
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            transient = "locked" in message or "busy" in message
            if not transient or attempt + 1 >= _COMPLETION_PERSIST_RETRY_ATTEMPTS:
                raise
            delay = _COMPLETION_PERSIST_RETRY_BASE_SECONDS * (2**attempt)
            logger.warning(
                "Async delegation %s terminal persistence hit transient "
                "SQLite contention; retrying in %.3fs (%d/%d): %s",
                event.get("delegation_id"),
                delay,
                attempt + 2,
                _COMPLETION_PERSIST_RETRY_ATTEMPTS,
                exc,
            )
            time.sleep(delay)
    raise AssertionError("unreachable completion persistence retry state")


def current_delegation_id() -> str:
    """Return the worker-bound delegation id; empty outside a retained child."""

    return _CURRENT_DELEGATION_ID.get()


def commit_child_terminal(
    child_index: int,
    result: Dict[str, Any],
    *,
    delegation_id: str = "",
    replay_decision: str = "executed",
    execution_generation: Optional[int] = None,
) -> bool:
    """Durably CAS one child result before aggregate completion is published."""

    delegation_id = delegation_id or current_delegation_id()
    if not delegation_id or int(child_index) < 0:
        return False
    generation = (
        _CURRENT_EXECUTION_GENERATION.get()
        if execution_generation is None
        else int(execution_generation)
    )
    if generation < 0:
        return False
    status = str(result.get("status") or "completed")
    state = {
        "error": "failed",
        "failed": "failed",
        "interrupted": "failed",
        "unknown": "unknown",
        "blocked_unknown_effect": "unknown",
        "cancelled": "cancelled",
    }.get(status, "completed")
    now = time.time()
    durable_result = dict(result)
    spill_path = _bound_completion_payload(
        {
            "type": "async_delegation_child",
            "delegation_id": delegation_id,
            "child_index": int(child_index),
            "status": status,
        },
        durable_result,
    )
    result_json = _encode_durable_json(durable_result)
    if result_json is None:
        return False
    with _DB_LOCK, _transaction() as conn:
        current = conn.execute(
            "SELECT execution_generation, state FROM async_delegations "
            "WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()
        if (
            current is None
            or int(current[0] or 0) != generation
            or str(current[1]) not in {"running", "stalling", "finalizing", "restarting"}
        ):
            if spill_path:
                try:
                    os.unlink(spill_path)
                except OSError:
                    pass
            return False
        changed = conn.execute(
            """UPDATE async_delegation_children
               SET state=?, result_json=?, replay_decision=?, completed_at=?,
                   updated_at=?
               WHERE delegation_id=? AND child_index=?
                 AND execution_generation=?
                 AND state IN ('running','restarting')""",
            (
                state,
                result_json,
                str(replay_decision or "executed"),
                now,
                now,
                delegation_id,
                int(child_index),
                generation,
            ),
        ).rowcount
    if not changed and spill_path:
        try:
            os.unlink(spill_path)
        except OSError:
            pass
    return bool(changed)


def _mark_runner_returned(
    delegation_id: str, execution_generation: Optional[int] = None
) -> bool:
    generation = (
        _CURRENT_EXECUTION_GENERATION.get()
        if execution_generation is None
        else int(execution_generation)
    )
    if generation < 0:
        return False
    with _DB_LOCK, _transaction() as conn:
        return bool(
            conn.execute(
                """UPDATE async_delegations SET runner_returned=1, updated_at=?
                   WHERE delegation_id=? AND execution_generation=?
                     AND state IN ('running','stalling','finalizing')""",
                (time.time(), delegation_id, generation),
            ).rowcount
        )


def _note_delivery_attempt(delegation_id: str) -> None:
    with _DB_LOCK, _transaction() as conn:
        conn.execute(
            "UPDATE async_delegations SET delivery_attempts=delivery_attempts+1, updated_at=? WHERE delegation_id=?",
            (time.time(), delegation_id),
        )


def restart_reason_is_eligible(reason: str) -> bool:
    """Host-only restart gate. No caller origin or model judgment participates."""
    return reason in {"gateway_drain", "dead_owner"}


def _new_restart_nonce() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def defer_restartable_interruption(delegation_id: str, reason: str) -> bool:
    if not restart_reason_is_eligible(reason):
        return False
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        changed = conn.execute(
            """UPDATE async_delegations SET state='restart_pending',
               restart_reason=?, restart_nonce=?, completed_at=NULL, event_json=NULL,
               result_json=NULL, updated_at=?, heartbeat_at=?
               WHERE delegation_id=? AND restart_policy=?
                 AND child_session_ids_json NOT IN ('', '[]')
                 AND restart_count < restart_budget
                 AND state IN ('running','stalling','finalizing')""",
            (
                reason,
                _new_restart_nonce(),
                now,
                now,
                delegation_id,
                _RESTART_POLICY,
            ),
        ).rowcount
    if changed:
        with _records_lock:
            record = _get_record_locked(delegation_id)
            if record is not None:
                record["status"] = "restart_pending"
        return True
    return False


def claim_restartable_delegation(
    delegation_id: str, *, owner_pid: int, owner_started_at: int,
    expected_session_key: str, restart_nonce: str,
) -> Optional[Dict[str, Any]]:
    """CAS one trusted persisted wake for its exact host-bound origin."""
    if not expected_session_key or not restart_nonce:
        return None
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        changed = conn.execute(
            """UPDATE async_delegations SET state='restarting', owner_pid=?,
               owner_started_at=?, restart_count=restart_count+1,
               restart_nonce='', updated_at=?,
               execution_generation=execution_generation+1
               WHERE delegation_id=? AND state='restart_pending'
                 AND restart_policy=? AND restart_count < restart_budget
                 AND origin_session=? AND restart_nonce=?""",
            (owner_pid, owner_started_at, now, delegation_id,
             _RESTART_POLICY, expected_session_key,
             restart_nonce),
        ).rowcount
        if not changed:
            return None
        row = conn.execute(
            """SELECT task_json, child_session_ids_json,
                      child_capability_names_json, restart_count,
                      parent_session_id, origin_session, origin_ui_session_id,
                      task_fingerprint, contract_version, execution_generation,
                      output_schema_fingerprints_json
               FROM async_delegations WHERE delegation_id=?""",
            (delegation_id,),
        ).fetchone()
        generation = int(row[9] or 0)
        conn.execute(
            """UPDATE async_delegation_children
               SET state='restarting', execution_generation=?,
                   replay_decision='', updated_at=?
               WHERE delegation_id=?
                 AND state NOT IN ('completed','failed','unknown','cancelled')""",
            (generation, now, delegation_id),
        )
        child_rows = conn.execute(
            """SELECT child_index, child_session_id, capability_names_json,
                      output_schema_fingerprint, state, result_json,
                      replay_decision
               FROM async_delegation_children WHERE delegation_id=?
               ORDER BY child_index""",
            (delegation_id,),
        ).fetchall()
    return {
        "delegation_id": delegation_id,
        "task": _load_durable_json(row[0], {}),
        "child_session_ids": _load_durable_json(row[1], []),
        "child_capability_names": _load_durable_json(row[2], []),
        "restart_count": row[3],
        "parent_session_id": row[4],
        "session_key": row[5],
        "origin_ui_session_id": row[6],
        "task_fingerprint": row[7],
        "contract_version": int(row[8] or 0),
        "execution_generation": generation,
        "output_schema_fingerprints": _load_durable_json(row[10], []),
        "children": [
            {
                "child_index": int(child[0]),
                "child_session_id": str(child[1] or ""),
                "capability_names": _load_durable_json(child[2], []),
                "output_schema_fingerprint": str(child[3] or ""),
                "state": str(child[4] or "unknown"),
                "result": _load_durable_json(child[5], {}),
                "replay_decision": str(child[6] or ""),
            }
            for child in child_rows
        ],
    }


def claim_restartable_delegations(*, owner_pid: int, owner_started_at: int) -> List[Dict[str, Any]]:
    """Compatibility bulk claimer; production wakes claim individually."""
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, origin_session, restart_nonce
               FROM async_delegations
               WHERE state='restart_pending' AND restart_policy=?
                 AND restart_count < restart_budget
               ORDER BY dispatched_at, delegation_id""",
            (_RESTART_POLICY,),
        ).fetchall()
    return [claim for delegation_id, session_key, restart_nonce in rows if (
        claim := claim_restartable_delegation(
            delegation_id,
            owner_pid=owner_pid,
            owner_started_at=owner_started_at,
            expected_session_key=str(session_key or ""),
            restart_nonce=str(restart_nonce or ""),
        )
    ) is not None]


def finalize_exhausted_restarts() -> int:
    """Publish one durable terminal error after the bounded retry budget."""
    now = time.time()
    finalized = 0
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, task_json, origin_session,
                      origin_ui_session_id, parent_session_id
               FROM async_delegations WHERE state='restart_pending'
                 AND restart_policy=? AND restart_count >= restart_budget""",
            (_RESTART_POLICY,),
        ).fetchall()
        for delegation_id, task_json, origin, origin_ui, parent_sid in rows:
            task = _load_durable_json(task_json, {})
            event = {
                "type": "async_delegation",
                "delegation_id": delegation_id,
                "status": "error",
                "task": task,
                "result": {
                    "error": "Delegation recovery exhausted its restart budget"
                },
                "session_key": origin,
                "origin_ui_session_id": origin_ui,
                "parent_session_id": parent_sid,
            }
            changed = conn.execute(
                """UPDATE async_delegations SET state='error', completed_at=?,
                   updated_at=?, event_json=?, result_json=?, restart_nonce=''
                   WHERE delegation_id=? AND state='restart_pending'
                     AND restart_count >= restart_budget""",
                (now, now, json.dumps(event), json.dumps(event["result"]),
                 delegation_id),
            ).rowcount
            if changed:
                _record_parent_terminal_in_tx(
                    conn,
                    delegation_id=str(delegation_id),
                    state="error",
                    result=event["result"],
                    now=now,
                )
            finalized += int(bool(changed))
    return finalized


def restore_restartable_delegations(target_queue, *, profile_name: str = "") -> int:
    """Restore persisted pending rows as trusted internal recovery wakes."""
    recover_abandoned_delegations()
    finalize_exhausted_restarts()
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, origin_session, origin_ui_session_id,
                      parent_session_id, restart_nonce FROM async_delegations
               WHERE state='restart_pending' AND restart_policy=?
                 AND restart_count < restart_budget
               ORDER BY dispatched_at, delegation_id""",
            (_RESTART_POLICY,),
        ).fetchall()
    for delegation_id, session_key, origin_ui, parent_sid, restart_nonce in rows:
        wake = TrustedRestartEvent({
            "type": "async_delegation_restart",
            "delegation_id": delegation_id,
            "session_key": session_key,
            "origin_ui_session_id": origin_ui,
            "parent_session_id": parent_sid,
            "restart_nonce": restart_nonce,
            "internal": True,
        })
        if profile_name:
            wake["profile"] = str(profile_name)
        target_queue.put(wake)
    return len(rows)


def release_restart_claim(delegation_id: str, reason: str) -> bool:
    if not restart_reason_is_eligible(reason):
        return False
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            """SELECT restart_count, restart_budget FROM async_delegations
               WHERE delegation_id=? AND state='restarting'""",
            (delegation_id,),
        ).fetchone()
        if row is None:
            return False
        changed = conn.execute(
            """UPDATE async_delegations SET state='restart_pending',
               restart_reason=?, restart_nonce=?, updated_at=? WHERE delegation_id=?
               AND state='restarting'""",
            (
                reason,
                _new_restart_nonce(),
                time.time(),
                delegation_id,
            ),
        ).rowcount
        return bool(changed and int(row[0]) < int(row[1]))


def finalize_unsafe_restart(delegation_id: str, error: str) -> bool:
    """Fail closed when persisted history contains an unknown side effect."""
    now = time.time()
    event: Dict[str, Any] | None = None
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            """SELECT task_json, origin_session, origin_ui_session_id,
                      parent_session_id, dispatched_at
               FROM async_delegations
               WHERE delegation_id=? AND state='restarting'""",
            (delegation_id,),
        ).fetchone()
        if row is None:
            return False
        task = _load_durable_json(row[0], {})
        result = {
            "status": "unknown",
            "summary": None,
            "error": error,
        }
        event = {
            "type": "async_delegation",
            "delegation_id": delegation_id,
            "status": "unknown",
            "task": task,
            "result": result,
            "session_key": row[1],
            "origin_ui_session_id": row[2],
            "parent_session_id": row[3],
            "dispatched_at": row[4],
            "completed_at": now,
        }
        changed = conn.execute(
            """UPDATE async_delegations SET state='unknown', completed_at=?,
               updated_at=?, heartbeat_at=?, event_json=?, result_json=?,
               delivery_state='pending', restart_nonce=''
               WHERE delegation_id=? AND state='restarting'""",
            (
                now,
                now,
                now,
                json.dumps(event),
                json.dumps(result),
                delegation_id,
            ),
        ).rowcount
        if changed:
            _record_parent_terminal_in_tx(
                conn,
                delegation_id=str(delegation_id),
                state="unknown",
                result=result,
                now=now,
            )
    if not changed or event is None:
        return False
    with _records_lock:
        _pop_record_locked(delegation_id)
    try:
        from tools.process_registry import process_registry

        process_registry.completion_queue.put(event)
    except Exception:
        logger.exception("Could not enqueue unsafe restart terminal")
    return True


def _interrupt_pending_restarts(
    reason: str,
    *,
    session_key: str = "",
    origin_ui_session_id: str = "",
    parent_session_id: str = "",
    all_rows: bool = False,
) -> set[str]:
    """Durably terminalize restart-enabled work before signalling children."""
    selectors: List[str] = []
    params: List[Any] = []
    for column, value in (
        ("origin_session", session_key),
        ("origin_ui_session_id", origin_ui_session_id),
        ("parent_session_id", parent_session_id),
    ):
        if value:
            selectors.append(f"{column}=?")
            params.append(value)
    if not all_rows and not selectors:
        return set()
    selector_sql = "" if all_rows else " AND (" + " OR ".join(selectors) + ")"
    now = time.time()
    events: List[Dict[str, Any]] = []
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, task_json, origin_session,
                      origin_ui_session_id, parent_session_id, dispatched_at
               FROM async_delegations
               WHERE restart_policy=?
                 AND state IN ('running','stalling','finalizing','restart_pending','restarting')"""
            + selector_sql,
            [_RESTART_POLICY, *params],
        ).fetchall()
        for delegation_id, task_json, origin, origin_ui, parent_sid, dispatched_at in rows:
            task = _load_durable_json(task_json, {})
            error = f"Retained work stopped before automatic recovery ({reason})"
            duration = round(max(0.0, now - float(dispatched_at or now)), 2)
            if task.get("is_batch"):
                result: Dict[str, Any] = {
                    "results": [],
                    "error": error,
                    "total_duration_seconds": duration,
                }
            else:
                result = {
                    "status": "interrupted",
                    "summary": None,
                    "error": error,
                    "api_calls": 0,
                    "duration_seconds": duration,
                    "exit_reason": "interrupted",
                }
            event = {
                "type": "async_delegation",
                "delegation_id": delegation_id,
                "status": "interrupted",
                "task": task,
                "result": result,
                "session_key": origin,
                "origin_ui_session_id": origin_ui,
                "parent_session_id": parent_sid,
                "dispatched_at": dispatched_at,
                "completed_at": now,
            }
            changed = conn.execute(
                """UPDATE async_delegations SET state='interrupted',
                   completed_at=?, updated_at=?, heartbeat_at=?, event_json=?,
                   result_json=?, delivery_state='pending', restart_reason=?,
                   restart_nonce=''
                   WHERE delegation_id=?
                     AND state IN ('running','stalling','finalizing','restart_pending','restarting')""",
                (
                    now,
                    now,
                    now,
                    json.dumps(event),
                    json.dumps(result),
                    reason,
                    delegation_id,
                ),
            ).rowcount
            if changed:
                _record_parent_terminal_in_tx(
                    conn,
                    delegation_id=str(delegation_id),
                    state="interrupted",
                    result=result,
                    now=now,
                )
                events.append(event)
    if not events:
        return set()
    with _records_lock:
        for event in events:
            _pop_record_locked(event["delegation_id"])
    try:
        from tools.process_registry import process_registry

        for event in events:
            process_registry.completion_queue.put(event)
    except Exception:
        logger.exception("Could not enqueue explicit restart cancellations")
    return {str(event["delegation_id"]) for event in events}


def recover_abandoned_delegations() -> int:
    """Recover restartable dead-owner work; terminalize all other outcomes."""
    try:
        from gateway.status import _pid_exists, get_process_start_time
    except Exception:
        return 0
    now = time.time()
    recovered = 0
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, state, origin_session, origin_ui_session_id,
                      parent_session_id, dispatched_at, owner_pid,
                      owner_started_at, task_json, origin_session_id,
                      runner_returned, restart_policy, restart_count,
                      restart_budget
               FROM async_delegations
               WHERE state IN ('running','stalling','finalizing','restarting')"""
        ).fetchall()
        for row in rows:
            (delegation_id, state, session_key, origin_ui, parent_id, dispatched_at,
             pid, started, task_json, origin_session_id, runner_returned,
             restart_policy, restart_count, restart_budget) = row
            live = False
            if pid:
                live = _pid_exists(int(pid))
                if live and started is not None:
                    live = get_process_start_time(int(pid)) == int(started)
            if live:
                continue
            if (
                state != "finalizing"
                and not bool(runner_returned)
                and task_json
                and restart_policy == _RESTART_POLICY
                and int(restart_count or 0) < int(restart_budget or 0)
            ):
                changed = conn.execute(
                    """UPDATE async_delegations SET state='restart_pending',
                       restart_reason='dead_owner', restart_nonce=?,
                       owner_pid=NULL, owner_started_at=NULL, updated_at=?,
                       heartbeat_at=?
                       WHERE delegation_id=?
                         AND state IN ('running','stalling','restarting')""",
                    (_new_restart_nonce(), now, now, delegation_id),
                ).rowcount
                recovered += int(bool(changed))
                if changed:
                    continue
            task = _load_durable_json(task_json, {})
            event = {
                "type": "async_delegation", "delegation_id": delegation_id,
                "session_key": session_key, "origin_ui_session_id": origin_ui,
                # Restore the durable wake target so completions recovered
                # after a restart remain routable to api_server sessions.
                "origin_session_id": origin_session_id or "",
                "parent_session_id": parent_id, "goal": task.get("goal", ""),
                "goals": task.get("goals"), "context": task.get("context"),
                "toolsets": task.get("toolsets"), "role": task.get("role"),
                "model": task.get("model"), "is_batch": bool(task.get("is_batch")),
                "status": "unknown", "summary": None,
                "error": "Delegation owner exited before recording a terminal result; outcome unknown.",
                "dispatched_at": dispatched_at, "completed_at": now,
            }
            # Routing origin persisted at dispatch (see _capture_routing_origin):
            # restores scope_id/user_id for the reconstructed SessionSource so
            # relay egress priming works after a restart.
            for _k in ("scope_id", "user_id", "user_name"):
                if task.get(_k):
                    event[_k] = task[_k]
            result = {"status": "unknown", "summary": None, "error": event["error"]}
            changed = conn.execute(
                """UPDATE async_delegations SET state='unknown', completed_at=?,
                   updated_at=?, heartbeat_at=?, event_json=?, result_json=?,
                   delivery_state='pending', status_revision=status_revision+1,
                   restart_nonce=''
                   WHERE delegation_id=?""",
                (now, now, now, json.dumps(event), json.dumps(result), delegation_id),
            ).rowcount
            if changed:
                _record_parent_terminal_in_tx(
                    conn,
                    delegation_id=str(delegation_id),
                    state="unknown",
                    result=result,
                    now=now,
                )
                recovered += 1
    return recovered


def restore_undelivered_completions(target_queue, *, profile_name: str = "") -> int:
    """Enqueue durable pending completions as fresh turns after process start.

    Every restored event is stamped ``restored=True`` (in-memory only — the
    stamp is added after the durable payload is deserialized and is never
    persisted). Restored events originate from a *previous* process, so no
    consumer in THIS process implicitly owns them: drain paths that run
    without an ownership filter (the legacy single-session behavior) must
    leave them queued for a consumer that can positively prove ownership,
    otherwise a brand-new session adopts a dead session's delegation
    results seconds after boot (#64484).

    Pending completion rows are delivery obligations. Age and count pruning
    never discard them; the explicit delivery-attempt policy owns any eventual
    terminal disposition.
    """
    recover_abandoned_delegations()
    restored = 0
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, event_json, completed_at, dispatched_at,
                      wire_accepted_at
               FROM async_delegations
               WHERE state != 'running' AND delivery_state='pending' AND event_json IS NOT NULL
               ORDER BY completed_at, delegation_id"""
        ).fetchall()
        for delegation_id, payload, completed_at, dispatched_at, wire_accepted_at in rows:
            valid, evt, reason = _decode_durable_json(payload, {})
            if valid and str(evt.get("delegation_id") or "") != str(delegation_id):
                valid = False
                reason = "event_identity_mismatch"
            if valid and evt.get("type") not in (None, "", "async_delegation"):
                valid = False
                reason = "event_type_mismatch"
            if valid:
                valid, spill_reason = _validate_payload_spill(evt)
                if not valid:
                    reason = spill_reason
            if not valid:
                changed = conn.execute(
                    """UPDATE async_delegations
                       SET delivery_state='quarantined', quarantine_reason=?,
                           delivery_claim=NULL, delivery_claimed_at=NULL,
                           updated_at=?
                       WHERE delegation_id=? AND delivery_state='pending'""",
                    (reason[:80], time.time(), delegation_id),
                ).rowcount
                if changed:
                    logger.error(
                        "Async delegation %s quarantined during cold restore: %s",
                        str(delegation_id)[:80],
                        reason[:80],
                    )
                continue
            evt["type"] = "async_delegation"
            evt["restored"] = True
            if wire_accepted_at is not None:
                evt["_wire_accepted"] = True
            if profile_name:
                evt["profile"] = str(profile_name)
            target_queue.put(evt)
            restored += 1
    return restored


def mark_completion_delivered(delegation_id: str) -> bool:
    """Atomically acknowledge successful injection of a durable completion."""
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        cur = conn.execute(
            """UPDATE async_delegations SET delivery_state='delivered', delivered_at=?, updated_at=?
               WHERE delegation_id=? AND delivery_state!='delivered'""",
            (now, now, delegation_id),
        )
        return cur.rowcount == 1


def claim_completion_delivery(delegation_id: str, claim_id: str) -> bool:
    """Claim one pending completion across competing consumers/processes."""
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            "SELECT delivery_state FROM async_delegations WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()
        if row is None:
            return True  # legacy event created before durable dispatch
        cur = conn.execute(
            """UPDATE async_delegations SET delivery_claim=?, delivery_claimed_at=?,
                      delivery_attempts=delivery_attempts+1, updated_at=?
               WHERE delegation_id=? AND delivery_state='pending'
                 AND (delivery_claim IS NULL OR delivery_claimed_at < ?)""",
            (claim_id, now, now, delegation_id, now - 300),
        )
        return cur.rowcount == 1


def claim_event_delivery(evt: Dict[str, Any], consumer: str) -> Optional[str]:
    """Claim a durable delegation event; non-durable events need no token."""
    if evt.get("type") != "async_delegation":
        return ""
    delegation_id = str(evt.get("delegation_id") or "")
    if not delegation_id:
        return ""
    claim_id = f"{consumer}:{__import__('os').getpid()}:{uuid.uuid4().hex}"
    return claim_id if claim_completion_delivery(delegation_id, claim_id) else None


def mark_completion_wire_accepted(delegation_id: str, claim_id: str) -> bool:
    """Persist that transport accepted the payload before DB settlement.

    A row with this marker is a DB-only obligation: cold recovery may finish
    its exact claim, but must never replay the user-facing wire effect.
    """
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        changed = conn.execute(
            """UPDATE async_delegations SET wire_accepted_at=COALESCE(wire_accepted_at, ?),
                      updated_at=?
               WHERE delegation_id=? AND delivery_state='pending'
                 AND delivery_claim=?""",
            (now, now, delegation_id, claim_id),
        ).rowcount
        return changed == 1


def release_completion_delivery(delegation_id: str, claim_id: str) -> bool:
    """Release a failed delivery claim so another consumer may retry.

    Attempts are counted at claim time, so a row that keeps being claimed and
    released has burned real delivery attempts. Once the budget is exhausted
    the row converges to a terminal ``dropped`` state instead of returning to
    ``pending`` — otherwise an undeliverable completion replays on every
    gateway restart forever (restore_undelivered_completions only restores
    pending rows).
    """
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        capped = conn.execute(
            """UPDATE async_delegations SET delivery_state='dropped',
                      delivery_claim=NULL, delivery_claimed_at=NULL, updated_at=?
               WHERE delegation_id=? AND delivery_state='pending'
                 AND delivery_claim=? AND delivery_attempts>=?
                 AND wire_accepted_at IS NULL""",
            (now, delegation_id, claim_id, _MAX_DELIVERY_ATTEMPTS),
        )
        if capped.rowcount == 1:
            logger.warning(
                "Async delegation %s exhausted its %d delivery attempts; "
                "marking terminally dropped (result remains queryable).",
                delegation_id, _MAX_DELIVERY_ATTEMPTS,
            )
            return True
        cur = conn.execute(
            """UPDATE async_delegations SET delivery_claim=NULL,
                      delivery_claimed_at=NULL, updated_at=?
               WHERE delegation_id=? AND delivery_state='pending'
                 AND delivery_claim=?""",
            (now, delegation_id, claim_id),
        )
        return cur.rowcount == 1


def drop_completion_delivery(delegation_id: str, claim_id: str) -> bool:
    """Terminally drop a claimed completion that can never be delivered.

    Used when the delivery target is permanently gone — the spawning session
    ended at an explicit user boundary (/new, reset) rather than a compression
    rotation. Marking the row ``dropped`` (not ``delivered``) keeps the ack
    honest, and (not ``pending``) keeps restart recovery from replaying a
    completion that will be fail-closed dropped again every time.
    """
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        cur = conn.execute(
            """UPDATE async_delegations SET delivery_state='dropped',
                      updated_at=?, delivery_claim=NULL,
                      delivery_claimed_at=NULL
               WHERE delegation_id=? AND delivery_state='pending'
                 AND delivery_claim=?""",
            (now, delegation_id, claim_id),
        )
        return cur.rowcount == 1


def complete_completion_delivery(delegation_id: str, claim_id: str) -> bool:
    """Acknowledge acceptance for the consumer holding this claim."""
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        cur = conn.execute(
            """UPDATE async_delegations SET delivery_state='delivered',
                      delivered_at=?, updated_at=?, delivery_claim=NULL,
                      delivery_claimed_at=NULL
               WHERE delegation_id=? AND delivery_state='pending'
                 AND delivery_claim=?""",
            (now, now, delegation_id, claim_id),
        )
        return cur.rowcount == 1


def complete_event_delivery(evt: Dict[str, Any], claim_id: str) -> None:
    if claim_id and evt.get("type") == "async_delegation":
        complete_completion_delivery(str(evt.get("delegation_id") or ""), claim_id)


def completion_delivery_disposition(delegation_id: str) -> str:
    """Classify a durable completion without exposing payload or route."""

    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            """SELECT delivery_state, delivery_claim, delivery_claimed_at
               FROM async_delegations WHERE delegation_id=?""",
            (delegation_id,),
        ).fetchone()
    if row is None:
        return "legacy"
    state = str(row[0] or "")
    if state != "pending":
        return state
    if row[1] and float(row[2] or 0) >= now - 300:
        return "claimed"
    return "pending"


def release_event_delivery(evt: Dict[str, Any], claim_id: str) -> None:
    if claim_id and evt.get("type") == "async_delegation":
        release_completion_delivery(str(evt.get("delegation_id") or ""), claim_id)


def get_durable_delegation(delegation_id: str) -> Optional[Dict[str, Any]]:
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            """SELECT origin_session, state, dispatched_at, completed_at,
                      result_json, delivery_state, delivery_attempts,
                      origin_session_id
               FROM async_delegations WHERE delegation_id=?""", (delegation_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "delegation_id": delegation_id, "origin_session": row[0], "state": row[1],
        "dispatched_at": row[2], "completed_at": row[3],
        "result": _load_durable_json(row[4], {}) if row[4] else None,
        "delivery_state": row[5], "delivery_attempts": row[6],
        "origin_session_id": row[7] or "",
    }


def _get_executor(max_workers: int) -> ThreadPoolExecutor:
    """Lazily create (or grow) the shared daemon executor.

    We never shrink — ThreadPoolExecutor can't resize — but if the configured
    cap grows between calls we rebuild a larger pool. Existing in-flight
    futures keep running on the old pool until it's garbage collected.
    """
    global _executor, _executor_max_workers
    with _executor_lock:
        if _executor is None or max_workers > _executor_max_workers:
            # Daemon threads: thread_name_prefix aids debugging in stack dumps.
            _executor = _DaemonThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="async-delegate",
            )
            _executor_max_workers = max_workers
        return _executor


def active_count() -> int:
    """Number of async delegation UNITS currently running.

    A unit is one dispatch: a single subagent OR a whole fan-out batch. A batch
    counts as ONE here because it occupies one async-pool slot (the capacity
    semantics ``dispatch_async_delegation_batch`` relies on). For the count of
    actual concurrent child subagents (batch expanded), use
    ``active_task_count()``.
    """
    with _records_lock:
        return sum(
            1 for r in _records.values()
            if r.get("status") in {"running", "stalling", "finalizing"}
        )


def active_for_session(origin_ui_session_id: str) -> int:
    """Number of live async delegations owned by one UI session."""
    if not origin_ui_session_id:
        return 0
    with _records_lock:
        return sum(
            1
            for r in _records.values()
            if r.get("status") in {"running", "stalling", "finalizing"}
            and str(r.get("origin_ui_session_id") or "")
            == origin_ui_session_id
        )


def active_task_count() -> int:
    """Number of async delegation TASKS (child subagents) currently running.

    Unlike ``active_count()`` (units/slots), this expands a batch to its child
    count: a running batch of N tasks contributes N, a single subagent
    contributes 1. This is the truthful "how many subagents are actually
    working right now" figure for observability, where a 3-task batch shown as
    "1" undercounts real concurrent work. Falls back to counting a batch as 1
    if its goal list is missing.
    """
    with _records_lock:
        total = 0
        for r in _records.values():
            if r.get("status") not in {"running", "finalizing"}:
                continue
            if r.get("is_batch"):
                goals = r.get("goals")
                total += len(goals) if isinstance(goals, (list, tuple)) and goals else 1
            else:
                total += 1
        return total


def _matches_session_selectors(
    record: Dict[str, Any],
    *,
    session_key: str = "",
    origin_ui_session_id: str = "",
    parent_session_id: str = "",
) -> bool:
    return (
        (origin_ui_session_id and str(record.get("origin_ui_session_id") or "") == origin_ui_session_id)
        or (session_key and str(record.get("session_key") or "") == session_key)
        or (parent_session_id and str(record.get("parent_session_id") or "") == parent_session_id)
    )


def has_live_for_session(
    session_key: str = "",
    origin_ui_session_id: str = "",
    parent_session_id: str = "",
) -> bool:
    """Whether a session still owns any live async delegation.

    Live = running / stalling / finalizing — the same states the reapers'
    keepalive treats as active work.
    """
    if not session_key and not origin_ui_session_id and not parent_session_id:
        return False
    with _records_lock:
        return any(
            r.get("status") in {"running", "stalling", "finalizing"}
            and _matches_session_selectors(
                r,
                session_key=session_key,
                origin_ui_session_id=origin_ui_session_id,
                parent_session_id=parent_session_id,
            )
            for r in _records.values()
        )


def _new_delegation_id() -> str:
    return f"deleg_{uuid.uuid4().hex[:8]}"


def _prune_completed_locked() -> None:
    """Drop the oldest completed records beyond the retention cap.

    Caller must hold ``_records_lock``.
    """
    completed = [
        (rid, r)
        for rid, r in _records.items()
        if r.get("status") != "running"
    ]
    if len(completed) <= _MAX_RETAINED_COMPLETED:
        return
    # Oldest-first by completion time (fall back to dispatch time).
    completed.sort(key=lambda kv: kv[1].get("completed_at") or kv[1].get("dispatched_at") or 0)
    for rid, _ in completed[: len(completed) - _MAX_RETAINED_COMPLETED]:
        _records.pop(rid, None)


def _current_origin_session_id() -> str:
    """Raw session id of the ORIGINATING api_server request, or ``""``.

    The obvious source — ``HERMES_SESSION_ID`` via ``get_session_env`` — is
    NOT safe to read at dispatch time: constructing a child agent
    (``agent/agent_init.py``) calls ``set_current_session_id(child.session_id)``,
    clobbering that ContextVar *and* ``os.environ`` with the subagent's
    internal ``{timestamp}_{uuid}`` id moments before the dispatch code reads
    it, so the completion wake would self-post into the subagent's own
    (unread) session instead of the spawner's.

    The request-scoped ``HERMES_SESSION_CHAT_ID`` binding survives child
    construction: ``_bind_api_server_session`` binds ``chat_id`` to the raw
    ``X-Hermes-Session-Id``, and its only writer is ``set_session_vars`` —
    ``set_current_session_id`` never touches it. Gate on the platform: on
    push platforms ``chat_id`` is a chat, not a session, so yield ``""``
    there.
    """
    try:
        from gateway.session_context import get_session_env

        if get_session_env("HERMES_SESSION_PLATFORM", "") != "api_server":
            return ""
        return get_session_env("HERMES_SESSION_CHAT_ID", "") or ""
    except Exception:
        return ""


def dispatch_async_delegation(
    *,
    goal: str,
    context: Optional[str],
    toolsets: Optional[List[str]],
    role: str,
    model: Optional[str],
    session_key: str,
    parent_session_id: Optional[str] = None,
    root_turn_id: str = "",
    existing_parent_barrier_id: str = "",
    runner: Callable[[], Dict[str, Any]],
    origin_ui_session_id: str = "",
    origin_session_id: str = "",
    interrupt_fn: Optional[Callable[[], None]] = None,
    max_async_children: int = _DEFAULT_MAX_ASYNC_CHILDREN,
    progress_fn: Optional[Callable[[], tuple]] = None,
    child_session_ids: Optional[List[str]] = None,
    child_capability_names: Optional[List[List[str]]] = None,
    restart_policy: str = "",
) -> Dict[str, Any]:
    """Spawn ``runner`` on the daemon executor and return a handle immediately.

    Parameters
    ----------
    goal, context, toolsets, role, model
        The dispatch-time task spec, captured verbatim for the rich
        completion block.
    session_key
        The gateway session_key (from ``tools.approval.get_current_session_key``)
        captured on the parent thread BEFORE dispatch, because the daemon
        worker thread won't carry the contextvar. Used to route the
        completion back to the originating session.
    parent_session_id
        The durable ``state.db`` session id of the parent agent that spawned
        the delegation. Carried on the completion event so the gateway can
        pin routing to the spawning session instead of recovering the latest
        ``ended_at IS NULL`` row for the peer tuple (#57498).
    runner
        Zero-arg callable that builds + runs the child and returns the same
        result dict ``_run_single_child`` produces. Runs on the worker thread.
    interrupt_fn
        Optional callable to signal the child to stop (used on shutdown /
        explicit cancel).
    progress_fn
        Optional zero-arg callable returning ``(token, in_tool)`` where
        ``token`` is any comparable snapshot of the child's progress (api
        call count + current tool) and ``in_tool`` says whether the child is
        currently inside a tool call. Sampled by the stale monitor; a frozen
        token past the stale threshold marks the delegation stuck (see the
        stale-detection block at the top of this module). When omitted, the
        delegation is not monitored.
    max_async_children
        Concurrency cap. When at capacity the dispatch is REJECTED (the caller
        should fall back to sync or tell the user) rather than queued, so a
        runaway model can't pile up unbounded background work.

    Returns
    -------
    dict
        ``{"status": "dispatched", "delegation_id": ...}`` on success, or
        ``{"status": "rejected", "error": ...}`` when at capacity.
    """
    delegation_id = _new_delegation_id()
    dispatched_at = time.time()
    record: Dict[str, Any] = {
        "delegation_id": delegation_id,
        "goal": goal,
        "context": context,
        "toolsets": list(toolsets) if toolsets else None,
        "role": role,
        "model": model,
        "session_key": session_key,
        "origin_ui_session_id": origin_ui_session_id,
        "origin_session_id": origin_session_id,
        "parent_session_id": parent_session_id,
        **_capture_routing_origin(),
        "child_session_ids": list(child_session_ids or []),
        "child_capability_names": [
            sorted({str(name) for name in names if str(name)})
            for names in (child_capability_names or [])
        ],
        "restart_policy": restart_policy,
        "status": "running",
        "dispatched_at": dispatched_at,
        "completed_at": None,
        "interrupt_fn": interrupt_fn,
        "progress_fn": progress_fn,
        # Stale-monitor bookkeeping (see _stale_monitor_loop).
        "_progress_token": None,
        "_progress_ts": dispatched_at,
        "_interrupted_at": None,
    }
    # Capacity check and record insert under ONE lock hold — checking
    # active_count() separately would let two concurrent dispatches (e.g.
    # from different gateway sessions) both pass the check and exceed the cap.
    with _records_lock:
        running = sum(
            1 for r in _records.values()
            if r.get("status") in ("running", "stalling")
        )
        if running >= max_async_children:
            return {
                "status": "rejected",
                "error": (
                    f"Async delegation capacity reached ({max_async_children} "
                    f"running). Wait for one to finish (its result will re-enter "
                    f"the chat), or run this task synchronously "
                    f"(background=false). Raise delegation.max_concurrent_children in "
                    f"config.yaml to allow more concurrent background subagents."
                ),
            }
        record["_state_home"] = str(_db_path().parent.resolve())
        _store_record_locked(delegation_id, record)

    if not _persist_dispatch(record):
        with _records_lock:
            _pop_record_locked(delegation_id)
        return {
            "status": "rejected",
            "error": _persist_rejection_error(
                "Async delegation dispatch could not be persisted"
            ),
        }
    if root_turn_id:
        try:
            from tools.parent_task_barrier import admit_required_child

            admit_required_child(
                origin_session=session_key or str(parent_session_id or ""),
                parent_session_id=str(parent_session_id or ""),
                root_turn_id=root_turn_id,
                task_id=delegation_id,
                existing_barrier_id=existing_parent_barrier_id,
            )
        except Exception:
            with _records_lock:
                _pop_record_locked(delegation_id)
            _delete_durable_delegation(delegation_id)
            raise
    executor = _get_executor(max_async_children)

    def _worker() -> None:
        result: Dict[str, Any] = {}
        status = "error"
        context_token = _CURRENT_DELEGATION_ID.set(delegation_id)
        generation_token = _CURRENT_EXECUTION_GENERATION.set(
            int(record.get("execution_generation", 0) or 0)
        )
        try:
            result = runner() or {}
            commit_child_terminal(0, result, delegation_id=delegation_id)
            _mark_runner_returned(delegation_id)
            status = result.get("status") or "completed"
        except Exception as exc:  # noqa: BLE001 — must never crash the worker
            logger.exception("Async delegation %s crashed", delegation_id)
            result = {
                "status": "error",
                "summary": None,
                "error": f"{type(exc).__name__}: {exc}",
                "api_calls": 0,
                "duration_seconds": round(time.time() - dispatched_at, 2),
            }
            commit_child_terminal(
                0,
                result,
                delegation_id=delegation_id,
                replay_decision="runner_exception",
            )
            _mark_runner_returned(delegation_id)
            status = "error"
        finally:
            try:
                _finalize(delegation_id, result, status)
            finally:
                _CURRENT_EXECUTION_GENERATION.reset(generation_token)
                _CURRENT_DELEGATION_ID.reset(context_token)

    try:
        # Propagate the dispatching profile so the detached child resolves
        # get_hermes_home() under the right profile.
        executor.submit(propagate_context_to_thread(_worker))
    except Exception as exc:  # pragma: no cover — pool submit failure is rare
        if root_turn_id:
            _finalize(
                delegation_id,
                {
                    "status": "error",
                    "summary": None,
                    "error": f"Failed to schedule async delegation: {exc}",
                    "api_calls": 0,
                },
                "error",
            )
        else:
            with _records_lock:
                _pop_record_locked(delegation_id)
            _delete_durable_delegation(delegation_id)
        return {
            "status": "rejected",
            "error": f"Failed to schedule async delegation: {exc}",
        }
    if progress_fn is not None:
        _ensure_stale_monitor()

    logger.info(
        "Dispatched async delegation %s (session_key=%s): %s",
        delegation_id, session_key or "<cli>", (goal or "")[:80],
    )
    return {"status": "dispatched", "delegation_id": delegation_id}


def _finalize(delegation_id: str, result: Dict[str, Any], status: str) -> None:
    """Mark a record complete and push the completion event onto the queue."""
    claimed = _begin_finalization(delegation_id)
    if claimed is None:
        return
    event_record, _interrupt_fn = claimed

    if status == "interrupted" and defer_restartable_interruption(
        delegation_id, str(event_record.get("_interrupt_reason") or "")
    ):
        return
    persisted = _push_completion_event(event_record, result, status)
    _finish_finalization(
        delegation_id,
        status if persisted else "superseded",
        expected_generation=int(event_record.get("execution_generation", 0) or 0),
    )


def _begin_finalization(
    delegation_id: str,
    expected_generation: Optional[int] = None,
) -> Optional[tuple[Dict[str, Any], Optional[Callable[[], None]]]]:
    """Atomically claim terminal delivery while keeping the record active."""
    if expected_generation is None:
        worker_generation = _CURRENT_EXECUTION_GENERATION.get()
        expected_generation = worker_generation if worker_generation >= 0 else None
    with _records_lock:
        record = _get_record_locked(delegation_id)
        if (
            record is None
            or record.get("status") not in ("running", "stalling")
            or (
                expected_generation is not None
                and int(record.get("execution_generation", 0) or 0)
                != int(expected_generation)
            )
        ):
            return
        # Stay active until durable persistence and queue publication finish;
        # otherwise process shutdown can kill this daemon worker in the narrow
        # gap after status flips but before SQLite is committed.
        record["status"] = "finalizing"
        record["completed_at"] = time.time()
        interrupt_fn = record.get("interrupt_fn")
        record["interrupt_fn"] = None  # drop the closure; child is done
        record["progress_fn"] = None  # stop stale-monitor sampling
        event_record = dict(record)

    return event_record, interrupt_fn


def _finish_finalization(
    delegation_id: str,
    status: str,
    *,
    expected_generation: Optional[int] = None,
) -> None:
    with _records_lock:
        record = _get_record_locked(delegation_id)
        if record is not None and (
            expected_generation is None
            or int(record.get("execution_generation", 0) or 0)
            == int(expected_generation)
        ):
            record["status"] = status
        _prune_completed_locked()


def _push_completion_event(
    record: Dict[str, Any], result: Dict[str, Any], status: str
) -> bool:
    """Push a type='async_delegation' event onto the shared completion queue.

    Best-effort: a failure here must not crash the worker, but it WOULD mean a
    silently-lost result, so we log loudly.
    """
    summary = result.get("summary")
    error = result.get("error")
    dispatched_at = record.get("dispatched_at") or time.time()
    completed_at = record.get("completed_at") or time.time()

    evt = {
        "type": "async_delegation",
        "delegation_id": record.get("delegation_id"),
        # session_key routes the completion back to the originating gateway
        # session; empty string => CLI (single-session) path.
        "session_key": record.get("session_key", ""),
        "origin_ui_session_id": record.get("origin_ui_session_id", ""),
        "origin_session_id": record.get("origin_session_id", ""),
        "parent_session_id": record.get("parent_session_id"),
        "goal": record.get("goal", ""),
        "context": record.get("context"),
        "toolsets": record.get("toolsets"),
        "role": record.get("role"),
        "model": result.get("model") or record.get("model"),
        "status": status,
        "execution_generation": int(record.get("execution_generation", 0) or 0),
        "summary": summary,
        "error": error,
        "api_calls": result.get("api_calls", 0),
        "duration_seconds": result.get(
            "duration_seconds", round(completed_at - dispatched_at, 2)
        ),
        "dispatched_at": dispatched_at,
        "completed_at": completed_at,
        "exit_reason": result.get("exit_reason"),
    }
    # Routing origin captured at dispatch (see _capture_routing_origin):
    # additive, lets the gateway reconstruct a full SessionSource (incl.
    # scope_id for relay tenant egress) when its own caches are cold.
    for _k in ("scope_id", "user_id", "user_name"):
        if record.get(_k):
            evt[_k] = record[_k]
    # Structured stall metadata (#51690) — additive, present only on
    # stall-monitor finalizations.
    for _k in (
        "stalled_after_quiet_seconds",
        "stall_threshold_seconds",
        "stall_phase",
        "stall_grace_seconds",
    ):
        if _k in result:
            evt[_k] = result[_k]
    if not _persist_completion_with_retry(evt, result):
        return False
    try:
        from tools.process_registry import process_registry

        process_registry.completion_queue.put(evt)
    except Exception as exc:  # pragma: no cover
        logger.error(
            "Async delegation %s: failed to enqueue completion event; "
            "durable replay retained: %s",
            record.get("delegation_id"), exc,
        )
        return True
    return True


def dispatch_async_delegation_batch(
    *,
    goals: List[str],
    context: Optional[str],
    toolsets: Optional[List[str]],
    role: str,
    model: Optional[str],
    session_key: str,
    parent_session_id: Optional[str] = None,
    root_turn_id: str = "",
    existing_parent_barrier_id: str = "",
    runner: Callable[[], Dict[str, Any]],
    origin_ui_session_id: str = "",
    origin_session_id: str = "",
    interrupt_fn: Optional[Callable[[], None]] = None,
    max_async_children: int = _DEFAULT_MAX_ASYNC_CHILDREN,
    delegation_id: Optional[str] = None,
    progress_fn: Optional[Callable[[], tuple]] = None,
    child_session_ids: Optional[List[str]] = None,
    child_capability_names: Optional[List[List[str]]] = None,
    task_specs: Optional[List[Dict[str, Any]]] = None,
    output_schemas: Optional[List[Optional[Dict[str, Any]]]] = None,
    output_schema_fingerprints: Optional[List[str]] = None,
    restart_policy: str = "",
    resume_claim: bool = False,
    execution_generation: int = 0,
) -> Dict[str, Any]:
    """Dispatch a WHOLE fan-out batch as ONE background unit.

    Unlike ``dispatch_async_delegation`` (which backs a single subagent),
    ``runner`` here runs the entire batch — it builds and joins on every child
    in parallel and returns the combined ``{"results": [...],
    "total_duration_seconds": N}`` dict that the synchronous path would have
    returned. We occupy ONE async slot for the whole batch (the in-batch
    parallelism is bounded separately by ``max_concurrent_children``), so a
    single ``delegate_task`` fan-out never exhausts the async pool by itself.

    When the batch finishes, a SINGLE completion event is pushed onto the
    shared ``process_registry.completion_queue`` carrying the full per-task
    ``results`` list, so the consolidated summaries re-enter the conversation
    as one message once every child is done — the chat is never blocked while
    they run.

    Returns ``{"status": "dispatched", "delegation_id": ...}`` on success or
    ``{"status": "rejected", "error": ...}`` when the async pool is at
    capacity.
    """
    delegation_id = delegation_id or _new_delegation_id()
    dispatched_at = time.time()
    n = len(goals)
    # A combined goal label for status listings / the completion header.
    combined_goal = (
        goals[0] if n == 1 else f"{n} parallel subagents: " + "; ".join(g[:40] for g in goals)
    )
    record: Dict[str, Any] = {
        "delegation_id": delegation_id,
        "goal": combined_goal,
        "goals": list(goals),
        "tasks": list(task_specs or []),
        "output_schemas": list(output_schemas or []),
        "context": context,
        "toolsets": list(toolsets) if toolsets else None,
        "role": role,
        "model": model,
        "session_key": session_key,
        "origin_ui_session_id": origin_ui_session_id,
        "origin_session_id": origin_session_id,
        "parent_session_id": parent_session_id,
        **_capture_routing_origin(),
        "child_session_ids": list(child_session_ids or []),
        "child_capability_names": [
            sorted({str(name) for name in names if str(name)})
            for names in (child_capability_names or [])
        ],
        "restart_policy": restart_policy,
        "resume_claim": resume_claim,
        "execution_generation": int(execution_generation or 0),
        "output_schema_fingerprints": list(output_schema_fingerprints or []),
        "status": "running",
        "dispatched_at": dispatched_at,
        "completed_at": None,
        "interrupt_fn": interrupt_fn,
        "is_batch": True,
        "progress_fn": progress_fn,
        "_progress_token": None,
        "_progress_ts": dispatched_at,
        "_interrupted_at": None,
    }
    with _records_lock:
        if _get_record_locked(delegation_id) is not None:
            return {
                "status": "rejected",
                "error": "Delegation id is already active or retained",
            }
        running = sum(
            1 for r in _records.values()
            if r.get("status") in ("running", "stalling")
        )
        if running >= max_async_children:
            return {
                "status": "rejected",
                "error": (
                    f"Async delegation capacity reached ({max_async_children} "
                    f"running). Wait for one to finish (its result will re-enter "
                    f"the chat), or raise delegation.max_concurrent_children in "
                    f"config.yaml to allow more concurrent background units."
                ),
            }
        record["_state_home"] = str(_db_path().parent.resolve())
        _store_record_locked(delegation_id, record)

    if not _persist_dispatch(record):
        with _records_lock:
            _pop_record_locked(delegation_id)
        return {
            "status": "rejected",
            "error": _persist_rejection_error(
                "Restart claim is no longer active"
            ),
        }
    if root_turn_id:
        try:
            from tools.parent_task_barrier import admit_required_child

            admit_required_child(
                origin_session=session_key or str(parent_session_id or ""),
                parent_session_id=str(parent_session_id or ""),
                root_turn_id=root_turn_id,
                task_id=delegation_id,
                existing_barrier_id=existing_parent_barrier_id,
            )
        except Exception:
            with _records_lock:
                _pop_record_locked(delegation_id)
            _delete_durable_delegation(delegation_id)
            raise
    executor = _get_executor(max_async_children)

    def _worker() -> None:
        combined: Dict[str, Any] = {}
        status = "error"
        context_token = _CURRENT_DELEGATION_ID.set(delegation_id)
        generation_token = _CURRENT_EXECUTION_GENERATION.set(
            int(record.get("execution_generation", 0) or 0)
        )
        try:
            combined = runner() or {}
            for child_result in combined.get("results") or []:
                commit_child_terminal(
                    int(child_result.get("task_index", -1)),
                    child_result,
                    delegation_id=delegation_id,
                )
            _mark_runner_returned(delegation_id)
            # A mixed batch is successful only when every child is successful.
            child_results = combined.get("results") or []
            if child_results and all(
                r.get("status") in ("completed", "success")
                for r in child_results
            ):
                status = "completed"
            else:
                status = "error"
        except Exception as exc:  # noqa: BLE001 — must never crash the worker
            logger.exception("Async delegation batch %s crashed", delegation_id)
            combined = {
                "results": [
                    {
                        "task_index": index,
                        "status": "error",
                        "summary": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    for index in range(n)
                ],
                "error": f"{type(exc).__name__}: {exc}",
                "total_duration_seconds": round(time.time() - dispatched_at, 2),
            }
            for child_result in combined["results"]:
                commit_child_terminal(
                    int(child_result["task_index"]),
                    child_result,
                    delegation_id=delegation_id,
                    replay_decision="runner_exception",
                )
            _mark_runner_returned(delegation_id)
            status = "error"
        finally:
            try:
                _finalize_batch(delegation_id, combined, status)
            finally:
                _CURRENT_EXECUTION_GENERATION.reset(generation_token)
                _CURRENT_DELEGATION_ID.reset(context_token)

    try:
        # Propagate the dispatching profile to the detached batch children.
        executor.submit(propagate_context_to_thread(_worker))
    except Exception as exc:  # pragma: no cover
        if root_turn_id:
            _finalize_batch(
                delegation_id,
                {
                    "results": [],
                    "error": f"Failed to schedule async delegation batch: {exc}",
                },
                "error",
            )
        else:
            with _records_lock:
                _pop_record_locked(delegation_id)
            _delete_durable_delegation(delegation_id)
        return {
            "status": "rejected",
            "error": f"Failed to schedule async delegation batch: {exc}",
        }
    if progress_fn is not None:
        _ensure_stale_monitor()

    logger.info(
        "Dispatched async delegation batch %s (%d task(s), session_key=%s)",
        delegation_id, n, session_key or "<cli>",
    )
    return {"status": "dispatched", "delegation_id": delegation_id}


def _finalize_batch(
    delegation_id: str, combined: Dict[str, Any], status: str
) -> None:
    """Mark a batch record complete and push ONE combined completion event."""
    claimed = _begin_finalization(delegation_id)
    if claimed is None:
        return
    event_record, _interrupt_fn = claimed

    if status == "interrupted" and defer_restartable_interruption(
        delegation_id, str(event_record.get("_interrupt_reason") or "")
    ):
        return
    persisted = _push_batch_completion_event(event_record, combined, status)
    _finish_finalization(
        delegation_id,
        status if persisted else "superseded",
        expected_generation=int(event_record.get("execution_generation", 0) or 0),
    )


def _push_batch_completion_event(
    event_record: Dict[str, Any], combined: Dict[str, Any], status: str
) -> bool:
    """Push a combined async-delegation batch completion event."""
    dispatched_at = event_record.get("dispatched_at") or time.time()
    completed_at = event_record.get("completed_at") or time.time()
    evt = {
        "type": "async_delegation",
        "delegation_id": event_record.get("delegation_id"),
        "session_key": event_record.get("session_key", ""),
        "origin_ui_session_id": event_record.get("origin_ui_session_id", ""),
        "origin_session_id": event_record.get("origin_session_id", ""),
        "parent_session_id": event_record.get("parent_session_id"),
        "goal": event_record.get("goal", ""),
        "goals": event_record.get("goals"),
        "context": event_record.get("context"),
        "toolsets": event_record.get("toolsets"),
        "role": event_record.get("role"),
        "model": event_record.get("model"),
        "status": status,
        "execution_generation": int(
            event_record.get("execution_generation", 0) or 0
        ),
        "is_batch": True,
        # The full per-task results list — the formatter renders a
        # consolidated multi-task block from this.
        "results": combined.get("results") or [],
        # Per-task live transcript log paths (cache/delegation/live/...).
        # They persist after completion and double as the full-fidelity
        # operational record of each child's run.
        "live_transcripts": combined.get("live_transcripts"),
        "error": combined.get("error"),
        "total_duration_seconds": combined.get("total_duration_seconds"),
        "dispatched_at": dispatched_at,
        "completed_at": completed_at,
    }
    # Routing origin captured at dispatch (see _capture_routing_origin).
    for _k in ("scope_id", "user_id", "user_name"):
        if event_record.get(_k):
            evt[_k] = event_record[_k]
    # Structured stall metadata (#51690) — additive, present only on
    # stall-monitor finalizations.
    for _k in (
        "stalled_after_quiet_seconds",
        "stall_threshold_seconds",
        "stall_phase",
        "stall_grace_seconds",
    ):
        if _k in combined:
            evt[_k] = combined[_k]
    if not _persist_completion_with_retry(evt, combined):
        return False
    try:
        from tools.process_registry import process_registry

        process_registry.completion_queue.put(evt)
    except Exception as exc:  # pragma: no cover
        logger.error(
            "Async delegation batch %s: failed to enqueue completion event; "
            "durable replay retained: %s",
            event_record.get("delegation_id"), exc,
        )
        return True
    return True


def _ensure_stale_monitor() -> None:
    """Start (once) the module-level stale-delegation monitor thread.

    One daemon thread serves every dispatch; it exits on its own when no
    monitorable records remain, and is restarted by the next dispatch that
    carries a ``progress_fn``.
    """
    global _monitor_thread
    with _monitor_lock:
        if _monitor_thread is not None and _monitor_thread.is_alive():
            return
        _monitor_stop.clear()
        _monitor_thread = threading.Thread(
            target=_stale_monitor_loop,
            name="async-delegate-stale-monitor",
            daemon=True,
        )
        _monitor_thread.start()


def _stale_monitor_loop() -> None:
    """Sweep running delegations for stalled progress.

    Per sweep, for every running record with a ``progress_fn``:

    - Sample ``(token, in_tool)``. A changed token refreshes the record's
      progress timestamp — a child that keeps advancing is never touched, no
      matter how long it runs.
    - A frozen token past the idle/in-tool threshold marks the record
      ``stalling``: we call ``interrupt_fn`` so a responsive-but-slow child
      can unwind and deliver its (partial) result through the normal
      ``_finalize`` path with full fidelity.
    - A ``stalling`` record whose runner still hasn't returned after the
      grace window is force-finalized with one terminal ``stalled`` event so
      the owning session hears an outcome and the async slot frees. A late
      runner return after that is ignored by ``_begin_finalization``.
    """
    while not _monitor_stop.wait(_STALE_CHECK_INTERVAL):
        now = time.time()
        # Every deferred action carries the exact generation and stall epoch
        # observed under _records_lock.  An id alone is unsafe because drain
        # recovery may install a replacement generation before callbacks run.
        stalled: List[tuple] = []
        expired: List[tuple] = []
        any_monitorable = False
        with _records_lock:
            for record in _records.values():
                status = record.get("status")
                if status == "stalling":
                    any_monitorable = True
                    interrupted_at = record.get("_interrupted_at") or now
                    if now - interrupted_at >= _STALL_GRACE_SECONDS:
                        expired.append(
                            (
                                record["delegation_id"],
                                _record_home(record),
                                int(record.get("execution_generation", 0) or 0),
                                interrupted_at,
                            )
                        )
                    continue
                if status != "running":
                    continue
                progress_fn = record.get("progress_fn")
                if progress_fn is None:
                    continue
                any_monitorable = True
                try:
                    token, in_tool = progress_fn()
                except Exception:
                    # An unreadable child must not look permanently healthy —
                    # keep the last timestamp running instead of refreshing it.
                    token, in_tool = record.get("_progress_token"), False
                if token != record.get("_progress_token"):
                    record["_progress_token"] = token
                    record["_progress_ts"] = now
                    continue
                quiet_for = now - (record.get("_progress_ts") or now)
                limit = (
                    _STALE_IN_TOOL_SECONDS if in_tool else _STALE_IDLE_SECONDS
                )
                if quiet_for >= limit:
                    record["status"] = "stalling"
                    record["_interrupted_at"] = now
                    # Structured stall context for the terminal event and
                    # status listings (#51690): how long progress was frozen,
                    # which threshold applied, and whether the child was
                    # inside a tool when it went quiet.
                    record["_stall_quiet_seconds"] = round(quiet_for, 2)
                    record["_stall_threshold_seconds"] = limit
                    record["_stall_in_tool"] = bool(in_tool)
                    stalled.append(
                        (
                            record["delegation_id"],
                            _record_home(record),
                            bool(record.get("is_batch")),
                            quiet_for,
                            in_tool,
                            int(record.get("execution_generation", 0) or 0),
                            now,
                        )
                    )
        for (
            delegation_id,
            state_home,
            _is_batch,
            quiet_for,
            in_tool,
            generation,
            interrupted_at,
        ) in stalled:
            logger.warning(
                "Async delegation %s made no progress for %.0fs "
                "(in_tool=%s) — interrupting; grace window %.0fs",
                delegation_id, quiet_for, in_tool, _STALL_GRACE_SECONDS,
            )
            with _records_lock:
                record = _get_record_locked(delegation_id, state_home)
                token_matches = bool(
                    record
                    and record.get("status") == "stalling"
                    and int(record.get("execution_generation", 0) or 0)
                    == generation
                    and record.get("_interrupted_at") == interrupted_at
                )
                fn = record.get("interrupt_fn") if token_matches else None
            if callable(fn):
                try:
                    with state_home_scope(state_home):
                        fn()
                except Exception as exc:
                    logger.debug(
                        "Async delegation %s stall interrupt failed: %s",
                        delegation_id, exc,
                    )
        for delegation_id, state_home, generation, interrupted_at in expired:
            with state_home_scope(state_home):
                _finalize_stalled(
                    delegation_id,
                    expected_generation=generation,
                    expected_interrupted_at=interrupted_at,
                )
        if not any_monitorable:
            return


def _finalize_stalled(
    delegation_id: str,
    *,
    expected_generation: Optional[int] = None,
    expected_interrupted_at: Optional[float] = None,
) -> None:
    """Force-finalize under the record's immutable durable home."""
    with _records_lock:
        record = _get_record_locked(delegation_id)
        if record is None:
            candidates = [
                item for item in _records.values()
                if str(item.get("delegation_id") or "") == str(delegation_id)
            ]
            record = candidates[0] if len(candidates) == 1 else None
        state_home = str((record or {}).get("_state_home") or "")
    if state_home:
        with state_home_scope(state_home):
            return _finalize_stalled_in_scope(
                delegation_id,
                expected_generation=expected_generation,
                expected_interrupted_at=expected_interrupted_at,
            )
    return _finalize_stalled_in_scope(
        delegation_id,
        expected_generation=expected_generation,
        expected_interrupted_at=expected_interrupted_at,
    )


def _finalize_stalled_in_scope(
    delegation_id: str,
    *,
    expected_generation: Optional[int] = None,
    expected_interrupted_at: Optional[float] = None,
) -> None:
    """Force-finalize a stalling delegation whose runner never returned."""
    with _records_lock:
        current = _get_record_locked(delegation_id)
        if current is None or current.get("status") != "stalling":
            return
        if (
            expected_generation is not None
            and int(current.get("execution_generation", 0) or 0)
            != int(expected_generation)
        ):
            return
        if (
            expected_interrupted_at is not None
            and current.get("_interrupted_at") != expected_interrupted_at
        ):
            return
        generation = int(current.get("execution_generation", 0) or 0)
    claimed = _begin_finalization(
        delegation_id,
        expected_generation=generation,
    )
    if claimed is None:
        return
    event_record, _interrupt_fn = claimed

    completed_at = event_record.get("completed_at") or time.time()
    duration = round(
        completed_at - (event_record.get("dispatched_at") or completed_at),
        2,
    )
    quiet_seconds = event_record.get("_stall_quiet_seconds")
    threshold_seconds = event_record.get("_stall_threshold_seconds")
    stall_in_tool = event_record.get("_stall_in_tool")
    error = (
        f"Async delegation {delegation_id} stalled: the detached subagent "
        "stopped making progress (no new API calls, tool activity, or "
        "streamed tokens), did not respond to interruption, and never "
        "produced a completion event. The worker may be wedged inside a "
        "model API call — this is a known failure mode of long-lived "
        "gateway processes (#60203). Re-dispatch the task if it is still "
        "needed."
    )
    logger.error(
        "Async delegation %s force-finalized as stalled after %.0fs",
        delegation_id, duration,
    )
    # Structured stall metadata (#51690): lets parents and UIs distinguish
    # a stall-monitor kill from other failures without parsing the error
    # string, mirroring the sync path's timeout_seconds/timed_out_after_
    # seconds/timeout_phase fields.
    stall_meta = {
        "stalled_after_quiet_seconds": quiet_seconds,
        "stall_threshold_seconds": threshold_seconds,
        "stall_phase": (
            "in_tool" if stall_in_tool
            else "idle" if stall_in_tool is not None
            else None
        ),
        "stall_grace_seconds": _STALL_GRACE_SECONDS,
    }
    if event_record.get("is_batch"):
        persisted = _push_batch_completion_event(
            event_record,
            {
                "results": [],
                "error": error,
                "total_duration_seconds": duration,
                **stall_meta,
            },
            "stalled",
        )
    else:
        persisted = _push_completion_event(
            event_record,
            {
                "status": "stalled",
                "summary": None,
                "error": error,
                "api_calls": 0,
                "duration_seconds": duration,
                "exit_reason": "stalled",
                **stall_meta,
            },
            "stalled",
        )
    _finish_finalization(
        delegation_id,
        "stalled" if persisted else "superseded",
        expected_generation=int(event_record.get("execution_generation", 0) or 0),
    )


def _children_activity_from_token(token: Any, now: float) -> Optional[List]:
    """Parse a progress token into per-child activity dicts (best-effort).

    delegate_tool's ``_batch_progress`` emits one ``(api_call_count,
    current_tool, last_activity_ts)`` tuple per child. Foreign token shapes
    (custom dispatchers) degrade to ``None`` entries rather than raising —
    the token contract is intentionally opaque to the registry.
    """
    try:
        parts = list(token)
    except TypeError:
        return None
    out: List[Optional[Dict[str, Any]]] = []
    for part in parts:
        if isinstance(part, (list, tuple)) and len(part) >= 2:
            entry: Dict[str, Any] = {
                "api_calls": part[0],
                "current_tool": part[1],
            }
            if len(part) >= 3 and isinstance(part[2], (int, float)):
                entry["seconds_since_activity"] = round(
                    max(0.0, now - float(part[2])), 1
                )
            out.append(entry)
        else:
            out.append(None)
    return out


def list_async_delegations() -> List[Dict[str, Any]]:
    """Snapshot of async delegations (running + recently completed).

    Safe to call from any thread. Excludes the non-serialisable callables
    and private monitor bookkeeping, but exposes computed live-status
    fields for UIs (#51690):

    - ``seconds_since_progress``: how long the stale monitor has seen a
      frozen progress token (running/stalling records).
    - ``children_activity``: per-child ``{api_calls, current_tool,
      seconds_since_activity}`` sampled live from the dispatch's
      ``progress_fn``.
    - ``stalled_after_quiet_seconds`` / ``stall_threshold_seconds`` /
      ``stall_in_tool``: stall context once the monitor has tripped.
    """
    now = time.time()
    samplers: Dict[str, Callable] = {}
    with _records_lock:
        items = []
        for r in _records.values():
            item = {
                k: v
                for k, v in r.items()
                if k not in {"interrupt_fn", "progress_fn"}
                and not k.startswith("_")
            }
            status = r.get("status")
            if status in ("running", "stalling"):
                ts = r.get("_progress_ts")
                if ts:
                    item["seconds_since_progress"] = round(now - ts, 1)
                fn = r.get("progress_fn")
                if callable(fn):
                    samplers[r["delegation_id"]] = fn
            if status in ("stalling", "stalled"):
                for src, dst in (
                    ("_stall_quiet_seconds", "stalled_after_quiet_seconds"),
                    ("_stall_threshold_seconds", "stall_threshold_seconds"),
                    ("_stall_in_tool", "stall_in_tool"),
                ):
                    if r.get(src) is not None:
                        item[dst] = r.get(src)
            items.append(item)

    # Sample live activity OUTSIDE the lock — progress_fn reads child-agent
    # attributes and must never run under _records_lock (a slow or broken
    # sampler would block every dispatch/finalize in the process).
    for item in items:
        fn = samplers.get(item.get("delegation_id"))
        if fn is None:
            continue
        try:
            token, in_tool = fn()
        except Exception:
            continue
        activity = _children_activity_from_token(token, now)
        if activity is not None:
            item["children_activity"] = activity
        item["in_tool"] = bool(in_tool)
    return items


def interrupt_all(reason: str = "shutdown") -> int:
    """Signal every running async delegation to stop. Returns how many.

    Used on ``/stop`` and gateway shutdown so a dangling background subagent
    can't keep burning tokens with no one listening. The child still emits a
    completion event (status='interrupted') via the normal finalize path.
    """
    count = 0
    with _records_lock:
        targets = [
            r for r in _records.values()
            if r.get("status") in ("running", "stalling")
        ]
    for r in targets:
        r["_interrupt_reason"] = reason
        fn = r.get("interrupt_fn")
        if callable(fn):
            try:
                fn()
                count += 1
            except Exception as exc:
                logger.debug(
                    "interrupt_all: %s interrupt failed: %s",
                    r.get("delegation_id"), exc,
                )
    if count:
        logger.info("Interrupted %d async delegation(s) (%s)", count, reason)
    return count


def interrupt_for_session(
    session_key: str = "",
    origin_ui_session_id: str = "",
    parent_session_id: str = "",
    reason: str = "session_end",
) -> int:
    """Signal running async delegations owned by ONE session to stop.

    A delegation's lifecycle is bound to the session that spawned it: when
    that session ends, its in-flight background subagents must end with it —
    a completed orphan would otherwise sit on the shared completion queue
    with no live owner, either leaking into another chat or burning tokens
    with no one listening (#55578).

    Selectors (any matching field claims the record):
    - ``origin_ui_session_id``: the live TUI tab/window that commissioned it.
    - ``session_key``: the durable routing key captured at dispatch.
    - ``parent_session_id``: the spawning agent's durable session-db id —
      the right selector for gateway chats, whose ``session_key`` (the
      platform conversation key) SURVIVES a ``/new`` reset while the
      session id rotates.

    Returns how many were interrupted.
    """
    if not session_key and not origin_ui_session_id and not parent_session_id:
        return 0
    from tools.parent_task_barrier import cancel_session_barriers

    cancel_session_barriers(
        origin_session=session_key,
        parent_session_id=parent_session_id,
    )
    count = 0
    with _records_lock:
        targets = [
            r for r in _records.values()
            if r.get("status") in ("running", "stalling")
            and _matches_session_selectors(
                r,
                session_key=session_key,
                origin_ui_session_id=origin_ui_session_id,
                parent_session_id=parent_session_id,
            )
        ]
    for r in targets:
        r["_interrupt_reason"] = reason
        fn = r.get("interrupt_fn")
        if callable(fn):
            try:
                fn()
                count += 1
            except Exception as exc:
                logger.debug(
                    "interrupt_for_session: %s interrupt failed: %s",
                    r.get("delegation_id"), exc,
                )
    if count:
        logger.info(
            "Interrupted %d async delegation(s) for ending session (%s)",
            count, reason,
        )
    return count


def _reset_for_tests() -> None:
    """Test-only: clear all state and tear down the executor + monitor."""
    global _executor, _executor_max_workers, _monitor_thread
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
        _executor = None
        _executor_max_workers = 0
    _monitor_stop.set()
    with _monitor_lock:
        thread = _monitor_thread
        _monitor_thread = None
    if thread is not None and thread.is_alive():
        thread.join(timeout=2)
    with _records_lock:
        _records.clear()
