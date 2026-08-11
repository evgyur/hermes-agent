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

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterator, List, Optional

from hermes_constants import get_hermes_home
from tools.daemon_pool import DaemonThreadPoolExecutor
from tools.thread_context import propagate_context_to_thread

logger = logging.getLogger(__name__)

CONTINUUM_RUNTIME_REVISION = "0.1.0rc18-restartable-task-cards-v1"
logger.info("Continuum async runtime loaded revision=%s", CONTINUUM_RUNTIME_REVISION)

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
_records: Dict[str, Dict[str, Any]] = {}

_DEFAULT_MAX_ASYNC_CHILDREN = 3
# How many completed records to retain for status queries before pruning.
_MAX_RETAINED_COMPLETED = 50
_DURABLE_RETENTION_SECONDS = 7 * 24 * 60 * 60
_STALE_RESERVATION_SECONDS = 300
# Pending terminal outcomes are delivery obligations, not bounded history. They
# remain durable until acknowledged delivery or explicit operator archival.
# Never count-prune them: dropping an undelivered callback is data loss.
# A pending completion whose delivery keeps failing is retried across claim
# cycles (and across restarts via restore_undelivered_completions). Cap the
# attempts so an unroutable row converges to a terminal 'dropped' state
# instead of replaying on every restart forever.
_MAX_DELIVERY_ATTEMPTS = 8
_MAX_RESTART_ATTEMPTS = 3
_RESTART_POLICY = "gateway_owned_v1"
_DB_LOCK = threading.Lock()
_CURRENT_DELEGATION_ID: ContextVar[str] = ContextVar(
    "HERMES_CURRENT_ASYNC_DELEGATION_ID", default=""
)
_PRIVATE_PATH_RE = re.compile(r"(?<![\w:])/(?:[^\s`]+)")
_PRIVATE_METADATA_RE = re.compile(
    r"(?i)\b(?:origin|session|parent[_ -]?session|transcript|callback)"
    r"(?:[_ -]?(?:id|path|payload))?(?:\s*[=:]\s*|\s+)[^\s,;]+"
)
_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+|api[_-]?key\s*[=:]\s*|token\s*[=:]\s*|password\s*[=:]\s*)"
    r"[^\s,;]+"
)
_CHECKPOINT_STATES = {"planned", "running", "blocked", "completed", "summarizing"}


class TrustedRestartEvent(dict):
    """Internal wake envelope; DB nonce + CAS provide authenticity."""

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
    return get_hermes_home() / "state.db"


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


