"""Hermes-owned generic same-card bridge (protocol v1).

This controller owns delivery metadata and never executes task work. It is
intended to run on a dedicated bridge executor so synchronous SQLite/UDS work
never blocks the gateway event loop. The adapter implementation marshals
provider send/edit calls onto the gateway loop.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

MAX_SNAPSHOT = 65_536
_UNCERTAIN_PHASES = {"sending", "sent", "binding"}


class Adapter(Protocol):
    def send(self, origin: str, text: str) -> str: ...

    def edit(self, origin: str, message_id: str, text: str) -> None: ...


class Bridge:
    """Crash-safe two-phase card admission controller.

    A retry is permitted only after an unambiguous pre-send failure. Once the
    phase reaches ``sending``, ambiguity is terminal for automatic retries: an
    operator must reconcile the provider before any new card can be emitted.
    """

    def __init__(
        self,
        db_path: Path,
        daemon_call: Callable[[str, dict[str, Any]], dict[str, Any]],
        adapter: Adapter,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS bindings(
            idem TEXT PRIMARY KEY,
            task_id TEXT UNIQUE NOT NULL,
            origin TEXT NOT NULL,
            message_id TEXT,
            nonce TEXT,
            last_card TEXT NOT NULL,
            phase TEXT NOT NULL,
            terminal INTEGER NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0,
            next_poll REAL NOT NULL DEFAULT 0,
            stale_reported INTEGER NOT NULL DEFAULT 0
            )"""
        )
        os.chmod(db_path, 0o600)
        self.call = daemon_call
        self.adapter = adapter
        self.clock = clock

    def launch(
        self,
        trusted_origin: str,
        goal: str,
        context: str,
        idempotency_key: str,
        plan: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        old = self.db.execute(
            "SELECT * FROM bindings WHERE idem=?", (idempotency_key,)
        ).fetchone()
        if old:
            if old["phase"] in {"active", "terminal", "admission_failed"}:
                return self._public(old)
            if old["phase"] in _UNCERTAIN_PHASES:
                raise RuntimeError("card admission outcome requires reconciliation")
            if old["phase"] == "send_failed":
                raise RuntimeError("card delivery failed; task was not activated")
            raise RuntimeError("reserved admission requires reconciliation")

        reserve_params: dict[str, Any] = {
            "goal": goal,
            "context": context,
            "idempotency_key": idempotency_key,
        }
        if plan:
            reserve_params["plan"] = plan
        reserved = self._result(self.call("reserve", reserve_params))
        card = self._card(reserved)
        task_id = reserved["task_id"]
        with self.db:
            self.db.execute(
                "INSERT INTO bindings(idem,task_id,origin,last_card,phase) "
                "VALUES(?,?,?,?, 'reserved')",
                (idempotency_key, task_id, trusted_origin, card),
            )
            self.db.execute(
                "UPDATE bindings SET phase='sending' WHERE task_id=?", (task_id,)
            )
        try:
            message_id = self.adapter.send(trusted_origin, card)
            if not isinstance(message_id, str) or not message_id:
                raise RuntimeError("provider returned no message ID")
        except Exception:
            # Provider exceptions may be ambiguous. Never retry automatically.
            with self.db:
                self.db.execute(
                    "UPDATE bindings SET phase='send_failed',terminal=1 WHERE task_id=?",
                    (task_id,),
                )
            raise
        nonce = secrets.token_urlsafe(32)
        binding_hash = hashlib.sha256(
            f"{trusted_origin}\0{message_id}".encode()
        ).hexdigest()
        with self.db:
            self.db.execute(
                "UPDATE bindings SET message_id=?,nonce=?,phase='sent' WHERE task_id=?",
                (message_id, nonce, task_id),
            )
            self.db.execute(
                "UPDATE bindings SET phase='binding' WHERE task_id=?", (task_id,)
            )
        try:
            self._result(
                self.call(
                    "bind_and_activate",
                    {
                        "task_id": task_id,
                        "binding_hash": binding_hash,
                        "nonce": nonce,
                    },
                )
            )
        except Exception:
            failure = card + "\n\n❌ Запуск не подтверждён; работа не начата."
            self._safe_edit(trusted_origin, message_id, failure)
            with self.db:
                self.db.execute(
                    "UPDATE bindings SET last_card=?,phase='admission_failed',terminal=1 "
                    "WHERE task_id=?",
                    (failure, task_id),
                )
            raise
        with self.db:
            self.db.execute(
                "UPDATE bindings SET phase='active' WHERE task_id=?", (task_id,)
            )
        row = self.db.execute("SELECT * FROM bindings WHERE task_id=?", (task_id,)).fetchone()
        assert row is not None
        return self._public(row)

    def tick(self, *, limit: int = 16) -> int:
        current = self.clock()
        rows = self.db.execute(
            "SELECT * FROM bindings WHERE phase='active' AND terminal=0 "
            "AND next_poll<=? ORDER BY rowid LIMIT ?",
            (current, limit),
        ).fetchall()
        for row in rows:
            try:
                value = self._result(
                    self.call("card_snapshot", {"task_id": row["task_id"]})
                )
                card = self._card(value)
                if card != row["last_card"]:
                    self.adapter.edit(row["origin"], row["message_id"], card)
                terminal = value.get("state") in {
                    "completed",
                    "failed",
                    "interrupted",
                    "unknown",
                }
                phase = "terminal" if terminal else "active"
                with self.db:
                    self.db.execute(
                        "UPDATE bindings SET last_card=?,phase=?,terminal=?,failures=0,"
                        "next_poll=?,stale_reported=0 WHERE task_id=?",
                        (card, phase, terminal, current + 1, row["task_id"]),
                    )
            except Exception:
                failures = min(int(row["failures"]) + 1, 10)
                if not row["stale_reported"]:
                    self._safe_edit(
                        row["origin"],
                        row["message_id"],
                        row["last_card"] + "\n\n⚠️ Статус временно недоступен.",
                    )
                with self.db:
                    self.db.execute(
                        "UPDATE bindings SET failures=?,next_poll=?,stale_reported=1 "
                        "WHERE task_id=?",
                        (failures, current + min(300, 2**failures), row["task_id"]),
                    )
        return len(rows)

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "message_id": row["message_id"],
            "card": row["last_card"],
        }

    @staticmethod
    def _result(value: dict[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or value.get("ok") is not True
            or not isinstance(value.get("result"), dict)
        ):
            raise RuntimeError("daemon protocol mismatch")
        result: dict[str, Any] = value["result"]
        return result

    @staticmethod
    def _card(value: dict[str, Any]) -> str:
        card = value.get("card")
        if (
            not isinstance(card, str)
            or not card
            or len(card.encode()) > MAX_SNAPSHOT
            or not (card.startswith("█") or card.startswith("░"))
        ):
            raise RuntimeError("invalid card snapshot")
        return card

    def _safe_edit(self, origin: str, message_id: str, text: str) -> None:
        with contextlib.suppress(Exception):
            self.adapter.edit(origin, message_id, text)
