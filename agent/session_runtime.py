"""Small public API for externally supervised, resumable Hermes sessions.

This module owns no scheduler, mailbox, task FSM, or delivery policy.  It wraps
one ordinary ``AIAgent`` and a caller-supplied private ``SessionDB``.  The
caller's binding manifest is durable and intentionally contains no credential.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, cast


class SessionRuntimeError(RuntimeError):
    """The public runtime contract cannot be honored safely."""


class SessionStore(Protocol):
    def get_session(self, session_id: str) -> Mapping[str, Any] | None: ...
    def get_messages_as_conversation(self, session_id: str) -> list[dict[str, Any]]: ...


class SessionAgent(Protocol):
    def run_conversation(
        self,
        user_message: Any,
        *,
        conversation_history: list[dict[str, Any]],
        task_id: str | None = None,
    ) -> dict[str, Any]: ...
    def configure_tool_allowlist(self, qualified_tools: Iterable[str]) -> None: ...


@dataclass(frozen=True)
class SessionBinding:
    model: str
    provider: str
    skills_hash: str
    workdir: str
    qualified_tools: tuple[str, ...]
    origin_hash: str
    policy_hash: str

    def normalized(self) -> dict[str, Any]:
        value = asdict(self)
        value["qualified_tools"] = sorted(set(self.qualified_tools))
        return value

    @property
    def digest(self) -> str:
        raw = json.dumps(self.normalized(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


class SessionRuntime:
    """Sequential-turn runtime over public ``AIAgent`` and ``SessionDB`` APIs."""

    def __init__(
        self,
        *,
        session_id: str,
        session_db: SessionStore,
        agent: SessionAgent,
        binding: SessionBinding,
        effective_tools: tuple[str, ...],
        event_callback: Callable[[str, dict[str, Any]], None] | None,
    ) -> None:
        self.session_id = session_id
        self.binding = binding
        self.effective_tools = effective_tools
        self._session_db = session_db
        self._agent = agent
        self._event_callback = event_callback
        self._turn_lock = threading.Lock()
        self._active_task_id: str | None = None
        self._last_outcome = "idle"

    @classmethod
    def open_or_resume(
        cls,
        *,
        session_id: str,
        session_db: SessionStore,
        agent_factory: Callable[..., Any],
        agent_options: Mapping[str, Any],
        binding: SessionBinding,
        binding_path: Path,
        current_allowlist: Iterable[str],
        creation_denylist: Iterable[str] = (),
        current_denylist: Iterable[str] = (),
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> "SessionRuntime":
        if not session_id or len(session_id.encode()) > 160:
            raise SessionRuntimeError("invalid session_id")
        cls._bind_manifest(binding_path, session_id, binding, creation_denylist)
        creation = set(binding.qualified_tools)
        current = set(current_allowlist)
        denied = set(creation_denylist) | set(current_denylist)
        effective = tuple(sorted((creation & current) - denied))
        forbidden = {"session_id", "session_db", "event_callback"} & set(agent_options)
        if forbidden:
            raise SessionRuntimeError(f"agent_options may not override {sorted(forbidden)[0]}")
        agent = cast(SessionAgent, agent_factory(
            **dict(agent_options),
            session_id=session_id,
            session_db=session_db,
            event_callback=event_callback,
        ))
        agent.configure_tool_allowlist(effective)
        runtime = cls(
            session_id=session_id,
            session_db=session_db,
            agent=agent,
            binding=binding,
            effective_tools=effective,
            event_callback=event_callback,
        )
        runtime._emit("session.opened", {"resumed": session_db.get_session(session_id) is not None})
        return runtime

    @staticmethod
    def _bind_manifest(
        path: Path,
        session_id: str,
        binding: SessionBinding,
        creation_denylist: Iterable[str],
    ) -> None:
        value = {
            "schema": 1,
            "session_id": session_id,
            "binding": binding.normalized(),
            "binding_hash": binding.digest,
            "creation_denylist": sorted(set(creation_denylist)),
        }
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        path = path.expanduser().resolve()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists():
            if path.is_symlink() or path.stat().st_mode & 0o077:
                raise SessionRuntimeError("unsafe binding manifest")
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SessionRuntimeError("invalid binding manifest") from exc
            if current != value:
                raise SessionRuntimeError("session binding mismatch")
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, raw.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def submit_turn(self, message: Any, *, task_id: str | None = None) -> dict[str, Any]:
        if not self._turn_lock.acquire(blocking=False):
            raise SessionRuntimeError("session already has an active turn")
        self._active_task_id = task_id
        self._last_outcome = "running"
        self._emit("turn.started", {"task_id": task_id})
        try:
            history = self._session_db.get_messages_as_conversation(self.session_id)
            result = self._agent.run_conversation(
                message,
                conversation_history=history,
                task_id=task_id,
            )
            if not isinstance(result, dict):
                raise SessionRuntimeError("agent returned an invalid terminal result")
            self._last_outcome = (
                "interrupted" if result.get("interrupted") is True
                else "failed" if result.get("failed") is True
                else "completed"
            )
            self._emit("turn.terminal", {"task_id": task_id, "outcome": self._last_outcome})
            return result
        except BaseException:
            self._last_outcome = "failed"
            self._emit("turn.terminal", {"task_id": task_id, "outcome": "failed"})
            raise
        finally:
            self._active_task_id = None
            self._turn_lock.release()

    def interrupt(self, message: str | None = None) -> bool:
        method = getattr(self._agent, "hard_interrupt", None)
        if not callable(method):
            method = getattr(self._agent, "interrupt", None)
        if not callable(method):
            return False
        method(message) if message is not None else method()
        self._emit("turn.interrupt_requested", {"task_id": self._active_task_id})
        return True

    def snapshot(self) -> dict[str, Any]:
        row = self._session_db.get_session(self.session_id)
        messages = self._session_db.get_messages_as_conversation(self.session_id) if row else []
        return {
            "session_id": self.session_id,
            "exists": row is not None,
            "active": self._turn_lock.locked(),
            "active_task_id": self._active_task_id,
            "last_outcome": self._last_outcome,
            "message_count": len(messages),
            "binding_hash": self.binding.digest,
            "effective_tools": list(self.effective_tools),
        }

    def close(self) -> None:
        close = getattr(self._agent, "close", None)
        if callable(close):
            close()

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self._event_callback is not None:
            self._event_callback(name, payload)
