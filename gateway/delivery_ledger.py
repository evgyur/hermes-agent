"""Durable delivery-obligation ledger for gateway final responses.

A final agent response that was generated but not yet confirmed-delivered
to the messaging platform is the one artifact the gateway can lose without
a trace: the turn already burned its tokens, the text exists only in a
Python local, and a crash / planned restart between finalize and platform
ACK drops it silently (#58818, #41696, #63695).

This module records a small durable row per outbound final response in the
shared ``state.db`` (same file and conventions as
``tools.async_delegation`` — WAL, owner pid + process-start-time liveness,
bounded retention). The gateway writes three checkpoints around the send:

    record_obligation()   state='pending'     before any send attempt
    mark_attempting()     state='attempting'  immediately before the await
    mark_delivered() /    state='delivered'   only on SendResult.success
    mark_failed()         state='failed'      on a definitive rejection

On startup, ``sweep_recoverable()`` claims rows whose owning process is
dead and hands them to the gateway for redelivery. After a platform adapter
reconnects without a process restart, ``sweep_failed_for_runtime()`` may claim
only the same live process's explicitly allowlisted transient failures. Crash
semantics are explicit about ambiguity (the contract review of the earlier
delivery-outbox attempt, #61790, closed it for silently resending ambiguous
sends):

- ``pending``     — the send never started: redeliver plainly, no dup risk.
- ``attempting``  — crashed mid-await: the platform MAY already have the
  message. Redelivered WITH a visible recovered-reply marker so the
  contract is honest at-least-once, never a silent duplicate.
- ``failed``      — definitively rejected once; the restart is a natural
  retry boundary. Also carries the marker.
- ``delivered``   — nothing to do; retention prunes.

Poison rows cannot spin: attempts are capped, stale rows expire, and both
transition to ``abandoned`` (kept briefly for inspection, then pruned).

Everything here is best-effort by design: ledger failures must never block
or delay an actual send. Callers wrap every call in try/except.
"""

from __future__ import annotations

import hashlib
import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_DB_LOCK = threading.Lock()

# Redelivery policy knobs (module constants; deliberately not config — the
# ledger itself is gated by ``gateway.delivery_ledger`` and these bounds
# only matter in the rare recovery path).
MAX_ATTEMPTS = 3
STALE_AFTER_SECONDS = 24 * 60 * 60
_RETENTION_SECONDS = 7 * 24 * 60 * 60
_MAX_ROWS = 500
_MAX_UNDELIVERED_ROWS = 2000
_MAX_UNDELIVERED_BYTES = 128 * 1024 * 1024


class DeliveryLedgerCapacityError(RuntimeError):
    """The durable outbox is full; an unreceipted send must not proceed."""


@dataclass(frozen=True)
class ObligationRecordResult:
    """Transactional admission/duplicate disposition for one exact effect."""

    disposition: str
    claim_token: str = ""

# Visible prefix for redeliveries that might duplicate an already-received
# message (crash mid-send / post-rejection retry). Honest at-least-once.
RECOVERED_MARKER = (
    "♻️ Recovered reply — the gateway restarted during delivery, "
    "so this may be a duplicate:\n\n"
)

# Runtime recovery uses a distinct marker because no gateway restart occurred.
# Keep the ambiguity explicit: a network rejection normally means the platform
# did not accept the message, but an acknowledgement can be lost independently.
RECONNECTED_MARKER = (
    "♻️ Recovered reply — the messaging platform reconnected after the original "
    "delivery failed, so this may be a duplicate:\n\n"
)

