"""Durable parent-task barrier for required background delegations.

The barrier owns parent closure state. Legacy ``async_delegations`` rows remain
child execution/delivery records only; they are never consulted to decide
whether a parent may publish its initial answer.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from hermes_constants import get_hermes_home

_ACTIVE_CHILD_STATES = {"required", "running"}
_TERMINAL_CHILD_STATES = {
    "completed",
    "failed",
    "error",
    "timeout",
    "stalled",
    "interrupted",
    "cancelled",
    "unknown",
}
_DEFAULT_LEASE_SECONDS = 1800.0
_MAX_CONTINUATION_ATTEMPTS = 8
_MAX_TERMINAL_DELIVERY_ATTEMPTS = 8
_MAX_GENERATIONS = 8
_RETENTION_SECONDS = 7 * 24 * 60 * 60


class TrustedParentTaskContinuation(dict):
    """Host-created wake carrying one durable barrier continuation claim."""


class TrustedParentTaskDelivery(str):
    """Final text bound to an accepted continuation until platform ACK."""

    _hermes_parent_task_delivery: Dict[str, Any]

    def __new__(
        cls,
        text: str,
        *,
        barrier_id: str,
        continuation_claim: str,
        result: Dict[str, Any],
    ):
        value = str.__new__(cls, str(text or ""))
        value._hermes_parent_task_delivery = {
            "barrier_id": str(barrier_id),
            "continuation_claim": str(continuation_claim),
            "result": dict(result or {}),
        }
        return value


def _db_path() -> Path:
    return get_hermes_home() / "state.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    schema_sql = """
        CREATE TABLE IF NOT EXISTS parent_task_barriers (
            barrier_id TEXT PRIMARY KEY,
            origin_session TEXT NOT NULL,
            parent_session_id TEXT NOT NULL,
            root_turn_id TEXT NOT NULL,
            initial_owner_pid INTEGER NOT NULL DEFAULT 0,
            initial_owner_started_at INTEGER,
            state TEXT NOT NULL DEFAULT 'open',
            initial_persisted INTEGER NOT NULL DEFAULT 0,
            continuation_status TEXT NOT NULL DEFAULT 'pending',
            continuation_claim TEXT NOT NULL DEFAULT '',
            continuation_owner TEXT NOT NULL DEFAULT '',
            continuation_lease_until REAL,
            continuation_attempts INTEGER NOT NULL DEFAULT 0,
            terminal_delivery_attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL,
            accepted_turn_id TEXT NOT NULL DEFAULT '',
            accepted_owner_pid INTEGER,
            accepted_owner_started_at INTEGER,
            accepted_at REAL,
            generation INTEGER NOT NULL DEFAULT 0,
            delivery_obligation_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            closed_at REAL,
            UNIQUE(parent_session_id, root_turn_id)
        );
        CREATE INDEX IF NOT EXISTS idx_parent_task_barriers_ready
          ON parent_task_barriers(state, initial_persisted, continuation_status);
        CREATE INDEX IF NOT EXISTS idx_parent_task_barriers_origin
          ON parent_task_barriers(origin_session, state);

        CREATE TABLE IF NOT EXISTS parent_task_children (
            barrier_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            required INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL DEFAULT 'required',
            terminal_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(barrier_id, task_id),
            UNIQUE(task_id),
            FOREIGN KEY(barrier_id) REFERENCES parent_task_barriers(barrier_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_parent_task_children_barrier_state
          ON parent_task_children(barrier_id, required, state);
        CREATE TABLE IF NOT EXISTS parent_task_barrier_meta (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            schema_version INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO parent_task_barrier_meta(singleton, schema_version)
        VALUES (1, 0);
    """
    for statement in schema_sql.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)
    version_row = conn.execute(
        "SELECT schema_version FROM parent_task_barrier_meta WHERE singleton=1"
    ).fetchone()
    needs_migration = version_row is None or int(version_row[0] or 0) < 5
    barrier_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(parent_task_barriers)")
    }
    if "continuation_result_json" in barrier_columns:
        conn.execute(
            "UPDATE parent_task_barriers SET continuation_result_json=NULL "
            "WHERE continuation_result_json IS NOT NULL"
        )
    for name, definition in {
        "terminal_delivery_attempts": "INTEGER NOT NULL DEFAULT 0",
        "next_attempt_at": "REAL",
        "accepted_turn_id": "TEXT NOT NULL DEFAULT ''",
        "accepted_owner_pid": "INTEGER",
        "accepted_owner_started_at": "INTEGER",
        "accepted_at": "REAL",
        "generation": "INTEGER NOT NULL DEFAULT 0",
        "delivery_obligation_id": "TEXT NOT NULL DEFAULT ''",
        "initial_owner_pid": "INTEGER NOT NULL DEFAULT 0",
        "initial_owner_started_at": "INTEGER",
    }.items():
        if name not in barrier_columns:
            conn.execute(
                f"ALTER TABLE parent_task_barriers ADD COLUMN {name} {definition}"
            )
    if needs_migration:
        migrated_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(parent_task_barriers)")
        }
        required_columns = {
            "terminal_delivery_attempts",
            "next_attempt_at",
            "accepted_turn_id",
            "accepted_owner_pid",
            "accepted_owner_started_at",
            "accepted_at",
            "generation",
            "delivery_obligation_id",
            "initial_owner_pid",
            "initial_owner_started_at",
        }
        if not required_columns <= migrated_columns:
            raise RuntimeError("parent-task barrier migration is structurally incomplete")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("parent-task barrier migration violated foreign keys")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).lower() != "ok":
            raise RuntimeError("parent-task barrier migration failed integrity_check")
        conn.execute(
            "UPDATE parent_task_barrier_meta SET schema_version=6 "
            "WHERE singleton=1 AND schema_version<6"
        )


def _normalize_terminal_state(state: str) -> str:
    normalized = str(state or "unknown").strip().lower()
    return normalized if normalized in _TERMINAL_CHILD_STATES else "unknown"


def _prune_terminal_in_tx(conn: sqlite3.Connection, now: float) -> None:
    conn.execute(
        """DELETE FROM parent_task_barriers
           WHERE state IN ('closed','cancelled','failed')
             AND closed_at IS NOT NULL AND closed_at < ?""",
        (now - _RETENTION_SECONDS,),
    )


def _pid_is_dead(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def _current_owner_stamp() -> tuple[int, Optional[int]]:
    pid = os.getpid()
    try:
        from gateway.status import get_process_start_time

        started = get_process_start_time(pid)
        return pid, int(started) if started is not None else None
    except Exception:
        return pid, None


def _owner_is_dead(pid: int, started_at: Optional[int]) -> bool:
    if int(pid) <= 0:
        return True
    if _pid_is_dead(pid):
        return True
    if started_at is None:
        return False
    try:
        from gateway.status import get_process_start_time

        current = get_process_start_time(pid)
        return current is None or int(current) != int(started_at)
    except Exception:
        return False


def _gateway_owner_is_dead(owner: str) -> bool:
    parts = str(owner or "").split(":", 2)
    if len(parts) < 2 or parts[0] != "gateway" or not parts[1].isdigit():
        return False
    return _pid_is_dead(int(parts[1]))


def admit_required_child(
    *,
    origin_session: str,
    parent_session_id: str,
    root_turn_id: str,
    task_id: str,
    existing_barrier_id: str = "",
) -> str:
    """Durably bind a required child before its worker is submitted.

    Returns an empty string for non-turn callers. Every non-empty identity is
    mandatory; partially identified parent ownership is rejected fail closed.
    """

    identities = {
        "origin_session": str(origin_session or "").strip(),
        "parent_session_id": str(parent_session_id or "").strip(),
        "root_turn_id": str(root_turn_id or "").strip(),
        "task_id": str(task_id or "").strip(),
    }
    if not identities["root_turn_id"]:
        return ""
    missing = [name for name, value in identities.items() if not value]
    if missing:
        raise RuntimeError(
            "required-child admission lacks parent identity: " + ", ".join(missing)
        )

    now = time.time()
    owner_pid, owner_started_at = _current_owner_stamp()
    with _transaction() as conn:
        _prune_terminal_in_tx(conn, now)
        existing_id = str(existing_barrier_id or "").strip()
        if existing_id:
            row = conn.execute(
                """SELECT barrier_id, origin_session, parent_session_id,
                          state, continuation_status, generation
                   FROM parent_task_barriers WHERE barrier_id=?""",
                (existing_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("nested required child references a missing barrier")
            if (
                str(row["origin_session"]) != identities["origin_session"]
                or str(row["parent_session_id"])
                != identities["parent_session_id"]
            ):
                raise RuntimeError(
                    "nested required child changed parent barrier ownership"
                )
            if (
                str(row["state"]) != "continuing"
                or str(row["continuation_status"]) != "accepted"
            ):
                raise RuntimeError(
                    "nested required child lacks an accepted continuation"
                )
            if int(row["generation"] or 0) >= _MAX_GENERATIONS:
                raise RuntimeError("parent-task continuation generation limit reached")
            barrier_id = existing_id
            conn.execute(
                """UPDATE parent_task_barriers
                   SET root_turn_id=?, initial_owner_pid=?,
                       initial_owner_started_at=?, state='open', initial_persisted=0,
                       continuation_status='pending', continuation_claim='',
                       continuation_owner='', continuation_lease_until=NULL,
                       continuation_attempts=0, terminal_delivery_attempts=0,
                       next_attempt_at=NULL, accepted_turn_id='',
                       accepted_owner_pid=NULL, accepted_owner_started_at=NULL,
                       accepted_at=NULL,
                       delivery_obligation_id='',
                       generation=generation+1, updated_at=?
                   WHERE barrier_id=?""",
                (
                    identities["root_turn_id"],
                    owner_pid,
                    owner_started_at,
                    now,
                    barrier_id,
                ),
            )
        else:
            row = conn.execute(
                """SELECT barrier_id, origin_session FROM parent_task_barriers
                   WHERE parent_session_id=? AND root_turn_id=?""",
                (identities["parent_session_id"], identities["root_turn_id"]),
            ).fetchone()
            if row is None:
                barrier_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO parent_task_barriers(
                           barrier_id, origin_session, parent_session_id,
                           root_turn_id, initial_owner_pid,
                           initial_owner_started_at, state, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
                    (
                        barrier_id,
                        identities["origin_session"],
                        identities["parent_session_id"],
                        identities["root_turn_id"],
                        owner_pid,
                        owner_started_at,
                        now,
                        now,
                    ),
                )
            else:
                barrier_id = str(row["barrier_id"])
                if str(row["origin_session"]) != identities["origin_session"]:
                    raise RuntimeError(
                        "root turn is already bound to another origin session"
                    )
        conn.execute(
            """INSERT INTO parent_task_children(
                   barrier_id, task_id, required, state, created_at, updated_at
               ) VALUES (?, ?, 1, 'required', ?, ?)
               ON CONFLICT(barrier_id, task_id) DO NOTHING""",
            (barrier_id, identities["task_id"], now, now),
        )
        bound = conn.execute(
            "SELECT barrier_id FROM parent_task_children WHERE task_id=?",
            (identities["task_id"],),
        ).fetchone()
        if bound is None or str(bound["barrier_id"]) != barrier_id:
            raise RuntimeError("required child is already bound to another parent barrier")
    return barrier_id


def record_child_terminal_in_tx(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    state: str,
    result: Optional[Dict[str, Any]],
    now: Optional[float] = None,
) -> Optional[str]:
    """Record child closure inside the caller's existing SQLite transaction."""

    current = time.time() if now is None else float(now)
    row = conn.execute(
        "SELECT barrier_id, state FROM parent_task_children WHERE task_id=?",
        (str(task_id),),
    ).fetchone()
    if row is None:
        return None
    barrier_id = str(row[0])
    previous = str(row[1] or "")
    terminal = _normalize_terminal_state(state)
    if previous not in _TERMINAL_CHILD_STATES:
        conn.execute(
            """UPDATE parent_task_children
               SET state=?, terminal_at=?, updated_at=?
               WHERE task_id=? AND state NOT IN (
                 'completed','failed','error','timeout','stalled','interrupted',
                 'cancelled','unknown'
               )""",
            (
                terminal,
                current,
                current,
                str(task_id),
            ),
        )
    active = conn.execute(
        """SELECT COUNT(*) FROM parent_task_children
           WHERE barrier_id=? AND required=1
             AND state NOT IN (
               'completed','failed','error','timeout','stalled','interrupted',
               'cancelled','unknown'
             )""",
        (barrier_id,),
    ).fetchone()[0]
    if int(active) == 0:
        conn.execute(
            """UPDATE parent_task_barriers
               SET state=CASE WHEN state='open' THEN 'ready' ELSE state END,
                   updated_at=?
               WHERE barrier_id=? AND state IN ('open','ready')""",
            (current, barrier_id),
        )
    return barrier_id


def record_child_terminal(
    *, task_id: str, state: str, result: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    with _transaction() as conn:
        return record_child_terminal_in_tx(
            conn, task_id=task_id, state=state, result=result
        )


def finalization_policy(
    *, parent_session_id: str, root_turn_id: str, persist: bool = True
) -> Dict[str, Any]:
    """Return and persist the root-turn closure policy.

    Any matching non-terminal barrier with required children withholds the
    initial parent answer, even if all children finished unusually quickly.
    """

    parent = str(parent_session_id or "").strip()
    turn = str(root_turn_id or "").strip()
    if not parent or not turn:
        return {"action": "deliver"}
    now = time.time()
    with _transaction() as conn:
        row = conn.execute(
            """SELECT barrier_id, state FROM parent_task_barriers
               WHERE parent_session_id=? AND root_turn_id=?""",
            (parent, turn),
        ).fetchone()
        if row is None or str(row["state"]) in {"closed", "cancelled"}:
            return {"action": "deliver"}
        barrier_id = str(row["barrier_id"])
        required = conn.execute(
            """SELECT COUNT(*) FROM parent_task_children
               WHERE barrier_id=? AND required=1""",
            (barrier_id,),
        ).fetchone()[0]
        if int(required) == 0:
            return {"action": "deliver"}
        if persist:
            conn.execute(
                """UPDATE parent_task_barriers
                   SET initial_persisted=1, updated_at=? WHERE barrier_id=?""",
                (now, barrier_id),
            )
    return {
        "action": "withhold",
        "barrier_id": barrier_id,
        "defer_goal_evaluation": True,
    }


def mark_initial_persisted(barrier_id: str) -> bool:
    now = time.time()
    with _transaction() as conn:
        changed = conn.execute(
            """UPDATE parent_task_barriers
               SET initial_persisted=1, updated_at=?
               WHERE barrier_id=? AND state NOT IN ('closed','cancelled','failed')""",
            (now, str(barrier_id)),
        ).rowcount
    return changed == 1


def barrier_for_child(task_id: str) -> Optional[str]:
    with _transaction() as conn:
        row = conn.execute(
            "SELECT barrier_id FROM parent_task_children WHERE task_id=?",
            (str(task_id),),
        ).fetchone()
    return str(row["barrier_id"]) if row is not None else None


def has_active_barrier(*, origin_session: str, parent_session_id: str = "") -> bool:
    origin = str(origin_session or "").strip()
    parent = str(parent_session_id or "").strip()
    if not origin and not parent:
        return False
    clauses = []
    params: list[Any] = []
    if origin:
        clauses.append("origin_session=?")
        params.append(origin)
    if parent:
        clauses.append("parent_session_id=?")
        params.append(parent)
    with _transaction() as conn:
        row = conn.execute(
            """SELECT 1 FROM parent_task_barriers
               WHERE state NOT IN ('closed','cancelled','failed') AND ("""
            + " OR ".join(clauses)
            + ") LIMIT 1",
            params,
        ).fetchone()
    return row is not None


def _aggregate_prompt(conn: sqlite3.Connection, barrier_id: str) -> str:
    rows = conn.execute(
        """SELECT c.task_id, c.state, a.result_json
           FROM parent_task_children AS c
           LEFT JOIN async_delegations AS a
             ON a.delegation_id=c.task_id
           WHERE c.barrier_id=? AND c.required=1
           ORDER BY c.created_at, c.task_id""",
        (barrier_id,),
    ).fetchall()
    payload = []
    for row in rows:
        try:
            result = json.loads(row["result_json"] or "{}")
        except (TypeError, ValueError):
            result = {"error": "stored child result is unreadable"}
        outcome: Dict[str, Any] = {
            "task_id": str(row["task_id"]),
            "status": str(row["state"]),
        }
        if isinstance(result.get("results"), list):
            outcome["results"] = result["results"]
            if result.get("error"):
                outcome["error"] = result["error"]
        else:
            outcome.update(
                summary=result.get("summary"),
                error=result.get("error"),
                result=result.get("result"),
            )
        payload.append(outcome)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "[INTERNAL PARENT-TASK BARRIER — trusted host continuation]\n"
        "All required background children for the withheld parent turn are now "
        "terminal. Continue the original user task using the child outcomes below. "
        "Produce one final user-facing answer; do not repeat the provisional parent "
        "answer and do not emit separate child notifications.\n\n"
        f"barrier_id={barrier_id}\nrequired_child_outcomes={rendered}"
    )


def _terminal_failure_prompt(conn: sqlite3.Connection, barrier_id: str) -> str:
    return (
        "[INTERNAL PARENT-TASK BARRIER — terminal recovery]\n"
        "Automatic parent continuation recovery exhausted its bounded retry budget. "
        "Do not launch or replay child work. Give the user one truthful final technical "
        "result through the normal response path, using the already persisted child "
        "outcomes below and naming any uncertainty.\n\n"
        + _aggregate_prompt(conn, barrier_id)
    )


def claim_next_ready_continuation(
    *, owner: str, lease_seconds: float = _DEFAULT_LEASE_SECONDS
) -> Optional[TrustedParentTaskContinuation]:
    """Lease one ready barrier and return its trusted aggregate wake."""

    owner_id = str(owner or "").strip()
    if not owner_id:
        raise ValueError("continuation owner is required")
    now = time.time()
    token = uuid.uuid4().hex
    with _transaction() as conn:
        _prune_terminal_in_tx(conn, now)
        incomplete_rows = conn.execute(
            """SELECT barrier_id, initial_owner_pid, initial_owner_started_at
               FROM parent_task_barriers AS b
               WHERE b.state IN ('open','ready') AND b.initial_persisted=0
                 AND NOT EXISTS (
                   SELECT 1 FROM parent_task_children AS c
                   WHERE c.barrier_id=b.barrier_id AND c.required=1
                     AND c.state NOT IN (
                       'completed','failed','error','timeout','stalled','interrupted',
                       'cancelled','unknown'
                     )
                 )"""
        ).fetchall()
        for incomplete in incomplete_rows:
            if _owner_is_dead(
                int(incomplete["initial_owner_pid"] or 0),
                incomplete["initial_owner_started_at"],
            ):
                conn.execute(
                    """UPDATE parent_task_barriers
                       SET initial_persisted=1, state='ready', updated_at=?
                       WHERE barrier_id=? AND state IN ('open','ready')
                         AND initial_persisted=0""",
                    (now, str(incomplete["barrier_id"])),
                )
        claimed_rows = conn.execute(
            """SELECT barrier_id, continuation_claim, continuation_owner
               FROM parent_task_barriers
               WHERE state='resuming' AND continuation_status='claimed'"""
        ).fetchall()
        for claimed_row in claimed_rows:
            if _gateway_owner_is_dead(
                str(claimed_row["continuation_owner"] or "")
            ):
                conn.execute(
                    """UPDATE parent_task_barriers
                       SET state='ready', continuation_status='pending',
                           continuation_claim='', continuation_owner='',
                           continuation_lease_until=NULL, updated_at=?
                       WHERE barrier_id=? AND state='resuming'
                         AND continuation_claim=?""",
                    (
                        now,
                        str(claimed_row["barrier_id"]),
                        str(claimed_row["continuation_claim"]),
                    ),
                )
        accepted_rows = conn.execute(
            """SELECT barrier_id, continuation_claim, accepted_owner_pid,
                      accepted_owner_started_at, delivery_obligation_id
               FROM parent_task_barriers
               WHERE state='continuing' AND continuation_status='accepted'"""
        ).fetchall()
        delivery_table_exists = conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='delivery_obligations'"""
        ).fetchone() is not None
        for accepted_row in accepted_rows:
            obligation_id = str(accepted_row["delivery_obligation_id"] or "")
            delivery_state = ""
            if obligation_id and delivery_table_exists:
                delivery_row = conn.execute(
                    "SELECT state FROM delivery_obligations WHERE obligation_id=?",
                    (obligation_id,),
                ).fetchone()
                delivery_state = (
                    str(delivery_row[0] or "") if delivery_row is not None else ""
                )
            if delivery_state == "delivered":
                conn.execute(
                    """UPDATE parent_task_barriers
                       SET state='closed', continuation_status='generated',
                           continuation_lease_until=NULL, updated_at=?, closed_at=?
                       WHERE barrier_id=? AND state='continuing'
                         AND continuation_claim=?""",
                    (
                        now,
                        now,
                        str(accepted_row["barrier_id"]),
                        str(accepted_row["continuation_claim"]),
                    ),
                )
                continue
            if delivery_state not in {"failed", "abandoned"}:
                owner_pid = int(accepted_row["accepted_owner_pid"] or 0)
                owner_started = accepted_row["accepted_owner_started_at"]
                if not _owner_is_dead(owner_pid, owner_started):
                    continue
                if delivery_state in {"pending", "attempting"}:
                    continue
            conn.execute(
                """UPDATE parent_task_barriers
                   SET state='ready', continuation_status='pending',
                       continuation_claim='', continuation_owner='',
                       continuation_lease_until=NULL, accepted_turn_id='',
                       accepted_owner_pid=NULL, accepted_owner_started_at=NULL,
                       accepted_at=NULL,
                       delivery_obligation_id='',
                       updated_at=?
                   WHERE barrier_id=? AND state='continuing'
                     AND continuation_claim=?""",
                (
                    now,
                    str(accepted_row["barrier_id"]),
                    str(accepted_row["continuation_claim"]),
                ),
            )
        conn.execute(
            """UPDATE parent_task_barriers
               SET state='ready', continuation_status='pending',
                   continuation_claim='', continuation_owner='',
                   continuation_lease_until=NULL, updated_at=?
               WHERE state='resuming' AND continuation_status='claimed'
                 AND continuation_lease_until IS NOT NULL
                 AND continuation_lease_until<?""",
            (now, now),
        )
        conn.execute(
            """UPDATE parent_task_barriers
               SET state='failed', continuation_status='terminal_delivery_exhausted',
                   continuation_claim='', continuation_owner='',
                   continuation_lease_until=NULL, updated_at=?, closed_at=?
               WHERE state='ready' AND continuation_attempts>=?
                 AND terminal_delivery_attempts>=?""",
            (
                now,
                now,
                _MAX_CONTINUATION_ATTEMPTS,
                _MAX_TERMINAL_DELIVERY_ATTEMPTS,
            ),
        )
        row = conn.execute(
            """SELECT barrier_id, origin_session, parent_session_id,
                      continuation_attempts, terminal_delivery_attempts
               FROM parent_task_barriers AS b
               WHERE b.state='ready' AND b.initial_persisted=1
                 AND b.continuation_status='pending'
                 AND COALESCE(b.next_attempt_at, 0)<=?
                 AND NOT EXISTS (
                   SELECT 1 FROM parent_task_children AS c
                   WHERE c.barrier_id=b.barrier_id AND c.required=1
                     AND c.state NOT IN (
                       'completed','failed','error','timeout','stalled','interrupted',
                       'cancelled','unknown'
                     )
                 )
               ORDER BY b.created_at, b.barrier_id LIMIT 1""",
            (now,),
        ).fetchone()
        if row is None:
            return None
        barrier_id = str(row["barrier_id"])
        changed = conn.execute(
            """UPDATE parent_task_barriers
               SET state='resuming', continuation_status='claimed',
                   continuation_claim=?, continuation_owner=?,
                   continuation_lease_until=?,
                   continuation_attempts=CASE
                     WHEN continuation_attempts < ?
                     THEN continuation_attempts+1
                     ELSE continuation_attempts
                   END,
                   terminal_delivery_attempts=CASE
                     WHEN continuation_attempts >= ?
                     THEN terminal_delivery_attempts+1
                     ELSE terminal_delivery_attempts
                   END,
                   next_attempt_at=NULL,
                   updated_at=?
               WHERE barrier_id=? AND state='ready'
                 AND continuation_status='pending'""",
            (
                token,
                owner_id,
                now + max(1.0, float(lease_seconds)),
                _MAX_CONTINUATION_ATTEMPTS,
                _MAX_CONTINUATION_ATTEMPTS,
                now,
                barrier_id,
            ),
        ).rowcount
        if changed != 1:
            return None
        terminal_failure = int(row["continuation_attempts"] or 0) >= _MAX_CONTINUATION_ATTEMPTS
        prompt = (
            _terminal_failure_prompt(conn, barrier_id)
            if terminal_failure
            else _aggregate_prompt(conn, barrier_id)
        )
        return TrustedParentTaskContinuation(
            type="parent_task_continuation",
            barrier_id=barrier_id,
            continuation_claim=token,
            session_key=str(row["origin_session"]),
            origin_session=str(row["origin_session"]),
            parent_session_id=str(row["parent_session_id"]),
            terminal_failure=terminal_failure,
            text=prompt,
            synthetic_message=prompt,
        )


def release_continuation_claim(barrier_id: str, claim: str) -> bool:
    now = time.time()
    with _transaction() as conn:
        row = conn.execute(
            """SELECT continuation_attempts, terminal_delivery_attempts
               FROM parent_task_barriers
               WHERE barrier_id=? AND state='resuming'
                 AND continuation_status='claimed' AND continuation_claim=?""",
            (str(barrier_id), str(claim)),
        ).fetchone()
        if row is None:
            return False
        terminal_backoff = 0.0
        if int(row["continuation_attempts"] or 0) >= _MAX_CONTINUATION_ATTEMPTS:
            terminal_backoff = min(
                300.0,
                float(2 ** min(int(row["terminal_delivery_attempts"] or 0), 8)),
            )
        changed = conn.execute(
            """UPDATE parent_task_barriers
               SET state='ready', continuation_status='pending',
                   continuation_claim='', continuation_owner='',
                   continuation_lease_until=NULL, next_attempt_at=?, updated_at=?
               WHERE barrier_id=? AND state='resuming'
                 AND continuation_status='claimed' AND continuation_claim=?""",
            (
                now + terminal_backoff,
                now,
                str(barrier_id),
                str(claim),
            ),
        ).rowcount
    return changed == 1


def accept_continuation(
    barrier_id: str,
    claim: str,
    *,
    accepted_turn_id: str,
    owner_pid: int,
    owner_started_at: Optional[int] = None,
) -> bool:
    turn_id = str(accepted_turn_id or "").strip()
    if not turn_id or int(owner_pid) <= 0:
        return False
    if owner_started_at is None:
        try:
            from gateway.status import get_process_start_time

            started = get_process_start_time(int(owner_pid))
            owner_started_at = int(started) if started is not None else None
        except Exception:
            owner_started_at = None
    now = time.time()
    with _transaction() as conn:
        changed = conn.execute(
            """UPDATE parent_task_barriers
               SET state='continuing', continuation_status='accepted',
                   accepted_turn_id=?, accepted_owner_pid=?,
                   accepted_owner_started_at=?, accepted_at=?,
                   continuation_lease_until=NULL, updated_at=?
               WHERE barrier_id=? AND state='resuming'
                 AND continuation_status='claimed' AND continuation_claim=?""",
            (
                turn_id,
                int(owner_pid),
                owner_started_at,
                now,
                now,
                str(barrier_id),
                str(claim),
            ),
        ).rowcount
    return changed == 1


def bind_delivery_obligation(
    barrier_id: str,
    claim: str,
    *,
    obligation_id: str,
    result: Optional[Dict[str, Any]] = None,
) -> bool:
    obligation = str(obligation_id or "").strip()
    if not obligation:
        return False
    now = time.time()
    with _transaction() as conn:
        changed = conn.execute(
            """UPDATE parent_task_barriers
               SET delivery_obligation_id=?, updated_at=?
               WHERE barrier_id=? AND state='continuing'
                 AND continuation_status='accepted' AND continuation_claim=?""",
            (
                obligation,
                now,
                str(barrier_id),
                str(claim),
            ),
        ).rowcount
    return changed == 1


def release_accepted_continuation(barrier_id: str, claim: str) -> bool:
    now = time.time()
    with _transaction() as conn:
        changed = conn.execute(
            """UPDATE parent_task_barriers
               SET state='ready', continuation_status='pending',
                   continuation_claim='', continuation_owner='',
                   accepted_turn_id='', accepted_owner_pid=NULL, accepted_owner_started_at=NULL,
                       accepted_at=NULL,
                   delivery_obligation_id='',
                   next_attempt_at=?, updated_at=?
               WHERE barrier_id=? AND state='continuing'
                 AND continuation_status='accepted' AND continuation_claim=?""",
            (now + 1.0, now, str(barrier_id), str(claim)),
        ).rowcount
    return changed == 1


def complete_continuation(
    barrier_id: str, claim: str, *, result: Optional[Dict[str, Any]] = None
) -> bool:
    now = time.time()
    with _transaction() as conn:
        changed = conn.execute(
            """UPDATE parent_task_barriers
               SET state='closed', continuation_status='generated',
                   continuation_lease_until=NULL,
                   updated_at=?, closed_at=?
               WHERE barrier_id=? AND state='continuing'
                 AND continuation_status='accepted' AND continuation_claim=?""",
            (
                now,
                now,
                str(barrier_id),
                str(claim),
            ),
        ).rowcount
    return changed == 1


def complete_continuation_after_delivery(
    barrier_id: str,
    claim: str,
    *,
    obligation_id: str,
) -> bool:
    """Close only after the exact bound obligation is durably delivered."""
    obligation = str(obligation_id or "").strip()
    if not obligation:
        return False
    now = time.time()
    sql = (
        "UPDATE parent_task_barriers SET state='closed', "
        "continuation_status='generated', continuation_lease_until=NULL, "
        "updated_at=?, closed_at=? WHERE barrier_id=? AND state='continuing' "
        "AND continuation_status='accepted' AND continuation_claim=? "
        "AND delivery_obligation_id=? AND EXISTS ("
        "SELECT 1 FROM delivery_obligations "
        "WHERE obligation_id=? AND state='delivered')"
    )
    with _transaction() as conn:
        changed = conn.execute(
            sql,
            (now, now, str(barrier_id), str(claim), obligation, obligation),
        ).rowcount
    return changed == 1


def cancel_session_barriers(
    *, origin_session: str = "", parent_session_id: str = ""
) -> int:
    """Terminally cancel open ownership on explicit session controls."""
    clauses = []
    params: list[Any] = []
    if str(origin_session or "").strip():
        clauses.append("origin_session=?")
        params.append(str(origin_session).strip())
    if str(parent_session_id or "").strip():
        clauses.append("parent_session_id=?")
        params.append(str(parent_session_id).strip())
    if not clauses:
        return 0
    now = time.time()
    with _transaction() as conn:
        rows = conn.execute(
            "SELECT barrier_id FROM parent_task_barriers "
            "WHERE state NOT IN ('closed','cancelled','failed') AND ("
            + " OR ".join(clauses)
            + ")",
            params,
        ).fetchall()
        ids = [str(row["barrier_id"]) for row in rows]
        for barrier_id in ids:
            conn.execute(
                "UPDATE parent_task_children SET state='cancelled', "
                "terminal_at=COALESCE(terminal_at, ?), updated_at=? "
                "WHERE barrier_id=? AND state NOT IN ("
                "'completed','failed','error','timeout','stalled','interrupted',"
                "'cancelled','unknown')",
                (now, now, barrier_id),
            )
            conn.execute(
                "UPDATE parent_task_barriers SET state='cancelled', "
                "continuation_status='cancelled', continuation_claim='', "
                "continuation_owner='', continuation_lease_until=NULL, "
                "updated_at=?, closed_at=? WHERE barrier_id=?",
                (now, now, barrier_id),
            )
    return len(ids)


def barrier_snapshot(barrier_id: str) -> Optional[Dict[str, Any]]:
    """Readback helper for tests, diagnostics, and release evidence."""

    with _transaction() as conn:
        barrier = conn.execute(
            "SELECT * FROM parent_task_barriers WHERE barrier_id=?",
            (str(barrier_id),),
        ).fetchone()
        if barrier is None:
            return None
        children = conn.execute(
            """SELECT task_id, required, state, terminal_at
               FROM parent_task_children WHERE barrier_id=?
               ORDER BY created_at, task_id""",
            (str(barrier_id),),
        ).fetchall()
    return {
        "barrier": dict(barrier),
        "children": [dict(row) for row in children],
    }