def _initialize_schema(conn: sqlite3.Connection) -> None:
    from hermes_state import apply_wal_with_fallback

    apply_wal_with_fallback(conn, db_label="state.db (async_delegation)")
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
            origin_session_id TEXT NOT NULL DEFAULT '',
            public_title TEXT NOT NULL DEFAULT '',
            progress_json TEXT,
            heartbeat_at REAL,
            api_calls INTEGER NOT NULL DEFAULT 0,
            current_tool TEXT NOT NULL DEFAULT '',
            status_revision INTEGER NOT NULL DEFAULT 1
        )"""
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(async_delegations)")}
    for name, sql_type in (
        ("owner_pid", "INTEGER"),
        ("owner_started_at", "INTEGER"),
        ("task_json", "TEXT"),
        ("delivery_claim", "TEXT"),
        ("delivery_claimed_at", "REAL"),
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
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE async_delegations ADD COLUMN {name} {sql_type}")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS continuum_status_rails (
            origin_session TEXT PRIMARY KEY,
            message_id TEXT NOT NULL DEFAULT '',
            rendered_hash TEXT NOT NULL DEFAULT '',
            source_revision INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL DEFAULT 0,
            pinned INTEGER NOT NULL DEFAULT 0,
            last_attempt_at REAL,
            last_published_at REAL,
            create_state TEXT NOT NULL DEFAULT '',
            create_token TEXT NOT NULL DEFAULT '',
            create_started_at REAL,
            updated_at REAL NOT NULL,
            last_error TEXT NOT NULL DEFAULT ''
        )"""
    )
    rail_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(continuum_status_rails)")
    }
    for name, sql_type in (
        ("create_state", "TEXT NOT NULL DEFAULT ''"),
        ("create_token", "TEXT NOT NULL DEFAULT ''"),
        ("create_started_at", "REAL"),
    ):
        if name not in rail_columns:
            conn.execute(f"ALTER TABLE continuum_status_rails ADD COLUMN {name} {sql_type}")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS continuum_task_cards (
            delegation_id TEXT PRIMARY KEY,
            origin_session TEXT NOT NULL,
            message_id TEXT NOT NULL DEFAULT '',
            rendered_hash TEXT NOT NULL DEFAULT '',
            source_revision INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL DEFAULT 0,
            last_attempt_at REAL,
            last_published_at REAL,
            create_state TEXT NOT NULL DEFAULT '',
            create_token TEXT NOT NULL DEFAULT '',
            create_started_at REAL,
            updated_at REAL NOT NULL,
            last_error TEXT NOT NULL DEFAULT ''
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_continuum_task_cards_origin "
        "ON continuum_task_cards(origin_session)"
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


def _sanitize_public_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    try:
        from agent.redact import redact_sensitive_text

        text = redact_sensitive_text(text, force=True)
    except Exception:
        # A failed central redactor must fail closed rather than persist raw text.
        text = "[скрыто]" if text else ""
    text = _SECRET_RE.sub("[скрыто]", text)
    text = _PRIVATE_PATH_RE.sub("[скрытый путь]", text)
    text = _PRIVATE_METADATA_RE.sub("[скрыто]", text)
    return text[:limit].rstrip()


def _load_durable_json(value: Any, default: Any) -> Any:
    """Decode typed persisted JSON without stranding a claimed row."""
    try:
        decoded = json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
    return decoded if isinstance(decoded, type(default)) else default


def _public_title(record: Dict[str, Any]) -> str:
    goal = record.get("goal")
    if not isinstance(goal, str):
        return ""
    first_line = next((line.strip() for line in goal.splitlines() if line.strip()), "")
    return _sanitize_public_text(first_line, limit=80)


def _persist_dispatch(record: Dict[str, Any]) -> bool:
    now = time.time()
    try:
        from gateway.status import get_process_start_time
        owner_started_at = get_process_start_time(__import__("os").getpid())
    except Exception:
        owner_started_at = None
    task_payload = {
        key: record.get(key)
        for key in ("goal", "goals", "context", "toolsets", "role", "model", "is_batch")
        if key in record
    }
    with _DB_LOCK, _transaction() as conn:
        existing = conn.execute(
            "SELECT state FROM async_delegations WHERE delegation_id=?",
            (record["delegation_id"],),
        ).fetchone()
        if record.get("resume_claim"):
            if not existing or existing[0] != "restarting":
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
                    json.dumps(record.get("child_session_ids") or []),
                    json.dumps(record.get("child_capability_names") or []),
                    record["delegation_id"],
                ),
            )
            return changed.rowcount == 1
        conn.execute(
            """INSERT OR REPLACE INTO async_delegations
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
                json.dumps(task_payload),
                record.get("origin_session_id", ""),
                _public_title(record),
                now,
            ),
        )
        conn.execute(
            """UPDATE async_delegations SET child_session_ids_json=?,
               child_capability_names_json=?, restart_policy=?, restart_count=?,
               restart_reason=''
               WHERE delegation_id=?""",
            (
                json.dumps(record.get("child_session_ids") or []),
                json.dumps(record.get("child_capability_names") or []),
                record.get("restart_policy", ""),
                int(record.get("restart_count", 0) or 0),
                record["delegation_id"],
            ),
        )
    _prune_durable_records()
    return True


def _delete_durable_delegation(delegation_id: str) -> None:
    with _DB_LOCK, _transaction() as conn:
        conn.execute("DELETE FROM async_delegations WHERE delegation_id=?", (delegation_id,))


def _mark_runner_returned(delegation_id: str) -> None:
    """Close the dead-owner replay window immediately after runner return."""
    with _DB_LOCK, _transaction() as conn:
        conn.execute(
            """UPDATE async_delegations SET runner_returned=1, updated_at=?
               WHERE delegation_id=? AND state IN ('running','stalling')""",
            (time.time(), delegation_id),
        )


def _prune_durable_records() -> None:
    """Bound acknowledged terminal history without deleting delivery obligations."""
    now = time.time()
    cutoff = now - _DURABLE_RETENTION_SECONDS
    with _DB_LOCK, _transaction() as conn:
        conn.execute(
            "DELETE FROM async_delegations WHERE delivery_state='delivered' AND updated_at < ?",
            (cutoff,),
        )
        delivered_count = conn.execute(
            """SELECT COUNT(*) FROM async_delegations
               WHERE state NOT IN ('running','finalizing')
                 AND delivery_state='delivered'"""
        ).fetchone()[0]
        excess = max(0, delivered_count - _MAX_RETAINED_COMPLETED)
        if excess:
            conn.execute(
                """DELETE FROM async_delegations WHERE delegation_id IN (
                     SELECT delegation_id FROM async_delegations
                     WHERE state NOT IN ('running','finalizing')
                       AND delivery_state='delivered'
                     ORDER BY updated_at ASC LIMIT ?
                   )""",
                (excess,),
            )


def _persist_completion(event: Dict[str, Any], result: Dict[str, Any]) -> None:
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        conn.execute(
            """UPDATE async_delegations SET state=?, completed_at=?, updated_at=?,
               heartbeat_at=?, event_json=?, result_json=?, delivery_state='pending',
               status_revision=status_revision+1
               WHERE delegation_id=?""",
            (
                event.get("status", "completed"),
                event.get("completed_at", now),
                now,
                now,
                json.dumps(event),
                json.dumps(result),
                event["delegation_id"],
            ),
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
                 AND restart_count < ? AND state IN ('running','stalling','finalizing')""",
            (
                reason,
                _new_restart_nonce(),
                now,
                now,
                delegation_id,
                _RESTART_POLICY,
                _MAX_RESTART_ATTEMPTS,
            ),
        ).rowcount
    if changed:
        with _records_lock:
            record = _records.get(delegation_id)
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
               restart_nonce='', updated_at=?
               WHERE delegation_id=? AND state='restart_pending'
                 AND restart_policy=? AND restart_count < ?
                 AND origin_session=? AND restart_nonce=?""",
            (owner_pid, owner_started_at, now, delegation_id,
             _RESTART_POLICY, _MAX_RESTART_ATTEMPTS, expected_session_key,
             restart_nonce),
        ).rowcount
        if not changed:
            return None
        row = conn.execute(
            """SELECT task_json, child_session_ids_json,
                      child_capability_names_json, restart_count,
                      parent_session_id, origin_session, origin_ui_session_id
               FROM async_delegations WHERE delegation_id=?""",
            (delegation_id,),
        ).fetchone()
    return {
        "delegation_id": delegation_id,
        "task": _load_durable_json(row[0], {}),
        "child_session_ids": _load_durable_json(row[1], []),
        "child_capability_names": _load_durable_json(row[2], []),
        "restart_count": row[3],
        "parent_session_id": row[4],
        "session_key": row[5],
        "origin_ui_session_id": row[6],
    }


def claim_restartable_delegations(*, owner_pid: int, owner_started_at: int) -> List[Dict[str, Any]]:
    """Compatibility bulk claimer; production wakes claim individually."""
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, origin_session, restart_nonce
               FROM async_delegations
               WHERE state='restart_pending' AND restart_policy=?
                 AND restart_count < ? ORDER BY dispatched_at, delegation_id""",
            (_RESTART_POLICY, _MAX_RESTART_ATTEMPTS),
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
                 AND restart_policy=? AND restart_count >= ?""",
            (_RESTART_POLICY, _MAX_RESTART_ATTEMPTS),
        ).fetchall()
        for delegation_id, task_json, origin, origin_ui, parent_sid in rows:
            task = _load_durable_json(task_json, {})
            event = {
                "type": "async_delegation",
                "delegation_id": delegation_id,
                "status": "error",
                "task": task,
                "result": {
                    "error": "Continuum recovery failed after 3 restart attempts"
                },
                "session_key": origin,
                "origin_ui_session_id": origin_ui,
                "parent_session_id": parent_sid,
            }
            changed = conn.execute(
                """UPDATE async_delegations SET state='error', completed_at=?,
                   updated_at=?, event_json=?, result_json=?, restart_nonce=''
                   WHERE delegation_id=? AND state='restart_pending'
                     AND restart_count >= ?""",
                (now, now, json.dumps(event), json.dumps(event["result"]),
                 delegation_id, _MAX_RESTART_ATTEMPTS),
            ).rowcount
            finalized += int(bool(changed))
    return finalized


def restore_restartable_delegations(target_queue) -> int:
    """Restore persisted pending rows as trusted internal recovery wakes."""
    recover_abandoned_delegations()
    finalize_exhausted_restarts()
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, origin_session, origin_ui_session_id,
                      parent_session_id, restart_nonce FROM async_delegations
               WHERE state='restart_pending' AND restart_policy=?
                 AND restart_count < ? ORDER BY dispatched_at, delegation_id""",
            (_RESTART_POLICY, _MAX_RESTART_ATTEMPTS),
        ).fetchall()
    for delegation_id, session_key, origin_ui, parent_sid, restart_nonce in rows:
        target_queue.put(TrustedRestartEvent({
            "type": "async_delegation_restart",
            "delegation_id": delegation_id,
            "session_key": session_key,
            "origin_ui_session_id": origin_ui,
            "parent_session_id": parent_sid,
            "restart_nonce": restart_nonce,
            "internal": True,
        }))
    return len(rows)