# Runtime replay is deliberately fail-closed. Only errors whose send contract
# proves they are transient reconnect failures belong here; permanent rejects
# (blocked bot, bad auth, missing chat) must not be retried merely because an
# adapter reconnected.
_RUNTIME_RETRYABLE_ERRORS = frozenset({"send_path_degraded"})


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

    apply_wal_with_fallback(conn, db_label="state.db (delivery_ledger)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS delivery_obligations (
            obligation_id TEXT PRIMARY KEY,
            session_key TEXT NOT NULL,
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            thread_id TEXT,
            content TEXT NOT NULL,
            state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            owner_pid INTEGER,
            owner_started_at INTEGER,
            last_error TEXT,
            adapter_profile TEXT
        )"""
    )
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(delivery_obligations)")
    }
    for name, sql_type in (
        ("adapter_profile", "TEXT"),
        ("resume_task_id", "TEXT NOT NULL DEFAULT ''"),
        ("continuation_generation", "INTEGER NOT NULL DEFAULT 0"),
        ("continuation_claim_owner", "TEXT NOT NULL DEFAULT ''"),
        ("continuation_claim_token", "TEXT NOT NULL DEFAULT ''"),
        ("runtime_claim_token", "TEXT NOT NULL DEFAULT ''"),
        ("route_envelope_json", "TEXT"),
    ):
        if name not in columns:
            try:
                conn.execute(
                    f"ALTER TABLE delivery_obligations ADD COLUMN {name} {sql_type}"
                )
            except sqlite3.OperationalError as exc:
                # Concurrent first-use connections can both observe the old schema.
                if "duplicate column" not in str(exc).lower():
                    raise


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit/rollback on exit, and ALWAYS close it.

    ``sqlite3.Connection.__enter__``/``__exit__`` only commit or roll back the
    transaction; they do not close the connection. Using ``with _connect()``
    alone therefore leaks a connection — and its WAL/SHM file descriptors — on
    every call, deferring the close to the garbage collector. On a long-running
    gateway that exhausts ``RLIMIT_NOFILE`` (the cron-ledger sibling of this
    bug was #69567 / PR #69594). ``record_obligation`` runs on every outbound
    final response, so this ledger is the highest-frequency leaker.
    """
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _owner_stamp() -> tuple[int, Optional[int]]:
    pid = os.getpid()
    try:
        from gateway.status import get_process_start_time

        return pid, get_process_start_time(pid)
    except Exception:
        return pid, None


def _owner_alive(pid: Any, started_at: Any) -> bool:
    """True when the recorded owning process still exists (pid + start time)."""
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        from gateway.status import get_process_start_time

        current_start = get_process_start_time(pid)
    except Exception:
        current_start = None
    if current_start is None:
        # No such process (or unreadable) — treat unreadable-but-extant
        # processes as alive only if the pid exists. Route through the
        # cross-platform probe: ``os.kill(pid, 0)`` on Windows is NOT a
        # no-op (bpo-14484 — CPython maps sig=0 to
        # ``GenerateConsoleCtrlEvent(0, pid)``), so a raw probe here could
        # Ctrl+C the gateway's own console group whenever psutil failed to
        # read the start time of a live pid. ``_pid_exists`` keeps the
        # EPERM-means-alive semantics (exists but owned by another user).
        try:
            from gateway.status import _pid_exists
        except Exception:
            if os.name == "nt":
                # Never fall back to a raw sig-0 probe on Windows.
                return False
            try:
                os.kill(pid, 0)  # windows-footgun: ok — POSIX-only fallback branch
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            except OSError:
                return False
            return True
        try:
            return bool(_pid_exists(pid))
        except Exception:
            return False
    if started_at is None:
        return True
    try:
        return int(current_start) == int(started_at)
    except (TypeError, ValueError):
        return True


def compute_obligation_id(session_key: str, message_ref: str, content: str) -> str:
    """Stable id: same turn + same content re-records idempotently, while
    distinct threads/topics on the same chat can never collide (the
    session_key carries platform, chat and thread; ``message_ref`` is the
    triggering inbound message id, distinguishing turns in one session)."""
    payload = f"{session_key}|{message_ref}|{content}"
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:24]


