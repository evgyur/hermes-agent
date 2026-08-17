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
_RETENTION_SECONDS = 7 * 24 * 60 * 60


class TrustedParentTaskContinuation(dict):
    """Host-created wake carrying one durable barrier continuation claim."""


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
    _ensure_schema(conn)
    return conn


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS parent_task_barriers (
            barrier_id TEXT PRIMARY KEY,
            origin_session TEXT NOT NULL,
            parent_session_id TEXT NOT NULL,
            root_turn_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'open',
            initial_persisted INTEGER NOT NULL DEFAULT 0,
            continuation_status TEXT NOT NULL DEFAULT 'pending',
            continuation_claim TEXT NOT NULL DEFAULT '',
            continuation_owner TEXT NOT NULL DEFAULT '',
            continuation_lease_until REAL,
            continuation_attempts INTEGER NOT NULL DEFAULT 0,
            continuation_result_json TEXT,
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
        """
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


def _gateway_owner_is_dead(owner: str) -> bool:
    parts = str(owner or "").split(":", 2)
    if len(parts) < 2 or parts[0] != "gateway" or not parts[1].isdigit():
        return False
    try:
        os.kill(int(parts[1]), 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def admit_required_child(
    *,
    origin_session: str,
    parent_session_id: str,
    root_turn_id: str,
    task_id: str,
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
    with _transaction() as conn:
        _prune_terminal_in_tx(conn, now)
        row = conn.execute(
            """SELECT barrier_id, origin_session FROM parent_task_barriers
               WHERE parent_session_id=? AND root_turn_id=?""",
            (identities["parent_session_id"], identities["root_turn_id"]),
        ).fetchone()
        if row is None:
            barrier_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO parent_task_barriers(
                       barrier_id, origin_session, parent_session_id, root_turn_id,
                       state, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 'open', ?, ?)""",
                (
                    barrier_id,
                    identities["origin_session"],
                    identities["parent_session_id"],
                    identities["root_turn_id"],
                    now,
                    now,
                ),
            )
        else:
            barrier_id = str(row["barrier_id"])
            if str(row["origin_session"]) != identities["origin_session"]:
                raise RuntimeError("root turn is already bound to another origin session")
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


def finalization_policy(*, parent_session_id: str, root_turn_id: str) -> Dict[str, Any]:
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


def barrier_for_child(task_id: str) -> Optional[str]:
    with _transaction() as conn:
        row = conn.execute(
            "SELECT barrier_id FROM parent_task_children WHERE task_id=?",
            (str(task_id),),
        ).fetchone()
    return str(row["barrier_id"]) if row is not None else None


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
        row = conn.execute(
            """SELECT barrier_id, origin_session, parent_session_id,
                      continuation_attempts
               FROM parent_task_barriers AS b
               WHERE b.state='ready' AND b.initial_persisted=1
                 AND b.continuation_status='pending'
                 AND NOT EXISTS (
                   SELECT 1 FROM parent_task_children AS c
                   WHERE c.barrier_id=b.barrier_id AND c.required=1
                     AND c.state NOT IN (
                       'completed','failed','error','timeout','stalled','interrupted',
                       'cancelled','unknown'
                     )
                 )
               ORDER BY b.created_at, b.barrier_id LIMIT 1"""
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
                   updated_at=?
               WHERE barrier_id=? AND state='ready'
                 AND continuation_status='pending'""",
            (
                token,
                owner_id,
                now + max(1.0, float(lease_seconds)),
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
    with _transaction() as conn:
        changed = conn.execute(
            """UPDATE parent_task_barriers
               SET state='ready', continuation_status='pending',
                   continuation_claim='', continuation_owner='',
                   continuation_lease_until=NULL, updated_at=?
               WHERE barrier_id=? AND state='resuming'
                 AND continuation_status='claimed' AND continuation_claim=?""",
            (time.time(), str(barrier_id), str(claim)),
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
                   continuation_result_json=?, continuation_lease_until=NULL,
                   updated_at=?, closed_at=?
               WHERE barrier_id=? AND state='resuming'
                 AND continuation_status='claimed' AND continuation_claim=?""",
            (
                json.dumps(result or {}, ensure_ascii=False),
                now,
                now,
                str(barrier_id),
                str(claim),
            ),
        ).rowcount
    return changed == 1


def cancel_session_barriers(*, origin_session: str = "", parent_session_id: str = "") -> int:
    """Terminally cancel open ownership on explicit /stop, /new, or /reset."""

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
            """SELECT barrier_id FROM parent_task_barriers
               WHERE state NOT IN ('closed','cancelled') AND ("""
            + " OR ".join(clauses)
            + ")",
            params,
        ).fetchall()
        ids = [str(row["barrier_id"]) for row in rows]
        for barrier_id in ids:
            conn.execute(
                """UPDATE parent_task_children
                   SET state='cancelled', terminal_at=COALESCE(terminal_at, ?),
                       updated_at=?
                   WHERE barrier_id=? AND state NOT IN (
                     'completed','failed','error','timeout','stalled','interrupted',
                     'cancelled','unknown'
                   )""",
                (now, now, barrier_id),
            )
            conn.execute(
                """UPDATE parent_task_barriers
                   SET state='cancelled', continuation_status='cancelled',
                       continuation_claim='', continuation_owner='',
                       continuation_lease_until=NULL, updated_at=?, closed_at=?
                   WHERE barrier_id=?""",
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