def release_restart_claim(delegation_id: str, reason: str) -> bool:
    if not restart_reason_is_eligible(reason):
        return False
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            """SELECT restart_count FROM async_delegations
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
        # At the cap the row is deliberately returned to restart_pending but
        # False tells the gateway to finalize+enqueue exhaustion immediately.
        return bool(changed and int(row[0]) < _MAX_RESTART_ATTEMPTS)


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
    if not changed or event is None:
        return False
    with _records_lock:
        _records.pop(delegation_id, None)
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
            task = json.loads(task_json or "{}")
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
                events.append(event)
    if not events:
        return set()
    with _records_lock:
        for event in events:
            _records.pop(event["delegation_id"], None)
    try:
        from tools.process_registry import process_registry

        for event in events:
            process_registry.completion_queue.put(event)
    except Exception:
        logger.exception("Could not enqueue explicit restart cancellations")
    return {str(event["delegation_id"]) for event in events}


def recover_abandoned_delegations() -> int:
    """Classify records whose owning process disappeared as outcome unknown."""
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
                      runner_returned
               FROM async_delegations
               WHERE state IN ('running','stalling','finalizing','restarting')"""
        ).fetchall()
        for row in rows:
            (delegation_id, state, session_key, origin_ui, parent_id,
             dispatched_at, pid, started, task_json, origin_session_id,
             runner_returned) = row
            live = False
            if pid:
                live = _pid_exists(int(pid))
                if live and started is not None:
                    live = get_process_start_time(int(pid)) == int(started)
            if live:
                continue
            restart_row = conn.execute(
                "SELECT restart_policy, restart_count FROM async_delegations WHERE delegation_id=?",
                (delegation_id,),
            ).fetchone()
            if (
                state != "finalizing"
                and not bool(runner_returned)
                and task_json
                and restart_row is not None
                and restart_row[0] == _RESTART_POLICY
                and int(restart_row[1] or 0) < _MAX_RESTART_ATTEMPTS
            ):
                conn.execute(
                    """UPDATE async_delegations SET state='restart_pending',
                       restart_reason='dead_owner', restart_nonce=?, owner_pid=NULL,
                       owner_started_at=NULL, updated_at=?, heartbeat_at=?
                       WHERE delegation_id=?""",
                    (_new_restart_nonce(), now, now, delegation_id),
                )
                recovered += 1
                continue
            task = json.loads(task_json or "{}")
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
            result = {"status": "unknown", "summary": None, "error": event["error"]}
            conn.execute(
                """UPDATE async_delegations SET state='unknown', completed_at=?,
                   updated_at=?, heartbeat_at=?, event_json=?, result_json=?,
                   delivery_state='pending', status_revision=status_revision+1,
                   restart_nonce=''
                   WHERE delegation_id=?""",
                (now, now, now, json.dumps(event), json.dumps(result), delegation_id),
            )
            recovered += 1
    return recovered


def restore_undelivered_completions(target_queue) -> int:
    """Enqueue durable pending completions as fresh turns after process start.

    Every restored event is stamped ``restored=True`` (in-memory only — the
    stamp is added after the durable payload is deserialized and is never
    persisted). Restored events originate from a *previous* process, so no
    consumer in THIS process implicitly owns them: drain paths that run
    without an ownership filter (the legacy single-session behavior) must
    leave them queued for a consumer that can positively prove ownership,
    otherwise a brand-new session adopts a dead session's delegation
    results seconds after boot (#64484).
    """
    recover_abandoned_delegations()
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, event_json FROM async_delegations
               WHERE state != 'running' AND delivery_state='pending' AND event_json IS NOT NULL
               ORDER BY completed_at, delegation_id"""
        ).fetchall()
        for _delegation_id, payload in rows:
            evt = json.loads(payload)
            if isinstance(evt, dict):
                evt["restored"] = True
            target_queue.put(evt)
    return len(rows)


def mark_completion_delivered(delegation_id: str) -> bool:
    """Atomically acknowledge successful injection of a durable completion."""
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        cur = conn.execute(
            """UPDATE async_delegations SET delivery_state='delivered', delivered_at=?,
                      updated_at=?, status_revision=status_revision+1
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


def claim_event_delivery(evt: Dict[str, Any], consumer: str) -> Optional[str]:
    """Claim a durable delegation event; non-durable events need no token."""
    if evt.get("type") != "async_delegation":
        return ""
    delegation_id = str(evt.get("delegation_id") or "")
    if not delegation_id:
        return ""
    claim_id = f"{consumer}:{__import__('os').getpid()}:{uuid.uuid4().hex}"
    return claim_id if claim_completion_delivery(delegation_id, claim_id) else None


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
                 AND delivery_claim=? AND delivery_attempts>=?""",
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


def release_completion_delivery_waiting(delegation_id: str, claim_id: str) -> bool:
    """Release a claim while waiting for idempotent card reconciliation."""
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        cur = conn.execute(
            """UPDATE async_delegations
               SET delivery_claim=NULL, delivery_claimed_at=NULL,
                   delivery_attempts=MAX(0, delivery_attempts-1), updated_at=?
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
                      delivery_claimed_at=NULL, status_revision=status_revision+1
               WHERE delegation_id=? AND delivery_state='pending'
                 AND delivery_claim=?""",
            (now, now, delegation_id, claim_id),
        )
        return cur.rowcount == 1


def complete_event_delivery(evt: Dict[str, Any], claim_id: str) -> None:
    if claim_id and evt.get("type") == "async_delegation":
        complete_completion_delivery(str(evt.get("delegation_id") or ""), claim_id)


def release_event_delivery(evt: Dict[str, Any], claim_id: str) -> None:
    if claim_id and evt.get("type") == "async_delegation":
        release_completion_delivery(str(evt.get("delegation_id") or ""), claim_id)


def current_delegation_id() -> str:
    """Return the worker-bound delegation id; empty outside a retained child."""
    return _CURRENT_DELEGATION_ID.get()


def record_current_delegation_checkpoint(
    *,
    stage: int,
    total: int,
    label: str,
    state: str,
    note: str = "",
    plan: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Persist a redacted semantic checkpoint for the currently running child."""
    delegation_id = current_delegation_id()
    if not delegation_id:
        return {"ok": False, "state": "blocked", "diagnostic": "NO_ACTIVE_DELEGATION"}
    if not 1 <= int(total) <= 32 or not 1 <= int(stage) <= int(total):
        return {"ok": False, "state": "blocked", "diagnostic": "INVALID_STAGE_RANGE"}
    if state not in _CHECKPOINT_STATES:
        return {"ok": False, "state": "blocked", "diagnostic": "INVALID_STAGE_STATE"}
    if plan is not None and (not isinstance(plan, list) or len(plan) != int(total)):
        return {"ok": False, "state": "blocked", "diagnostic": "INVALID_STAGE_PLAN"}

    now = time.time()
    safe_label = _sanitize_public_text(label, limit=72) or f"этап {stage}"
    safe_note = _sanitize_public_text(note, limit=120)
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            "SELECT state, progress_json FROM async_delegations WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()
        if row is None or row[0] not in {"running", "stalling", "finalizing"}:
            return {"ok": False, "state": "blocked", "diagnostic": "DELEGATION_NOT_RUNNING"}
        try:
            progress = json.loads(row[1]) if row[1] else {}
        except (TypeError, ValueError):
            progress = {}
        raw_stages = progress.get("stages")
        stages: List[Dict[str, Any]] = (
            [dict(item) for item in raw_stages if isinstance(item, dict)]
            if isinstance(raw_stages, list)
            else []
        )
        requested_total = int(total)
        if int(stage) < 1 or int(stage) > requested_total:
            return {"ok": False, "state": "blocked", "diagnostic": "CHECKPOINT_STAGE_RANGE"}
        if len(stages) > requested_total:
            return {"ok": False, "state": "blocked", "diagnostic": "CHECKPOINT_TOTAL_REGRESSION"}
        if len(stages) < requested_total:
            labels = plan if plan is not None else [f"этап {i}" for i in range(1, requested_total + 1)]
            for index in range(len(stages), requested_total):
                item = labels[index] if index < len(labels) else f"этап {index + 1}"
                stages.append(
                    {
                        "label": _sanitize_public_text(item, limit=72),
                        "state": "planned",
                    }
                )
        target = stages[int(stage) - 1]
        previous = str(target.get("state") or "planned")
        if any(
            str(item.get("state") or "planned") != "completed"
            for item in stages[: int(stage) - 1]
        ) and state != "planned":
            return {"ok": False, "state": "blocked", "diagnostic": "CHECKPOINT_ORDER"}
        allowed_transitions = {
            "planned": {"planned", "running", "blocked", "summarizing", "completed"},
            "running": {"running", "blocked", "summarizing", "completed"},
            "blocked": {"blocked", "running", "summarizing", "completed"},
            "summarizing": {"summarizing", "completed"},
            "completed": {"completed"},
        }
        if state not in allowed_transitions.get(previous, set()):
            return {"ok": False, "state": "blocked", "diagnostic": "CHECKPOINT_REGRESSION"}
        if previous == "completed" and safe_label != str(target.get("label") or ""):
            return {"ok": False, "state": "blocked", "diagnostic": "CHECKPOINT_IMMUTABLE"}
        target["label"] = safe_label
        if state in {"running", "blocked", "summarizing"} and not target.get("started_at"):
            target["started_at"] = now
        if state == "completed":
            target.setdefault("started_at", now)
            target.setdefault("completed_at", now)
        target["state"] = state
        progress = {"stages": stages, "note": safe_note, "updated_at": now}
        conn.execute(
            """UPDATE async_delegations SET progress_json=?, heartbeat_at=?, updated_at=?,
                      status_revision=status_revision+1
               WHERE delegation_id=?""",
            (json.dumps(progress), now, now, delegation_id),
        )
    return {"ok": True, "stage": int(stage), "total": int(total), "state": state}