def record_obligation(
    *,
    obligation_id: str,
    session_key: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str],
    content: str,
    adapter_profile: Optional[str] = None,
    resume_task_id: str = "",
    continuation_generation: int = 0,
    continuation_claim_owner: str = "",
    continuation_claim_token: str = "",
    route_envelope: Optional[Dict[str, Any]] = None,
) -> ObligationRecordResult:
    """Record a final response as owed to the platform (state='pending')."""
    now = time.time()
    stored_profile = str(adapter_profile).strip() if adapter_profile else "default"
    pid, started = _owner_stamp()
    envelope_json = None
    if route_envelope is not None:
        if str(platform) == "telegram":
            from gateway.telegram_egress_policy import canonical_route_envelope

            route_envelope = canonical_route_envelope(route_envelope)
        envelope_json = json.dumps(
            route_envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    with _DB_LOCK, _transaction() as conn:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """SELECT session_key, platform, chat_id, thread_id, content, state
               FROM delivery_obligations WHERE obligation_id=?""",
            (obligation_id,),
        ).fetchone()
        if existing is not None:
            expected = (
                session_key,
                platform,
                str(chat_id),
                str(thread_id) if thread_id else None,
                content,
            )
            if tuple(existing[:5]) != expected:
                raise ValueError(
                    "delivery obligation id collision with different payload"
                )
            state = str(existing[5] or "")
            if state in {"pending", "failed"}:
                claim_token = uuid.uuid4().hex
                changed = conn.execute(
                    """UPDATE delivery_obligations
                       SET state='attempting', attempts=attempts+1,
                           owner_pid=?, owner_started_at=?, runtime_claim_token=?,
                           updated_at=?, last_error=NULL
                       WHERE obligation_id=? AND state=?""",
                    (pid, started, claim_token, now, obligation_id, state),
                )
                if changed.rowcount == 1:
                    return ObligationRecordResult("retry_claimed", claim_token)
                return ObligationRecordResult("attempting")
            return ObligationRecordResult(state or "attempting")
        pending_count, pending_bytes = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(
                     LENGTH(CAST(content AS BLOB))
                     + LENGTH(CAST(session_key AS BLOB))
                     + LENGTH(CAST(chat_id AS BLOB))
                   ), 0)
               FROM delivery_obligations
               WHERE state NOT IN ('delivered','abandoned')"""
        ).fetchone()
        planned_bytes = len(content.encode("utf-8")) + len(
            session_key.encode("utf-8")
        ) + len(str(chat_id).encode("utf-8"))
        if (
            int(pending_count or 0) >= _MAX_UNDELIVERED_ROWS
            or int(pending_bytes or 0) + planned_bytes
            > _MAX_UNDELIVERED_BYTES
        ):
            raise DeliveryLedgerCapacityError(
                "delivery outbox capacity is full; final was not sent"
            )
        conn.execute(
            """INSERT INTO delivery_obligations
               (obligation_id, session_key, platform, chat_id, thread_id,
                content, state, attempts, created_at, updated_at,
                owner_pid, owner_started_at, adapter_profile, resume_task_id,
                continuation_generation, continuation_claim_owner,
                continuation_claim_token, runtime_claim_token,
                route_envelope_json)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)""",
            (obligation_id, session_key, platform, str(chat_id),
             str(thread_id) if thread_id else None, content, now, now,
             pid, started, stored_profile, str(resume_task_id or ""),
             int(continuation_generation or 0),
             str(continuation_claim_owner or ""),
             str(continuation_claim_token or ""), envelope_json),
        )
    _prune()
    return ObligationRecordResult("created")


def mark_attempting(obligation_id: str) -> None:
    _update_state(obligation_id, "attempting")


def mark_delivered(obligation_id: str) -> None:
    _update_state(obligation_id, "delivered")


def mark_failed(obligation_id: str, error: str = "") -> None:
    _update_state(obligation_id, "failed", error=error)


def release_runtime_claim(obligation_id: str, error: str = "") -> bool:
    """Return an unsent runtime claim to ``failed`` without spending an attempt.

    Runtime recovery claims before clearing ``resume_pending`` so that two
    reconnect paths cannot send the same row. If the session flag cannot be
    cleared, no platform send was attempted and the claim must not consume the
    bounded redelivery budget. Release is fail-closed to the exact current
    process instance and the ``attempting`` state.
    """
    pid, started = _owner_stamp()
    if started is None:
        return False
    with _DB_LOCK, _transaction() as conn:
        cursor = conn.execute(
            """UPDATE delivery_obligations
               SET state='failed', attempts=CASE
                       WHEN attempts > 0 THEN attempts - 1 ELSE 0 END,
                   updated_at=?, last_error=?
               WHERE obligation_id=? AND state='attempting'
                 AND owner_pid IS ? AND owner_started_at IS ?""",
            (time.time(), error[:500] if error else None,
             obligation_id, pid, started),
        )
    return bool(cursor.rowcount)


def mark_abandoned(obligation_id: str, error: str = "") -> None:
    _update_state(obligation_id, "abandoned", error=error)


def _update_state(obligation_id: str, state: str, error: str = "") -> None:
    with _DB_LOCK, _transaction() as conn:
        conn.execute(
            """UPDATE delivery_obligations
               SET state=?, updated_at=?, last_error=?
               WHERE obligation_id=?""",
            (state, time.time(), error[:500] if error else None, obligation_id),
        )


def _route_envelope_for_replay(
    platform: str,
    chat_id: Any,
    thread_id: Any,
    route_envelope_json: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Decode and validate durable recipient provenance before replay."""
    if platform == "telegram":
        if not route_envelope_json:
            raise ValueError("ambiguous_route_envelope")
        from gateway.telegram_egress_policy import (
            assert_recipient_allowed,
            canonical_route_envelope,
        )

        route_envelope = canonical_route_envelope(
            json.loads(route_envelope_json)
        )
        if (
            route_envelope["chat_id"] != str(chat_id)
            or route_envelope["platform"] != str(platform)
            or route_envelope["thread_id"]
            != (str(thread_id) if thread_id is not None else None)
        ):
            raise ValueError("ambiguous_route_envelope")
        assert_recipient_allowed(chat_id)
        return route_envelope
    if not route_envelope_json:
        return None
    decoded = json.loads(route_envelope_json)
    return decoded if isinstance(decoded, dict) else None


