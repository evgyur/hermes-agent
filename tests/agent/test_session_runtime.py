from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from agent.session_runtime import SessionBinding, SessionRuntime, SessionRuntimeError
from agent.tool_executor import _qualified_tool_block
from hermes_state import SessionDB


class FakeStore:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(session_id)

    def get_messages_as_conversation(self, session_id: str) -> list[dict[str, Any]]:
        return list(self.messages.get(session_id, []))


class FakeAgent:
    def __init__(self, store: Any, session_id: str) -> None:
        self.store = store
        self.session_id = session_id
        self.allowed: tuple[str, ...] = ()
        self.histories: list[list[dict[str, Any]]] = []
        self.interrupts: list[str | None] = []
        self.closed = False

    def configure_tool_allowlist(self, tools: Any) -> None:
        self.allowed = tuple(tools)
        self.qualified_tool_allowlist = frozenset(self.allowed)

    def run_conversation(
        self,
        user_message: Any,
        *,
        conversation_history: list[dict[str, Any]],
        task_id: str | None = None,
    ) -> dict[str, Any]:
        self.histories.append(list(conversation_history))
        self.store.sessions[self.session_id] = {"id": self.session_id}
        self.store.messages.setdefault(self.session_id, []).extend(
            [{"role": "user", "content": user_message}, {"role": "assistant", "content": "ok"}]
        )
        return {"final": "ok", "task_id": task_id}

    def hard_interrupt(self, message: str | None = None) -> None:
        self.interrupts.append(message)

    def close(self) -> None:
        self.closed = True


def binding(tmp_path: Path) -> SessionBinding:
    return SessionBinding(
        model="model-a",
        provider="provider-a",
        skills_hash="a" * 64,
        workdir=str(tmp_path.resolve()),
        qualified_tools=("read_file", "terminal", "web_search"),
        origin_hash="b" * 64,
        policy_hash="c" * 64,
    )


def open_runtime(
    tmp_path: Path,
    store: FakeStore,
    *,
    current: tuple[str, ...] = ("read_file", "terminal", "web_search"),
    current_deny: tuple[str, ...] = (),
    events: list[tuple[str, dict[str, Any]]] | None = None,
) -> tuple[SessionRuntime, FakeAgent]:
    made: list[FakeAgent] = []

    def factory(*, session_id: str, session_db: FakeStore, **_: Any) -> FakeAgent:
        agent = FakeAgent(session_db, session_id)
        made.append(agent)
        return agent

    runtime = SessionRuntime.open_or_resume(
        session_id="continuum-session-1",
        session_db=store,
        agent_factory=factory,
        agent_options={"model": "model-a", "provider": "provider-a"},
        binding=binding(tmp_path),
        binding_path=tmp_path / "bindings" / "continuum-session-1.json",
        current_allowlist=current,
        current_denylist=current_deny,
        event_callback=(lambda name, payload: events.append((name, payload))) if events is not None else None,
    )
    return runtime, made[0]


def test_sequential_turns_resume_committed_public_history(tmp_path: Path) -> None:
    store = FakeStore()
    events: list[tuple[str, dict[str, Any]]] = []
    first, first_agent = open_runtime(tmp_path, store, events=events)
    assert first.submit_turn("one", task_id="turn-1")["final"] == "ok"
    assert first_agent.histories == [[]]
    first.close()

    resumed, second_agent = open_runtime(tmp_path, store, events=events)
    assert resumed.snapshot()["exists"] is True
    assert resumed.snapshot()["message_count"] == 2
    resumed.submit_turn("two", task_id="turn-2")
    assert [item["content"] for item in second_agent.histories[0]] == ["one", "ok"]
    assert [name for name, _ in events].count("turn.terminal") == 2