def _persist_progress_telemetry(delegation_id: str, token: Any, in_tool: bool) -> None:
    api_calls = 0
    tools: List[str] = []
    if isinstance(token, (tuple, list)):
        nested = bool(token) and all(isinstance(part, (tuple, list)) for part in token)
        parts = token if nested else (token,)
        for part in parts:
            if part and isinstance(part[0], int):
                api_calls += max(0, part[0])
            if len(part) > 1 and isinstance(part[1], str) and part[1]:
                tools.append(part[1])
    unique_tools = list(dict.fromkeys(tools))
    current_tool = ""
    if len(unique_tools) == 1:
        current_tool = unique_tools[0]
    elif len(unique_tools) > 1:
        current_tool = f"{len(unique_tools)} active tools"
    if not in_tool:
        current_tool = ""
    safe_tool = _sanitize_public_text(current_tool, limit=48)
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            "SELECT api_calls, current_tool FROM async_delegations WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()
        if row is None or (int(row[0] or 0), str(row[1] or "")) == (api_calls, safe_tool):
            return
        conn.execute(
            """UPDATE async_delegations SET heartbeat_at=?, updated_at=?, api_calls=?,
                      current_tool=? WHERE delegation_id=?
               AND state IN ('running','stalling','finalizing')""",
            (now, now, api_calls, safe_tool, delegation_id),
        )


def list_continuum_rail_snapshots(*, terminal_age_seconds: float = 86400) -> List[Dict[str, Any]]:
    """Return internal origin-scoped snapshots for the gateway status publisher."""
    now = time.time()
    cutoff = now - max(0, float(terminal_age_seconds))
    reservation_cutoff = now - _STALE_RESERVATION_SECONDS
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, origin_session, state, dispatched_at, completed_at,
                      updated_at, heartbeat_at, delivery_state, api_calls, current_tool,
                      public_title, progress_json, status_revision
               FROM async_delegations
               WHERE origin_session!=''
                 AND (
                   state IN ('reserved','dispatched','running','stalling','finalizing','restart_pending','restarting')
                   OR origin_session IN (
                     SELECT origin_session FROM continuum_status_rails WHERE message_id!=''
                   )
                 )
                 AND (
                   state IN ('reserved','dispatched','running','stalling','finalizing','restart_pending','restarting')
                   OR completed_at>=?
                 )
                 AND (state!='reserved' OR dispatched_at>=?)
               ORDER BY origin_session, dispatched_at""",
            (cutoff, reservation_cutoff),
        ).fetchall()
        rail_rows = conn.execute(
            """SELECT origin_session, message_id, rendered_hash, source_revision,
                      revision, pinned, last_attempt_at, last_published_at,
                      create_state, create_started_at
               FROM continuum_status_rails"""
        ).fetchall()
    bindings = {
        row[0]: {
            "message_id": row[1],
            "rendered_hash": row[2],
            "source_revision": row[3],
            "revision": row[4],
            "pinned": bool(row[5]),
            "last_attempt_at": row[6],
            "last_published_at": row[7],
            "create_state": row[8],
            "create_started_at": row[9],
        }
        for row in rail_rows
    }
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        try:
            progress = json.loads(row[11]) if row[11] else None
        except (TypeError, ValueError):
            progress = None
        grouped.setdefault(row[1], []).append(
            {
                "delegation_id": row[0],
                "state": row[2],
                "dispatched_at": row[3],
                "completed_at": row[4],
                "updated_at": row[5],
                "heartbeat_at": row[6],
                "delivery_state": row[7],
                "api_calls": row[8],
                "current_tool": row[9],
                "public_title": row[10],
                "progress": progress,
                "status_revision": row[12],
            }
        )
    snapshots = [
        {
            "origin_session": origin,
            "rows": grouped[origin],
            "source_revision": max(
                int(row.get("status_revision") or 0) for row in grouped[origin]
            ),
            "rail": bindings.get(origin, {}),
        }
        for origin in sorted(grouped)
    ]
    for origin in sorted(set(bindings) - set(grouped)):
        binding = bindings[origin]
        if not binding.get("message_id"):
            continue
        snapshots.append(
            {
                "origin_session": origin,
                "rows": [],
                "source_revision": int(binding.get("source_revision") or 0),
                "rail": binding,
            }
        )
    return snapshots


def continuum_rail_publish_due(
    origin_session: str,
    rendered_hash: str,
    *,
    now: Optional[float] = None,
    min_interval_seconds: float = 5,
) -> bool:
    """Rate-limit edits and suppress byte-identical dashboard renders."""
    current = time.time() if now is None else float(now)
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            """SELECT rendered_hash, last_attempt_at FROM continuum_status_rails
               WHERE origin_session=?""",
            (origin_session,),
        ).fetchone()
        if row and row[0] == rendered_hash:
            return False
        if row and row[1] is not None and current - float(row[1]) < min_interval_seconds:
            return False
        conn.execute(
            """INSERT INTO continuum_status_rails(origin_session, last_attempt_at, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(origin_session) DO UPDATE SET
                 last_attempt_at=excluded.last_attempt_at, updated_at=excluded.updated_at""",
            (origin_session, current, current),
        )
    return True


def claim_continuum_rail_create(origin_session: str) -> str:
    """Reserve one create attempt; an uncertain attempt is never replayed."""
    token = uuid.uuid4().hex
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            "SELECT message_id, create_state FROM continuum_status_rails WHERE origin_session=?",
            (origin_session,),
        ).fetchone()
        if row and (str(row[0] or "") or str(row[1] or "")):
            return ""
        conn.execute(
            """INSERT INTO continuum_status_rails(
                   origin_session, create_state, create_token, create_started_at,
                   last_attempt_at, updated_at
               ) VALUES (?, 'in_flight', ?, ?, ?, ?)
               ON CONFLICT(origin_session) DO UPDATE SET
                   create_state='in_flight', create_token=excluded.create_token,
                   create_started_at=excluded.create_started_at,
                   last_attempt_at=excluded.last_attempt_at, updated_at=excluded.updated_at""",
            (origin_session, token, now, now, now),
        )
    return token


def mark_continuum_rail_missing(origin_session: str, message_id: str) -> bool:
    """Clear a durable binding only after the provider confirms it is gone."""
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        cursor = conn.execute(
            """UPDATE continuum_status_rails
               SET message_id='', rendered_hash='', create_state='', create_token='',
                   create_started_at=NULL, updated_at=?
               WHERE origin_session=? AND message_id=?""",
            (now, origin_session, str(message_id)),
        )
        return cursor.rowcount == 1


def reconcile_continuum_rail_create(
    origin_session: str,
    create_token: str,
    *,
    confirmed_message_id: str = "",
    confirmed_absent: bool = False,
) -> bool:
    """Resolve an ambiguous create only after an operator checks Telegram.

    Exactly one resolution is allowed: bind the provider-confirmed message or
    clear the claim after confirming that no message exists. The claim token
    makes stale/manual guesses fail closed; this function is never called by
    the automatic publisher.
    """
    message_id = str(confirmed_message_id).strip()
    if bool(message_id) == bool(confirmed_absent):
        return False
    if message_id and (not message_id.isdigit() or int(message_id) <= 0):
        return False
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        if message_id:
            cursor = conn.execute(
                """UPDATE continuum_status_rails
                   SET message_id=?, rendered_hash='', create_state='', create_token='',
                       create_started_at=NULL, revision=revision+1, updated_at=?, last_error=''
                   WHERE origin_session=? AND message_id='' AND create_token=?
                     AND create_state IN ('in_flight','uncertain')""",
                (message_id, now, origin_session, create_token),
            )
        else:
            cursor = conn.execute(
                """UPDATE continuum_status_rails
                   SET create_state='', create_token='', create_started_at=NULL,
                       updated_at=?, last_error='operator confirmed create absent'
                   WHERE origin_session=? AND message_id='' AND create_token=?
                     AND create_state IN ('in_flight','uncertain')""",
                (now, origin_session, create_token),
            )
        return cursor.rowcount == 1


def _continuum_rail_reconcile_handle(origin_session: str, create_token: str) -> str:
    material = f"{origin_session}\0{create_token}".encode()
    return hashlib.sha256(material).hexdigest()[:20]


def list_continuum_rail_reconciliations() -> List[Dict[str, Any]]:
    """List ambiguous creates by opaque local handle, never by origin/token."""
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT origin_session, create_token, create_state, create_started_at
               FROM continuum_status_rails
               WHERE message_id='' AND create_token!=''
                 AND create_state IN ('in_flight','uncertain')"""
        ).fetchall()
    return sorted(
        (
            {
                "handle": _continuum_rail_reconcile_handle(row[0], row[1]),
                "state": row[2],
                "age_seconds": max(0, int(now - float(row[3] or now))),
            }
            for row in rows
        ),
        key=lambda item: item["handle"],
    )


