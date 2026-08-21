"""Hermes-owned adaptive same-card bridge for Continuum protocols v1/v2."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools.continuum_host_bridge_protocol import METHOD_CAPABILITY, call_v2
from tools.continuum_host_bridge_v1 import MAX_SNAPSHOT, Adapter
from tools.continuum_host_bridge_v1 import Bridge as V1Bridge

_ADAPTIVE_SCHEMA = 1
_ADAPTIVE_CONFIG = Path(".hermes/continuum-v2-bridge.json")


class V2Bridge:
    """Keep one provider-confirmed card per root without storing origin secrets."""

    def __init__(
        self,
        db_path: Path,
        daemon_call: Callable[[str, dict[str, Any]], dict[str, Any]],
        adapter: Adapter,
        capability_for_origin: Callable[[str], str],
        *,
        policy_hash: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS v2_bindings(
            idem TEXT PRIMARY KEY, root_id TEXT UNIQUE NOT NULL, agent_id TEXT NOT NULL,
            origin TEXT NOT NULL, message_id TEXT, last_card TEXT NOT NULL,
            phase TEXT NOT NULL, terminal INTEGER NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0, next_poll REAL NOT NULL DEFAULT 0,
            stale_reported INTEGER NOT NULL DEFAULT 0)"""
        )
        os.chmod(db_path, 0o600)
        self.call = daemon_call
        self.adapter = adapter
        self.capability_for_origin = capability_for_origin
        self.policy_hash = policy_hash
        self.clock = clock

    def launch(
        self,
        trusted_origin: str,
        goal: str,
        context: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        old = self.db.execute(
            "SELECT * FROM v2_bindings WHERE idem=?", (idempotency_key,)
        ).fetchone()
        if old is not None:
            phase = str(old["phase"])
            if phase in {"active", "terminal"}:
                return self._public(old)
            if phase in {"binding", "bind_unknown"} and old["message_id"]:
                capability = self.capability_for_origin(trusted_origin)
                provider_hash = hashlib.sha256(
                    f"{trusted_origin}\0{old['message_id']}".encode()
                ).hexdigest()
                bound = self.call(
                    "bind_card",
                    {
                        "origin_capability": capability,
                        "root_id": old["root_id"],
                        "provider_message_hash": provider_hash,
                    },
                )
                bound_card = self._card(bound)
                with self.db:
                    self.db.execute(
                        "UPDATE v2_bindings SET last_card=?,phase='active' WHERE idem=?",
                        (bound_card, idempotency_key),
                    )
                recovered = self.db.execute(
                    "SELECT * FROM v2_bindings WHERE idem=?", (idempotency_key,)
                ).fetchone()
                assert recovered is not None
                return self._public(recovered)
            raise RuntimeError("v2 card admission outcome requires reconciliation")
        capability = self.capability_for_origin(trusted_origin)
        created = self.call(
            "create",
            {
                "origin_capability": capability,
                "goal": goal,
                "context": context,
                "policy_hash": self.policy_hash,
                "idempotency_key": idempotency_key,
            },
        )
        root_id = self._text(created, "root_id")
        agent_id = self._text(created, "agent_id")
        projection = created.get("projection")
        if not isinstance(projection, dict):
            raise RuntimeError("v2 create response lacks projection")
        card = self._card(projection)
        with self.db:
            self.db.execute(
                "INSERT INTO v2_bindings(idem,root_id,agent_id,origin,last_card,phase) "
                "VALUES(?,?,?,?,?,'sending')",
                (idempotency_key, root_id, agent_id, trusted_origin, card),
            )
        try:
            message_id = self.adapter.send(trusted_origin, card)
            if not isinstance(message_id, str) or not message_id:
                raise RuntimeError("provider returned no message ID")
        except Exception:
            with self.db:
                self.db.execute(
                    "UPDATE v2_bindings SET phase='send_unknown',terminal=1 WHERE root_id=?",
                    (root_id,),
                )
            raise
        provider_hash = hashlib.sha256(
            f"{trusted_origin}\0{message_id}".encode()
        ).hexdigest()
        with self.db:
            self.db.execute(
                "UPDATE v2_bindings SET message_id=?,phase='binding' WHERE root_id=?",
                (message_id, root_id),
            )
        try:
            bound = self.call(
                "bind_card",
                {
                    "origin_capability": capability,
                    "root_id": root_id,
                    "provider_message_hash": provider_hash,
                },
            )
            bound_card = self._card(bound)
        except Exception:
            with self.db:
                self.db.execute(
                    "UPDATE v2_bindings SET phase='bind_unknown' WHERE root_id=?",
                    (root_id,),
                )
            raise
        with self.db:
            self.db.execute(
                "UPDATE v2_bindings SET last_card=?,phase='active' WHERE root_id=?",
                (bound_card, root_id),
            )
        row = self.db.execute(
            "SELECT * FROM v2_bindings WHERE root_id=?", (root_id,)
        ).fetchone()
        assert row is not None
        return self._public(row)

    def tick(self, *, limit: int = 16) -> int:
        current = self.clock()
        rows = self.db.execute(
            "SELECT * FROM v2_bindings WHERE phase='active' AND terminal=0 "
            "AND next_poll<=? ORDER BY rowid LIMIT ?",
            (current, limit),
        ).fetchall()
        for row in rows:
            try:
                capability = self.capability_for_origin(str(row["origin"]))
                status = self.call(
                    "status",
                    {
                        "origin_capability": capability,
                        "root_id": row["root_id"],
                        "after_event": 0,
                        "event_limit": 100,
                    },
                )
                card = self._card(status)
                if card != row["last_card"]:
                    try:
                        self.adapter.edit(str(row["origin"]), str(row["message_id"]), card)
                    except Exception:
                        with self.db:
                            self.db.execute(
                                "UPDATE v2_bindings SET phase='edit_unknown',last_card=? WHERE root_id=?",
                                (card, row["root_id"]),
                            )
                        continue
                root = status.get("root")
                terminal = isinstance(root, dict) and root.get("state") in {
                    "completed", "failed", "stopped", "unknown"
                }
                with self.db:
                    self.db.execute(
                        "UPDATE v2_bindings SET last_card=?,phase=?,terminal=?,failures=0,"
                        "next_poll=?,stale_reported=0 WHERE root_id=?",
                        (
                            card,
                            "terminal" if terminal else "active",
                            int(terminal),
                            current + 1,
                            row["root_id"],
                        ),
                    )
            except Exception:
                failures = min(int(row["failures"]) + 1, 10)
                if not row["stale_reported"]:
                    self._safe_edit(
                        str(row["origin"]),
                        str(row["message_id"]),
                        str(row["last_card"]) + "\n\n⚠️ Статус временно недоступен.",
                    )
                with self.db:
                    self.db.execute(
                        "UPDATE v2_bindings SET failures=?,next_poll=?,stale_reported=1 "
                        "WHERE root_id=?",
                        (failures, current + min(300, 2**failures), row["root_id"]),
                    )
        return len(rows)

    @staticmethod
    def _text(value: dict[str, Any], key: str) -> str:
        item = value.get(key)
        if not isinstance(item, str) or not item:
            raise RuntimeError(f"v2 response lacks {key}")
        return item

    @staticmethod
    def _card(value: dict[str, Any]) -> str:
        card = value.get("card")
        if (
            not isinstance(card, str)
            or not card.startswith("█")
            or len(card.encode()) > MAX_SNAPSHOT
        ):
            raise RuntimeError("invalid v2 card projection")
        return card

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "root_id": row["root_id"],
            "agent_id": row["agent_id"],
            "message_id": row["message_id"],
            "card": row["last_card"],
        }

    def _safe_edit(self, origin: str, message_id: str, text: str) -> None:
        with contextlib.suppress(Exception):
            self.adapter.edit(origin, message_id, text)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def route_hash(origin: str) -> str:
    """Hash an exact stable route while excluding ephemeral Hermes session identity."""

    try:
        value: Any = json.loads(origin)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("invalid durable bridge origin") from None
    expected = {
        "platform", "chat_id", "chat_type", "thread_id", "user_id", "profile",
        "business_connection_id", "session_key",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeError("invalid durable bridge origin")
    stable = {key: value[key] for key in sorted(expected - {"session_key"})}
    if any(not isinstance(item, str) for item in stable.values()):
        raise RuntimeError("invalid durable bridge origin")
    return hashlib.sha256(_canonical(stable)).hexdigest()


class AdaptiveBridge:
    """Preserve v1 while routing allowlisted exact origins to protocol v2."""

    def __init__(
        self,
        db_path: Path,
        daemon_call: Callable[[str, dict[str, Any]], dict[str, Any]],
        adapter: Adapter,
    ) -> None:
        self.v1 = V1Bridge(db_path, daemon_call, adapter)
        self.v2: V2Bridge | None = None
        self._v2_call: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
        self._v2_capability: Callable[[str], str] | None = None
        self._origins: dict[str, str] = {}
        path = Path.home() / _ADAPTIVE_CONFIG
        if not path.exists():
            return
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise RuntimeError("v2 bridge config must be one private regular file")
        try:
            value: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("v2 bridge config is invalid") from None
        expected = {"schema", "socket", "binding_database", "policy_hash", "origins"}
        if not isinstance(value, dict) or set(value) != expected or value["schema"] != _ADAPTIVE_SCHEMA:
            raise RuntimeError("v2 bridge config fields are invalid")
        origins = value["origins"]
        if (
            not isinstance(origins, dict)
            or not origins
            or not all(
                isinstance(key, str) and len(key) == 64
                and isinstance(capability, str) and 8 <= len(capability) <= 128
                for key, capability in origins.items()
            )
        ):
            raise RuntimeError("v2 bridge origin map is invalid")
        socket_path = Path(value["socket"])
        binding_database = Path(value["binding_database"])
        policy_hash = value["policy_hash"]
        if (
            not socket_path.is_absolute()
            or not binding_database.is_absolute()
            or not isinstance(policy_hash, str)
            or len(policy_hash) != 64
        ):
            raise RuntimeError("v2 bridge authority is invalid")
        self._origins = dict(origins)

        def capability(origin: str) -> str:
            selected = self._origins.get(route_hash(origin))
            if selected is None:
                raise RuntimeError("origin is not routed to Continuum v2")
            return selected

        def v2_call(method: str, params: dict[str, Any]) -> dict[str, Any]:
            response = call_v2(
                socket_path,
                method,
                params,
                client_id="client:gateway-card-bridge-v2",
                capabilities=[METHOD_CAPABILITY[method]],
            )
            result = response["result"]
            if not isinstance(result, dict):
                raise RuntimeError("invalid v2 bridge result")
            return result

        self.v2 = V2Bridge(
            binding_database,
            v2_call,
            adapter,
            capability,
            policy_hash=policy_hash,
        )
        self._v2_call = v2_call
        self._v2_capability = capability

    def launch(
        self,
        trusted_origin: str,
        goal: str,
        context: str,
        idempotency_key: str,
        plan: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if self.v2 is None or route_hash(trusted_origin) not in self._origins:
            return self.v1.launch(trusted_origin, goal, context, idempotency_key, plan)
        if plan:
            checklist = "\n".join(
                f"- {item.get('label', '')}: {item.get('detail', '')}" for item in plan
            )
            context = f"{context}\n\nOperator plan:\n{checklist}".strip()
        return self.v2.launch(trusted_origin, goal, context, idempotency_key)

    def tick(self, *, limit: int = 32) -> int:
        count = self.v1.tick(limit=limit)
        if self.v2 is not None:
            count += self.v2.tick(limit=limit)
        return count

    def control(self, trusted_origin: str, request: dict[str, Any]) -> dict[str, Any]:
        """Run one exact-origin privileged v2 control from the gateway direct tool."""

        if (
            self.v2 is None
            or self._v2_call is None
            or self._v2_capability is None
            or route_hash(trusted_origin) not in self._origins
        ):
            raise RuntimeError("current origin is not routed to Continuum v2")
        if not isinstance(request, dict):
            raise ValueError("control must be an object")
        operation = request.get("operation")
        allowed = {
            "list": set(),
            "status": {"root_id", "after_event", "event_limit"},
            "result": {"root_id"},
            "send": {
                "root_id",
                "agent_id",
                "message",
                "mode",
                "idempotency_key",
                "correlation_id",
            },
            "stop": {"root_id"},
            "tree": {"root_id"},
            "children": {"root_id", "agent_id"},
            "schedules": {"root_id", "agent_id"},
            "heartbeats": {"root_id", "agent_id"},
        }
        if not isinstance(operation, str) or operation not in allowed:
            raise ValueError("unsupported Continuum control operation")
        fields = set(request) - {"operation"}
        if not fields <= allowed[operation]:
            raise ValueError("unsupported Continuum control argument")
        params: dict[str, Any] = {
            "origin_capability": self._v2_capability(trusted_origin)
        }
        if operation != "list":
            root_id = request.get("root_id")
            if not isinstance(root_id, str) or not root_id:
                raise ValueError("root_id is required")
            params["root_id"] = root_id
        if operation == "status":
            params["after_event"] = request.get("after_event", 0)
            params["event_limit"] = request.get("event_limit", 100)
        if operation == "send":
            for key in ("agent_id", "message", "idempotency_key", "correlation_id"):
                if not isinstance(request.get(key), str) or not request[key]:
                    raise ValueError(f"{key} is required")
                params[key] = request[key]
            params["mode"] = request.get("mode", "follow_up")
        if operation in {"children", "schedules", "heartbeats"}:
            agent_id = request.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id:
                raise ValueError("agent_id is required")
            params["agent_id"] = agent_id
        return self._v2_call(operation, params)


__all__ = ["AdaptiveBridge", "V2Bridge", "route_hash"]