def test_real_sessiondb_restart_roundtrip(tmp_path: Path) -> None:
    session_id = "continuum-real-session"

    class PersistingAgent(FakeAgent):
        def run_conversation(
            self,
            user_message: Any,
            *,
            conversation_history: list[dict[str, Any]],
            task_id: str | None = None,
        ) -> dict[str, Any]:
            self.histories.append(list(conversation_history))
            if self.store.get_session(self.session_id) is None:
                self.store.create_session(
                    session_id=self.session_id,
                    source="continuum",
                    model="fake",
                    system_prompt="probe",
                )
            self.store.append_message(self.session_id, "user", str(user_message))
            self.store.append_message(self.session_id, "assistant", "ok")
            return {"final": "ok", "task_id": task_id}

    path = tmp_path / "state.db"
    first_db = SessionDB(db_path=path)
    first = SessionRuntime.open_or_resume(
        session_id=session_id,
        session_db=first_db,
        agent_factory=lambda **_: PersistingAgent(first_db, session_id),
        agent_options={},
        binding=binding(tmp_path),
        binding_path=tmp_path / "binding-real.json",
        current_allowlist=("read_file",),
    )
    first.submit_turn("one")
    first.close()
    first_db.close()

    second_db = SessionDB(db_path=path)
    second_agent = PersistingAgent(second_db, session_id)
    second = SessionRuntime.open_or_resume(
        session_id=session_id,
        session_db=second_db,
        agent_factory=lambda **_: second_agent,
        agent_options={},
        binding=binding(tmp_path),
        binding_path=tmp_path / "binding-real.json",
        current_allowlist=("read_file", "ungranted"),
    )
    second.submit_turn("two")
    assert [item["content"] for item in second_agent.histories[0]] == ["one", "ok"]
    second_db.close()


def test_capability_intersection_only_shrinks_and_manifest_is_private(tmp_path: Path) -> None:
    store = FakeStore()
    runtime, agent = open_runtime(
        tmp_path,
        store,
        current=("read_file", "terminal", "imaginary_widening"),
        current_deny=("terminal",),
    )
    assert runtime.effective_tools == ("read_file",)
    assert agent.allowed == ("read_file",)
    manifest = tmp_path / "bindings" / "continuum-session-1.json"
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert "imaginary_widening" not in manifest.read_text()


def test_binding_change_is_rejected_on_resume(tmp_path: Path) -> None:
    store = FakeStore()
    open_runtime(tmp_path, store)
    other = binding(tmp_path)
    changed = SessionBinding(**{**other.__dict__, "policy_hash": "d" * 64})
    with pytest.raises(SessionRuntimeError, match="binding mismatch"):
        SessionRuntime.open_or_resume(
            session_id="continuum-session-1",
            session_db=store,
            agent_factory=lambda **_: FakeAgent(store, "continuum-session-1"),
            agent_options={},
            binding=changed,
            binding_path=tmp_path / "bindings" / "continuum-session-1.json",
            current_allowlist=changed.qualified_tools,
        )


def test_interrupt_and_exact_execution_gate(tmp_path: Path) -> None:
    assert _qualified_tool_block(object(), "terminal") is None
    runtime, agent = open_runtime(tmp_path, FakeStore(), current=("read_file",))
    assert runtime.interrupt("stop") is True
    assert agent.interrupts == ["stop"]
    assert _qualified_tool_block(agent, "read_file") is None
    assert _qualified_tool_block(agent, "terminal") == "'terminal' is not authorized for this session."


def test_one_active_turn_per_runtime(tmp_path: Path) -> None:
    store = FakeStore()
    entered = threading.Event()
    release = threading.Event()

    class BlockingAgent(FakeAgent):
        def run_conversation(self, user_message: Any, *, conversation_history: list[dict[str, Any]], task_id: str | None = None) -> dict[str, Any]:
            entered.set()
            assert release.wait(2)
            return super().run_conversation(user_message, conversation_history=conversation_history, task_id=task_id)

    runtime = SessionRuntime.open_or_resume(
        session_id="continuum-session-1",
        session_db=store,
        agent_factory=lambda **_: BlockingAgent(store, "continuum-session-1"),
        agent_options={},
        binding=binding(tmp_path),
        binding_path=tmp_path / "binding.json",
        current_allowlist=("read_file",),
    )
    thread = threading.Thread(target=lambda: runtime.submit_turn("one"))
    thread.start()
    assert entered.wait(1)
    with pytest.raises(SessionRuntimeError, match="active turn"):
        runtime.submit_turn("two")
    release.set()
    thread.join(2)
    assert not thread.is_alive()