def reconcile_continuum_rail_create_by_handle(
    handle: str,
    *,
    confirmed_message_id: str = "",
    confirmed_absent: bool = False,
) -> bool:
    """Operator surface for one opaque reconciliation handle."""
    candidate = str(handle).strip().lower()
    if re.fullmatch(r"[0-9a-f]{20}", candidate) is None:
        return False
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT origin_session, create_token
               FROM continuum_status_rails
               WHERE message_id='' AND create_token!=''
                 AND create_state IN ('in_flight','uncertain')"""
        ).fetchall()
    matches = [
        row
        for row in rows
        if _continuum_rail_reconcile_handle(str(row[0]), str(row[1])) == candidate
    ]
    if len(matches) != 1:
        return False
    return reconcile_continuum_rail_create(
        str(matches[0][0]),
        str(matches[0][1]),
        confirmed_message_id=confirmed_message_id,
        confirmed_absent=confirmed_absent,
    )


def record_continuum_rail_publish(
    origin_session: str,
    *,
    message_id: str,
    rendered_hash: str,
    source_revision: int,
    pinned: bool,
    error: str = "",
    create_token: str = "",
) -> bool:
    """Persist an accepted binding; reject a stale or foreign create claim."""
    now = time.time()
    safe_error = _sanitize_public_text(error, limit=160)
    with _DB_LOCK, _transaction() as conn:
        if create_token:
            row = conn.execute(
                "SELECT create_token FROM continuum_status_rails WHERE origin_session=?",
                (origin_session,),
            ).fetchone()
            if row is None or str(row[0] or "") != create_token:
                return False
        conn.execute(
            """INSERT INTO continuum_status_rails(
                   origin_session, message_id, rendered_hash, source_revision,
                   revision, pinned, last_attempt_at, last_published_at,
                   create_state, create_token, create_started_at, updated_at, last_error
               ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, '', '', NULL, ?, ?)
               ON CONFLICT(origin_session) DO UPDATE SET
                   message_id=excluded.message_id,
                   rendered_hash=excluded.rendered_hash,
                   source_revision=MAX(source_revision, excluded.source_revision),
                   revision=revision+1,
                   pinned=MAX(pinned, excluded.pinned),
                   last_attempt_at=excluded.last_attempt_at,
                   last_published_at=excluded.last_published_at,
                   create_state='', create_token='', create_started_at=NULL,
                   updated_at=excluded.updated_at,
                   last_error=excluded.last_error""",
            (
                origin_session,
                str(message_id),
                rendered_hash,
                max(0, int(source_revision)),
                int(bool(pinned)),
                now,
                now,
                now,
                safe_error,
            ),
        )
    return True


def record_continuum_rail_failure(
    origin_session: str, error: str, *, create_token: str = ""
) -> None:
    now = time.time()
    safe_error = _sanitize_public_text(error, limit=160)
    with _DB_LOCK, _transaction() as conn:
        if create_token:
            conn.execute(
                """UPDATE continuum_status_rails
                   SET create_state='uncertain', updated_at=?, last_error=?, last_attempt_at=?
                   WHERE origin_session=? AND create_token=?""",
                (now, safe_error, now, origin_session, create_token),
            )
            return
        conn.execute(
            """INSERT INTO continuum_status_rails(origin_session, updated_at, last_error, last_attempt_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(origin_session) DO UPDATE SET
                 updated_at=excluded.updated_at, last_error=excluded.last_error,
                 last_attempt_at=excluded.last_attempt_at""",
            (origin_session, now, safe_error, now),
        )


def register_continuum_task_card(delegation_id: str, parent_session_id: str) -> bool:
    """Bind a freshly launched Continuum task to its host-owned trusted origin."""
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            """SELECT origin_session FROM async_delegations
               WHERE delegation_id=? AND parent_session_id=? AND origin_session!=''""",
            (delegation_id, parent_session_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            """INSERT INTO continuum_task_cards(
                   delegation_id, origin_session, last_attempt_at, updated_at
               ) VALUES (?, ?, NULL, ?)
               ON CONFLICT(delegation_id) DO NOTHING""",
            (delegation_id, str(row[0]), now),
        )
    return True


def list_continuum_task_card_snapshots(
    *, terminal_age_seconds: float = 86400
) -> List[Dict[str, Any]]:
    """Return one internal, trusted-origin snapshot per durable task card."""
    now = time.time()
    cutoff = now - max(0, float(terminal_age_seconds))
    reservation_cutoff = now - _STALE_RESERVATION_SECONDS
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT d.delegation_id, d.origin_session, d.state, d.dispatched_at,
                      d.completed_at, d.updated_at, d.heartbeat_at, d.delivery_state,
                      d.api_calls, d.current_tool, d.public_title, d.progress_json,
                      d.status_revision,
                      c.message_id, c.rendered_hash, c.source_revision, c.revision,
                      c.last_attempt_at, c.last_published_at, c.create_state,
                      c.create_started_at
               FROM async_delegations AS d
               INNER JOIN continuum_task_cards AS c
                 ON c.delegation_id=d.delegation_id AND c.origin_session=d.origin_session
               WHERE d.origin_session!=''
                 AND (d.state!='reserved' OR d.dispatched_at>=?)
                 AND (
                   d.state IN ('reserved','dispatched','running','stalling','finalizing','restart_pending','restarting')
                   OR d.completed_at>=?
                 )
               ORDER BY d.dispatched_at, d.delegation_id""",
            (reservation_cutoff, cutoff),
        ).fetchall()
    snapshots: List[Dict[str, Any]] = []
    for row in rows:
        try:
            progress = json.loads(row[11]) if row[11] else None
        except (TypeError, ValueError):
            progress = None
        snapshots.append(
            {
                "delegation_id": row[0],
                "origin_session": row[1],
                "row": {
                    "delegation_id": row[0],
                    "state": row[2],
                    "dispatched_at": row[3],
                    "completed_at": row[4],
                    "updated_at": row[5],
                    "heartbeat_at": row[6],
                    "delivery_state": row[7],
                    "api_calls": row[8],
                    "current_tool": row[9],
                    "public_title": row[10],
                    "progress": progress,
                    "status_revision": row[12],
                },
                "source_revision": int(row[12] or 0),
                "card": {
                    "message_id": row[13] or "",
                    "rendered_hash": row[14] or "",
                    "source_revision": int(row[15] or 0),
                    "revision": int(row[16] or 0),
                    "last_attempt_at": row[17],
                    "last_published_at": row[18],
                    "create_state": row[19] or "",
                    "create_started_at": row[20],
                },
            }
        )
    return snapshots