def sweep_recoverable(
    now: Optional[float] = None,
    *,
    deliverable_platforms: Optional[set] = None,
    deliverable_targets: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Claim undelivered rows owned by dead processes; return them for
    redelivery.

    Claiming atomically re-stamps the owner to THIS process and increments
    ``attempts``, so a second gateway racing the same sweep cannot
    double-claim (the UPDATE is guarded on the previous owner stamp).
    Rows over the attempts cap or older than the stale cutoff transition to
    'abandoned' instead of being returned.

    ``deliverable_platforms`` (platform value strings) restricts claiming to
    platforms the caller can actually send on this boot.  ``attempts`` is the
    redelivery budget, so it must only be spent on a real send: a platform
    that failed to connect would otherwise burn one attempt per boot and hit
    the cap having never been sent once.  Rows for absent platforms are left
    untouched for a later boot; the stale cutoff still bounds them.

    ``deliverable_targets`` further scopes multiplexed gateways by exact
    ``(platform, adapter_profile)`` identity, preventing one connected bot from
    spending another disconnected bot's retry budget.
    """
    now = now if now is not None else time.time()
    pid, started = _owner_stamp()
    claimed: List[Dict[str, Any]] = []
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT obligation_id, session_key, platform, chat_id, thread_id,
                      content, state, attempts, created_at,
                      owner_pid, owner_started_at, adapter_profile,
                      resume_task_id,
                      continuation_generation, continuation_claim_owner,
                      continuation_claim_token, route_envelope_json
               FROM delivery_obligations
               WHERE state IN ('pending', 'attempting', 'failed')"""
        ).fetchall()
        for (oid, session_key, platform, chat_id, thread_id, content, state,
             attempts, created_at, owner_pid, owner_started_at,
             adapter_profile, resume_task_id,
             continuation_generation, continuation_owner,
             continuation_token, route_envelope_json) in rows:
            if _owner_alive(owner_pid, owner_started_at):
                continue  # a live gateway still owns this row
            if attempts >= MAX_ATTEMPTS or (now - created_at) > STALE_AFTER_SECONDS:
                conn.execute(
                    """UPDATE delivery_obligations
                       SET state='abandoned', updated_at=? WHERE obligation_id=?""",
                    (now, oid),
                )
                continue
            if (
                deliverable_platforms is not None
                and platform not in deliverable_platforms
            ):
                # No adapter for this platform this boot — the caller cannot
                # send, so claiming would spend an attempt on a no-op.
                continue
            if (
                deliverable_targets is not None
                and (platform, adapter_profile) not in deliverable_targets
            ):
                continue
            route_envelope = None
            try:
                route_envelope = _route_envelope_for_replay(
                    platform, chat_id, thread_id, route_envelope_json
                )
            except Exception as exc:
                if platform == "telegram":
                    error = str(exc) or "ambiguous_route_envelope"
                    if "denied" not in error:
                        error = "ambiguous_route_envelope"
                    conn.execute(
                        """UPDATE delivery_obligations
                           SET state='abandoned', updated_at=?, last_error=?
                           WHERE obligation_id=?""",
                        (now, error, oid),
                    )
                    continue
                route_envelope = None
            cursor = conn.execute(
                """UPDATE delivery_obligations
                   SET owner_pid=?, owner_started_at=?, attempts=attempts+1,
                       updated_at=?
                   WHERE obligation_id=? AND (owner_pid IS ? OR owner_pid=?)""",
                (pid, started, now, oid, owner_pid, owner_pid),
            )
            if cursor.rowcount:
                claimed.append({
                    "obligation_id": oid,
                    "session_key": session_key,
                    "platform": platform,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "content": content,
                    # pending = send never started, redeliver plainly;
                    # attempting/failed = ambiguous or rejected, carry marker.
                    "needs_marker": state != "pending",
                    "profile": adapter_profile,
                    "attempts": attempts + 1,
                    "resume_task_id": str(resume_task_id or ""),
                    "continuation_generation": int(continuation_generation or 0),
                    "continuation_claim_owner": str(continuation_owner or ""),
                    "continuation_claim_token": str(continuation_token or ""),
                    "route_envelope": route_envelope,
                })
    return claimed


