from __future__ import annotations

import asyncio
import json
import weakref
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from tools import continuum_card_tool as tool


class EditableAdapter:
    async def edit_message(self, *_args, **_kwargs):
        return SimpleNamespace(success=True)


class Runner:
    def __init__(self, source):
        self._session_sources = {"trusted-session": source}
        self.adapter = EditableAdapter()

    def _adapter_for_source(self, _source):
        return self.adapter

    def _thread_metadata_for_source(self, _source):
        return {"thread_id": "24984"}


@pytest.mark.asyncio
async def test_tool_uses_trusted_session_origin_and_complete_plan(monkeypatch):
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1003971448755",
        chat_type="group",
        thread_id="24984",
        user_id="617744661",
    )
    runner = Runner(source)
    tool._RUNNER = weakref.ref(runner)
    tool._LOOP = asyncio.get_running_loop()
    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda key: "trusted-session" if key == "HERMES_SESSION_KEY" else "",
    )
    monkeypatch.setattr(tool, "_ensure_watcher", lambda: None)
    captured = {}

    class FakeBridge:
        def __init__(self, *_args):
            pass

        def launch(self, origin, goal, context, idem, plan):
            captured.update(
                origin=json.loads(origin), goal=goal, context=context, idem=idem, plan=plan
            )
            return {"task_id": "con_test", "message_id": "42", "card": "card"}

    monkeypatch.setattr(tool, "Bridge", FakeBridge)
    result = json.loads(
        tool._handle(
            {
                "goal": "Do work",
                "context": "bounded",
                "idempotency_key": "idem-123456789012",
                "plan": ["Inspect", "Execute", "Verify"],
            }
        )
    )
    assert result["task_id"] == "con_test"
    assert captured["origin"]["chat_id"] == "-1003971448755"
    assert captured["origin"]["thread_id"] == "24984"
    assert [item["label"] for item in captured["plan"]] == [
        "Inspect",
        "Execute",
        "Verify",
    ]
    definition = tool.registry.get_entry("continuum_card_launch")
    assert definition is not None
    assert definition.check_fn is tool._check_requirements
    properties = definition.schema["parameters"]["properties"]
    assert not ({"chat_id", "thread_id", "message_id", "origin"} & set(properties))


def test_durable_origin_rejects_extra_fields():
    with pytest.raises(RuntimeError, match="invalid durable bridge origin"):
        tool._source_from_origin(
            json.dumps(
                {
                    "platform": "telegram",
                    "chat_id": "1",
                    "chat_type": "dm",
                    "thread_id": "",
                    "user_id": "1",
                    "profile": "",
                    "business_connection_id": "",
                    "session_key": "s",
                    "forged": "x",
                }
            )
        )