def continuum_task_card_delivery_state(delegation_id: str) -> str:
    """Return unmanaged, pending, or delivered for a managed task card."""
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            """SELECT d.state, d.status_revision, c.message_id,
                      c.rendered_hash, c.source_revision
               FROM async_delegations AS d
               JOIN continuum_task_cards AS c
                 ON c.delegation_id=d.delegation_id
                AND c.origin_session=d.origin_session
               WHERE d.delegation_id=?""",
            (delegation_id,),
        ).fetchone()
    if row is None:
        return "unmanaged"
    state = str(row[0] or "")
    terminal = state not in {"reserved", "running", "stalling", "finalizing"}
    delivered = (
        terminal
        and bool(str(row[2] or ""))
        and bool(str(row[3] or ""))
        and int(row[4] or 0) >= int(row[1] or 0)
    )
    return "delivered" if delivered else "pending"


def continuum_task_card_publish_due(
    delegation_id: str,
    rendered_hash: str,
    *,
    now: Optional[float] = None,
    min_interval_seconds: float = 5,
) -> bool:
    """Suppress unchanged renders and coalesce semantic card updates."""
    current = time.time() if now is None else float(now)
    with _DB_LOCK, _transaction() as conn:
        row = conn.execute(
            """SELECT rendered_hash, last_attempt_at FROM continuum_task_cards
               WHERE delegation_id=?""",
            (delegation_id,),
        ).fetchone()
        if row and row[0] == rendered_hash:
            return False
        if row and row[1] is not None and current - float(row[1]) < min_interval_seconds:
            return False
        origin = conn.execute(
            "SELECT origin_session FROM async_delegations WHERE delegation_id=?",
            (delegation_id,),
        ).fetchone()
        if origin is None or not str(origin[0] or ""):
            return False
        conn.execute(
            """INSERT INTO continuum_task_cards(
                   delegation_id, origin_session, last_attempt_at, updated_at
               ) VALUES (?, ?, ?, ?)
               ON CONFLICT(delegation_id) DO UPDATE SET
                 last_attempt_at=excluded.last_attempt_at, updated_at=excluded.updated_at""",
            (delegation_id, str(origin[0]), current, current),
        )
    return True


def claim_continuum_task_card_create(delegation_id: str, origin_session: str) -> str:
    """Reserve the only automatic create attempt for one trusted task binding."""
    token = uuid.uuid4().hex
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        owner = conn.execute(
            """SELECT 1 FROM async_delegations
               WHERE delegation_id=? AND origin_session=? AND origin_session!=''""",
            (delegation_id, origin_session),
        ).fetchone()
        if owner is None:
            return ""
        row = conn.execute(
            """SELECT message_id, create_state FROM continuum_task_cards
               WHERE delegation_id=? AND origin_session=?""",
            (delegation_id, origin_session),
        ).fetchone()
        if row and (str(row[0] or "") or str(row[1] or "")):
            return ""
        conn.execute(
            """INSERT INTO continuum_task_cards(
                   delegation_id, origin_session, create_state, create_token,
                   create_started_at, last_attempt_at, updated_at
               ) VALUES (?, ?, 'in_flight', ?, ?, ?, ?)
               ON CONFLICT(delegation_id) DO UPDATE SET
                   create_state='in_flight', create_token=excluded.create_token,
                   create_started_at=excluded.create_started_at,
                   last_attempt_at=excluded.last_attempt_at, updated_at=excluded.updated_at""",
            (delegation_id, origin_session, token, now, now, now),
        )
    return token


def mark_continuum_task_card_missing(
    delegation_id: str, origin_session: str, message_id: str
) -> bool:
    """Clear a task-card binding only after Telegram proves it is gone."""
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        cursor = conn.execute(
            """UPDATE continuum_task_cards
               SET message_id='', rendered_hash='', create_state='', create_token='',
                   create_started_at=NULL, updated_at=?
               WHERE delegation_id=? AND origin_session=? AND message_id=?""",
            (now, delegation_id, origin_session, str(message_id)),
        )
        return cursor.rowcount == 1


def record_continuum_task_card_publish(
    delegation_id: str,
    origin_session: str,
    *,
    message_id: str,
    rendered_hash: str,
    source_revision: int,
    error: str = "",
    create_token: str = "",
) -> bool:
    """Persist one accepted create/edit without accepting caller-selected routing."""
    now = time.time()
    safe_error = _sanitize_public_text(error, limit=160)
    with _DB_LOCK, _transaction() as conn:
        owner = conn.execute(
            """SELECT 1 FROM async_delegations
               WHERE delegation_id=? AND origin_session=? AND origin_session!=''""",
            (delegation_id, origin_session),
        ).fetchone()
        if owner is None:
            return False
        if create_token:
            row = conn.execute(
                """SELECT create_token FROM continuum_task_cards
                   WHERE delegation_id=? AND origin_session=?""",
                (delegation_id, origin_session),
            ).fetchone()
            if row is None or str(row[0] or "") != create_token:
                return False
        conn.execute(
            """INSERT INTO continuum_task_cards(
                   delegation_id, origin_session, message_id, rendered_hash,
                   source_revision, revision, last_attempt_at, last_published_at,
                   create_state, create_token, create_started_at, updated_at, last_error
               ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, '', '', NULL, ?, ?)
               ON CONFLICT(delegation_id) DO UPDATE SET
                   message_id=excluded.message_id,
                   rendered_hash=excluded.rendered_hash,
                   source_revision=MAX(source_revision, excluded.source_revision),
                   revision=revision+1,
                   last_attempt_at=excluded.last_attempt_at,
                   last_published_at=excluded.last_published_at,
                   create_state='', create_token='', create_started_at=NULL,
                   updated_at=excluded.updated_at, last_error=excluded.last_error""",
            (
                delegation_id,
                origin_session,
                str(message_id),
                rendered_hash,
                max(0, int(source_revision)),
                now,
                now,
                now,
                safe_error,
            ),
        )
    return True