def sweep_failed_for_runtime(
    platform: str,
    now: Optional[float] = None,
    *,
    profile: Optional[str] = None,
    transport_profile: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Claim this process's reconnect-retryable failed rows for one adapter.

    ``transport_profile`` scopes Telegram replay using the immutable durable
    route envelope. ``profile`` remains the compatibility selector for older
    callers and non-Telegram rows whose adapter owner is stored separately.

    Startup recovery intentionally ignores rows owned by a live gateway. That
    protects concurrent processes, but it also means a final response rejected
    with ``send_path_degraded`` remains stranded when only the platform adapter
    reconnects. This runtime sweep closes that gap without weakening ownership:

    - only rows stamped to this exact process instance are eligible;
    - only explicitly allowlisted transient errors are eligible;
    - attempts/staleness bounds match startup recovery;
    - every update is guarded by the prior owner stamp and ``failed`` state.

    Unowned rows and rows owned by another process are left untouched for the
    normal startup/dead-owner sweep. Claimed rows always carry the reconnect
    marker because the failed send's acknowledgement is not safe to infer.
    """
    now = now if now is not None else time.time()
    expected_transport = (
        str(transport_profile).strip()
        if transport_profile is not None
        else None
    )
    pid, started = _owner_stamp()
    if started is None:
        # PID equality alone cannot distinguish this process from a stale row
        # left by an earlier process incarnation after PID reuse. Runtime replay
        # is optional recovery, so fail closed when the process fingerprint is
        # unavailable; startup recovery remains the durable fallback.
        return []
    claimed: List[Dict[str, Any]] = []
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT obligation_id, session_key, platform, chat_id, thread_id,
                      content, attempts, created_at, owner_pid,
                      owner_started_at, last_error, adapter_profile,
                      resume_task_id, continuation_generation,
                      continuation_claim_owner, continuation_claim_token,
                      route_envelope_json
               FROM delivery_obligations
               WHERE state='failed' AND platform=?""",
            (platform,),
        ).fetchall()
        for (
            oid,
            session_key,
            row_platform,
            chat_id,
            thread_id,
            content,
            attempts,
            created_at,
            owner_pid,
            owner_started_at,
            last_error,
            adapter_profile,
            resume_task_id,
            continuation_generation,
            continuation_owner,
            continuation_token,
            route_envelope_json,
        ) in rows:
            expected_profile = (
                "default" if not profile or profile == "default" else str(profile)
            )
            # New Telegram reconnects are selected by immutable route
            # provenance. Legacy callers still use adapter_profile, preserving
            # compatibility without allowing that mutable column to override a
            # transport stamped into the exact route envelope.
            if expected_transport is None and adapter_profile != expected_profile:
                continue
            raw_route = None
            if row_platform == "telegram":
                try:
                    raw_route = json.loads(route_envelope_json or "")
                    if not isinstance(raw_route, dict):
                        raise ValueError("ambiguous_route_envelope")
                    from gateway.telegram_egress_policy import (
                        canonical_route_envelope,
                    )

                    canonical_route = canonical_route_envelope(raw_route)
                    if (
                        canonical_route["platform"] != row_platform
                        or canonical_route["chat_id"] != str(chat_id)
                        or canonical_route["thread_id"]
                        != (str(thread_id) if thread_id is not None else None)
                    ):
                        raise ValueError("ambiguous_route_envelope")
                    if (
                        expected_transport is not None
                        and canonical_route["transport_profile"]
                        != expected_transport
                    ):
                        continue
                except Exception as exc:
                    raw_transport = (
                        raw_route.get("transport_profile")
                        if isinstance(raw_route, dict)
                        else None
                    )
                    owns_route = (
                        expected_transport is None
                        or (
                            isinstance(raw_transport, str)
                            and raw_transport == expected_transport
                            and raw_transport.strip() == raw_transport
                        )
                    )
                    if owns_route:
                        error = str(exc) or "ambiguous_route_envelope"
                        if "denied" not in error:
                            error = "ambiguous_route_envelope"
                        conn.execute(
                            """UPDATE delivery_obligations
                               SET state='abandoned', updated_at=?, last_error=?
                               WHERE obligation_id=? AND state='failed'
                                 AND owner_pid IS ? AND owner_started_at IS ?""",
                            (now, error, oid, owner_pid, owner_started_at),
                        )
                    continue
            # Runtime reconnect recovery may act only on its own rows. Exact
            # process-start matching prevents PID reuse from stealing work.
            if owner_pid != pid or owner_started_at != started:
                continue
            if str(last_error or "").strip().lower() not in _RUNTIME_RETRYABLE_ERRORS:
                continue
            owner_guard = (oid, owner_pid, owner_started_at)
            if attempts >= MAX_ATTEMPTS or (now - created_at) > STALE_AFTER_SECONDS:
                conn.execute(
                    """UPDATE delivery_obligations
                       SET state='abandoned', updated_at=?
                       WHERE obligation_id=? AND state='failed'
                         AND owner_pid IS ? AND owner_started_at IS ?""",
                    (now, *owner_guard),
                )
                continue
            try:
                route_envelope = _route_envelope_for_replay(
                    row_platform, chat_id, thread_id, route_envelope_json
                )
            except Exception as exc:
                error = str(exc) or "ambiguous_route_envelope"
                if "denied" not in error:
                    error = "ambiguous_route_envelope"
                conn.execute(
                    """UPDATE delivery_obligations
                       SET state='abandoned', updated_at=?, last_error=?
                       WHERE obligation_id=? AND state='failed'
                         AND owner_pid IS ? AND owner_started_at IS ?""",
                    (now, error, oid, owner_pid, owner_started_at),
                )
                continue
            claim_token = uuid.uuid4().hex
            cursor = conn.execute(
                """UPDATE delivery_obligations
                   SET state='attempting', attempts=attempts+1,
                       runtime_claim_token=?, updated_at=?
                   WHERE obligation_id=? AND state='failed'
                      AND owner_pid IS ? AND owner_started_at IS ?""",
                (claim_token, now, *owner_guard),
            )
            if cursor.rowcount:
                claimed.append({
                    "obligation_id": oid,
                    "session_key": session_key,
                    "platform": row_platform,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "content": content,
                    "needs_marker": True,
                    "marker": RECONNECTED_MARKER,
                    "profile": adapter_profile,
                    "runtime_recovery": True,
                    "attempts": attempts + 1,
                    "runtime_claim_token": claim_token,
                    "resume_task_id": str(resume_task_id or ""),
                    "continuation_generation": int(continuation_generation or 0),
                    "continuation_claim_owner": str(continuation_owner or ""),
                    "continuation_claim_token": str(continuation_token or ""),
                    "route_envelope": route_envelope,
                })
    return claimed