def record_continuum_task_card_failure(
    delegation_id: str,
    origin_session: str,
    error: str,
    *,
    create_token: str = "",
) -> None:
    """Record delivery failure internally; never turn it into a chat message."""
    now = time.time()
    safe_error = _sanitize_public_text(error, limit=160)
    with _DB_LOCK, _transaction() as conn:
        if create_token:
            conn.execute(
                """UPDATE continuum_task_cards
                   SET create_state='uncertain', updated_at=?, last_error=?, last_attempt_at=?
                   WHERE delegation_id=? AND origin_session=? AND create_token=?""",
                (now, safe_error, now, delegation_id, origin_session, create_token),
            )
            return
        conn.execute(
            """UPDATE continuum_task_cards
               SET updated_at=?, last_error=?, last_attempt_at=?
               WHERE delegation_id=? AND origin_session=?""",
            (now, safe_error, now, delegation_id, origin_session),
        )


def _continuum_task_card_reconcile_handle(delegation_id: str, create_token: str) -> str:
    """Return an opaque operator handle without exposing task or routing ids."""
    if not delegation_id or not create_token:
        return ""
    return hashlib.sha256(f"{delegation_id}:{create_token}".encode()).hexdigest()[:24]


def list_continuum_task_card_reconciliations() -> List[Dict[str, Any]]:
    """List ambiguous creates using opaque handles only."""
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, create_token, create_state, create_started_at
               FROM continuum_task_cards
               WHERE message_id='' AND create_state IN ('in_flight','uncertain')
                     AND create_token!=''
               ORDER BY create_started_at, delegation_id"""
        ).fetchall()
    return [
        {
            "handle": _continuum_task_card_reconcile_handle(str(row[0]), str(row[1])),
            "state": str(row[2]),
            "age_seconds": max(0, int(now - float(row[3] or now))),
        }
        for row in rows
    ]


def reconcile_continuum_task_card_create_by_handle(
    handle: str,
    *,
    accepted_message_id: str = "",
    retry_create: bool = False,
) -> bool:
    """Resolve one ambiguous create after an operator verifies Telegram reality.

    Exactly one resolution is allowed: bind the accepted Telegram message, or
    clear the reservation so the publisher may retry after absence is proved.
    No origin, session, delegation, or create token is accepted from the caller.
    """
    candidate = str(handle or "").strip().lower()
    accepted = str(accepted_message_id or "").strip()
    if (
        len(candidate) != 24
        or bool(accepted) == bool(retry_create)
        or (accepted and not re.fullmatch(r"[1-9][0-9]{0,31}", accepted))
    ):
        return False
    now = time.time()
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT delegation_id, origin_session, create_token
               FROM continuum_task_cards
               WHERE message_id='' AND create_state IN ('in_flight','uncertain')
                     AND create_token!=''"""
        ).fetchall()
        matches = [
            row
            for row in rows
            if _continuum_task_card_reconcile_handle(str(row[0]), str(row[2]))
            == candidate
        ]
        if len(matches) != 1:
            return False
        delegation_id, origin_session, create_token = map(str, matches[0])
        owner = conn.execute(
            """SELECT 1 FROM async_delegations
               WHERE delegation_id=? AND origin_session=? AND origin_session!=''""",
            (delegation_id, origin_session),
        ).fetchone()
        if owner is None:
            return False
        if accepted:
            cursor = conn.execute(
                """UPDATE continuum_task_cards
                   SET message_id=?, rendered_hash='', source_revision=0,
                       revision=revision+1, last_published_at=?, last_attempt_at=?,
                       create_state='', create_token='', create_started_at=NULL,
                       updated_at=?, last_error=''
                   WHERE delegation_id=? AND origin_session=? AND create_token=?
                         AND message_id=''""",
                (
                    accepted,
                    now,
                    now,
                    now,
                    delegation_id,
                    origin_session,
                    create_token,
                ),
            )
        else:
            cursor = conn.execute(
                """UPDATE continuum_task_cards
                   SET create_state='', create_token='', create_started_at=NULL,
                       last_attempt_at=NULL, updated_at=?, last_error=''
                   WHERE delegation_id=? AND origin_session=? AND create_token=?
                         AND message_id=''""",
                (now, delegation_id, origin_session, create_token),
            )
        return cursor.rowcount == 1


def list_durable_delegations(
    limit: int = 20, *, origin_session: str | None = None
) -> List[Dict[str, Any]]:
    """Return newest durable task statuses for read-only operator surfaces.

    Deliberately excludes goals, context, origin identifiers, and ownership
    metadata. Result payloads stay available for exact-ID capability lookup.
    """
    bounded = max(1, min(int(limit), 50))
    where = " WHERE origin_session=?" if origin_session else ""
    params: tuple[Any, ...] = (origin_session, bounded) if origin_session else (bounded,)
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            f"""SELECT delegation_id, state, dispatched_at, completed_at,
                       updated_at, result_json, delivery_state, delivery_attempts
                FROM async_delegations{where}
                ORDER BY dispatched_at DESC, delegation_id DESC LIMIT ?""",
            params,
        ).fetchall()
    return [
        {
            "delegation_id": row[0],
            "state": row[1],
            "dispatched_at": row[2],
            "completed_at": row[3],
            "updated_at": row[4],
            "result": json.loads(row[5]) if row[5] else None,
            "delivery_state": row[6],
            "delivery_attempts": row[7],
        }
        for row in rows
    ]


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
        "result": json.loads(row[4]) if row[4] else None,
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
        _records[delegation_id] = record

    _persist_dispatch(record)
    executor = _get_executor(max_async_children)

    def _worker() -> None:
        result: Dict[str, Any] = {}
        status = "error"
        context_token = _CURRENT_DELEGATION_ID.set(delegation_id)
        try:
            result = runner() or {}
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
            status = "error"
        finally:
            try:
                _finalize(delegation_id, result, status)
            finally:
                _CURRENT_DELEGATION_ID.reset(context_token)

    try:
        # Propagate the dispatching profile so the detached child resolves
        # get_hermes_home() under the right profile.
        executor.submit(propagate_context_to_thread(_worker))
    except Exception as exc:  # pragma: no cover — pool submit failure is rare
        with _records_lock:
            _records.pop(delegation_id, None)
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

    _push_completion_event(event_record, result, status)
    _finish_finalization(delegation_id, status)


def _begin_finalization(
    delegation_id: str,
) -> Optional[tuple[Dict[str, Any], Optional[Callable[[], None]]]]:
    """Atomically claim terminal delivery while keeping the record active."""
    with _records_lock:
        record = _records.get(delegation_id)
        if record is None or record.get("status") not in ("running", "stalling"):
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


def _finish_finalization(delegation_id: str, status: str) -> None:
    with _records_lock:
        record = _records.get(delegation_id)
        if record is not None:
            record["status"] = status
        _prune_completed_locked()


def _push_completion_event(
    record: Dict[str, Any], result: Dict[str, Any], status: str
) -> None:
    """Push a type='async_delegation' event onto the shared completion queue.

    Best-effort: a failure here must not crash the worker, but it WOULD mean a
    silently-lost result, so we log loudly.
    """
    try:
        from tools.process_registry import process_registry
    except Exception as exc:  # pragma: no cover
        logger.error(
            "Async delegation %s finished but process_registry import failed; "
            "result lost: %s",
            record.get("delegation_id"), exc,
        )
        return

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
    _persist_completion(evt, result)
    try:
        process_registry.completion_queue.put(evt)
    except Exception as exc:  # pragma: no cover
        logger.error(
            "Async delegation %s: failed to enqueue completion event; "
            "result lost: %s",
            record.get("delegation_id"), exc,
        )


def dispatch_async_delegation_batch(
    *,
    goals: List[str],
    context: Optional[str],
    toolsets: Optional[List[str]],
    role: str,
    model: Optional[str],
    session_key: str,
    parent_session_id: Optional[str] = None,
    runner: Callable[[], Dict[str, Any]],
    origin_ui_session_id: str = "",
    origin_session_id: str = "",
    interrupt_fn: Optional[Callable[[], None]] = None,
    max_async_children: int = _DEFAULT_MAX_ASYNC_CHILDREN,
    delegation_id: Optional[str] = None,
    progress_fn: Optional[Callable[[], tuple]] = None,
    child_session_ids: Optional[List[str]] = None,
    child_capability_names: Optional[List[List[str]]] = None,
    restart_policy: str = "",
    resume_claim: bool = False,
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
        "context": context,
        "toolsets": list(toolsets) if toolsets else None,
        "role": role,
        "model": model,
        "session_key": session_key,
        "origin_ui_session_id": origin_ui_session_id,
        "origin_session_id": origin_session_id,
        "parent_session_id": parent_session_id,
        "child_session_ids": list(child_session_ids or []),
        "child_capability_names": [
            sorted({str(name) for name in names if str(name)})
            for names in (child_capability_names or [])
        ],
        "restart_policy": restart_policy,
        "resume_claim": resume_claim,
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
        _records[delegation_id] = record

    if not _persist_dispatch(record):
        with _records_lock:
            _records.pop(delegation_id, None)
        return {
            "status": "rejected",
            "error": "Restart claim is no longer active",
        }
    executor = _get_executor(max_async_children)

    def _worker() -> None:
        combined: Dict[str, Any] = {}
        status = "error"
        context_token = _CURRENT_DELEGATION_ID.set(delegation_id)
        try:
            combined = runner() or {}
            _mark_runner_returned(delegation_id)
            # Batch status: completed unless every child errored/was interrupted.
            child_results = combined.get("results") or []
            if child_results and all(
                (r.get("status") not in ("completed", "success"))
                for r in child_results
            ):
                status = "error"
            else:
                status = "completed"
        except Exception as exc:  # noqa: BLE001 — must never crash the worker
            logger.exception("Async delegation batch %s crashed", delegation_id)
            combined = {
                "results": [],
                "error": f"{type(exc).__name__}: {exc}",
                "total_duration_seconds": round(time.time() - dispatched_at, 2),
            }
            status = "error"
        finally:
            try:
                _finalize_batch(delegation_id, combined, status)
            finally:
                _CURRENT_DELEGATION_ID.reset(context_token)

    try:
        # Propagate the dispatching profile to the detached batch children.
        executor.submit(propagate_context_to_thread(_worker))
    except Exception as exc:  # pragma: no cover
        with _records_lock:
            _records.pop(delegation_id, None)
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

    _push_batch_completion_event(event_record, combined, status)
    _finish_finalization(delegation_id, status)


def _push_batch_completion_event(
    event_record: Dict[str, Any], combined: Dict[str, Any], status: str
) -> None:
    """Push a combined async-delegation batch completion event."""
    try:
        from tools.process_registry import process_registry
    except Exception as exc:  # pragma: no cover
        logger.error(
            "Async delegation batch %s finished but process_registry import "
            "failed; result lost: %s",
            event_record.get("delegation_id"), exc,
        )
        return

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
    _persist_completion(evt, combined)
    try:
        process_registry.completion_queue.put(evt)
    except Exception as exc:  # pragma: no cover
        logger.error(
            "Async delegation batch %s: failed to enqueue completion event; "
            "result lost: %s",
            event_record.get("delegation_id"), exc,
        )


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
        stalled: List[tuple] = []  # (delegation_id, is_batch, quiet_for, in_tool)
        expired: List[str] = []  # stalling past grace → force-finalize
        telemetry: List[tuple[str, Any, bool]] = []
        any_monitorable = False
        with _records_lock:
            for record in _records.values():
                status = record.get("status")
                if status == "stalling":
                    any_monitorable = True
                    interrupted_at = record.get("_interrupted_at") or now
                    if now - interrupted_at >= _STALL_GRACE_SECONDS:
                        expired.append(record["delegation_id"])
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
                telemetry.append((record["delegation_id"], token, bool(in_tool)))
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
                            bool(record.get("is_batch")),
                            quiet_for,
                            in_tool,
                        )
                    )
        for delegation_id, token, in_tool in telemetry:
            try:
                _persist_progress_telemetry(delegation_id, token, in_tool)
            except Exception:
                logger.debug(
                    "Async delegation %s telemetry persistence failed",
                    delegation_id,
                    exc_info=True,
                )
        for delegation_id, _is_batch, quiet_for, in_tool in stalled:
            logger.warning(
                "Async delegation %s made no progress for %.0fs "
                "(in_tool=%s) — interrupting; grace window %.0fs",
                delegation_id, quiet_for, in_tool, _STALL_GRACE_SECONDS,
            )
            with _records_lock:
                record = _records.get(delegation_id)
                fn = record.get("interrupt_fn") if record else None
            if callable(fn):
                try:
                    fn()
                except Exception as exc:
                    logger.debug(
                        "Async delegation %s stall interrupt failed: %s",
                        delegation_id, exc,
                    )
        for delegation_id in expired:
            _finalize_stalled(delegation_id)
        if not any_monitorable:
            return


def _finalize_stalled(delegation_id: str) -> None:
    """Force-finalize a stalling delegation whose runner never returned."""
    claimed = _begin_finalization(delegation_id)
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
        _push_batch_completion_event(
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
        _push_completion_event(
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
    _finish_finalization(delegation_id, "stalled")


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
    can't keep burning tokens with no one listening. Explicit stops are first
    durably terminalized; restart-enabled gateway-drain interruptions are
    durably deferred for same-id recovery and emit no intermediate outcome.
    """
    with _records_lock:
        targets = [
            r for r in _records.values()
            if r.get("status") in ("running", "stalling")
        ]
    terminalized = (
        set()
        if reason.startswith("gateway shutdown")
        else _interrupt_pending_restarts(reason, all_rows=True)
    )
    signalled: set[str] = set()
    for r in targets:
        r["_interrupt_reason"] = (
            "gateway_drain" if reason.startswith("gateway shutdown") else reason
        )
        if r["_interrupt_reason"] == "gateway_drain":
            # Persist restart_pending before the hard child interrupt can race
            # through normal interrupted finalization and publish a terminal.
            defer_restartable_interruption(r["delegation_id"], "gateway_drain")
        fn = r.get("interrupt_fn")
        if callable(fn):
            try:
                fn()
                signalled.add(str(r.get("delegation_id") or ""))
            except Exception as exc:
                logger.debug(
                    "interrupt_all: %s interrupt failed: %s",
                    r.get("delegation_id"), exc,
                )
    count = len((terminalized | signalled) - {""})
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
    terminalized = _interrupt_pending_restarts(
        reason,
        session_key=session_key,
        origin_ui_session_id=origin_ui_session_id,
        parent_session_id=parent_session_id,
    )
    signalled: set[str] = set()
    for r in targets:
        fn = r.get("interrupt_fn")
        if callable(fn):
            try:
                fn()
                signalled.add(str(r.get("delegation_id") or ""))
            except Exception as exc:
                logger.debug(
                    "interrupt_for_session: %s interrupt failed: %s",
                    r.get("delegation_id"), exc,
                )
    count = len((terminalized | signalled) - {""})
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