def abandon_runtime_claim(
    obligation_id: str,
    claim_token: str,
    error: str = "",
) -> bool:
    """Abandon only the reconnect generation represented by ``claim_token``."""
    with _DB_LOCK, _transaction() as conn:
        return bool(conn.execute(
            """UPDATE delivery_obligations
               SET state='abandoned', runtime_claim_token='', updated_at=?,
                   last_error=?
               WHERE obligation_id=? AND state='attempting'
                 AND runtime_claim_token=?""",
            (
                time.time(),
                str(error or "")[:500] or None,
                obligation_id,
                claim_token,
            ),
        ).rowcount)


def settle_runtime_claim(
    obligation_id: str,
    claim_token: str,
    *,
    delivered: bool,
    error: str = "",
) -> bool:
    """Settle only the exact reconnect claim generation."""
    state = "delivered" if delivered else "failed"
    with _DB_LOCK, _transaction() as conn:
        return bool(conn.execute(
            """UPDATE delivery_obligations
               SET state=?, runtime_claim_token='', updated_at=?, last_error=?
               WHERE obligation_id=? AND state='attempting'
                 AND runtime_claim_token=?""",
            (
                state,
                time.time(),
                None if delivered else str(error or "")[:500],
                obligation_id,
                claim_token,
            ),
        ).rowcount)


async def settle_with_retry(callable_, *args, **kwargs):
    """Retry only an idempotent post-wire ledger settlement off-loop."""
    delay = 0.01
    for attempt in range(4):
        try:
            return await asyncio.to_thread(callable_, *args, **kwargs)
        except sqlite3.OperationalError as exc:
            locked = "locked" in str(exc).lower() or "busy" in str(exc).lower()
            if not locked:
                raise
            if attempt == 3:
                task = asyncio.create_task(
                    _settle_locked_until_owned(callable_, args, kwargs)
                )
                _PENDING_SETTLEMENT_TASKS.add(task)
                task.add_done_callback(_PENDING_SETTLEMENT_TASKS.discard)
                # Give the supervised owner one short handoff slice. If the
                # lock clears on the next attempt, callers observe the settled
                # row before returning; a persistent lock still continues in
                # the retained background task without blocking wire delivery.
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
                except asyncio.TimeoutError:
                    pass
                return False
            await asyncio.sleep(delay)
            delay *= 2


_PENDING_SETTLEMENT_TASKS: set[asyncio.Task] = set()


async def _settle_locked_until_owned(callable_, args, kwargs) -> None:
    """Continue DB-only settlement after irreversible wire acceptance."""
    delay = 0.08
    while True:
        try:
            await asyncio.to_thread(callable_, *args, **kwargs)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                logger.error("delivery settlement failed permanently: %s", exc)
                return
            await asyncio.sleep(delay)
            delay = min(2.0, delay * 2)
        except Exception:
            logger.exception("delivery settlement failed permanently")
            return


def _prune(now: Optional[float] = None) -> None:
    now = now if now is not None else time.time()
    cutoff = now - _RETENTION_SECONDS
    try:
        with _transaction() as conn:
            conn.execute(
                """DELETE FROM delivery_obligations
                   WHERE state IN ('delivered', 'abandoned') AND updated_at < ?""",
                (cutoff,),
            )
            total = conn.execute(
                "SELECT COUNT(*) FROM delivery_obligations"
            ).fetchone()[0]
            excess = max(0, total - _MAX_ROWS)
            if excess:
                conn.execute(
                    """DELETE FROM delivery_obligations WHERE obligation_id IN (
                         SELECT obligation_id FROM delivery_obligations
                         WHERE state IN ('delivered','abandoned')
                         ORDER BY updated_at ASC
                         LIMIT ?)""",
                    (excess,),
                )
    except Exception:
        logger.debug("delivery ledger prune failed", exc_info=True)


def ledger_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """Read the ``gateway.delivery_ledger`` config gate (default on)."""
    try:
        if config is None:
            from hermes_cli.config import load_config

            config = load_config()
        gw = config.get("gateway") or {}
        value = gw.get("delivery_ledger", True)
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "off"}
        return bool(value)
    except Exception:
        return True


def debug_rows(limit: int = 20) -> str:
    """Human-readable dump for ad-hoc inspection (sqlite3-free path)."""
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT obligation_id, session_key, state, attempts,
                      created_at, updated_at, last_error
               FROM delivery_obligations
               ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return json.dumps(
        [
            {
                "id": r[0], "session": r[1], "state": r[2], "attempts": r[3],
                "created_at": r[4], "updated_at": r[5], "last_error": r[6],
            }
            for r in rows
        ],
        indent=2,
    )
