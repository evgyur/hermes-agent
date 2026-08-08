from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

from agent.claim_integrity import (
    claim_guarded_callbacks,
    claim_integrity_enabled,
    collect_evidence,
    enforce_claim_integrity,
)
from agent.turn_finalizer import finalize_turn
from hermes_cli.config_defaults import DEFAULT_CONFIG


class _FakeAgent:
    def __init__(self):
        self.max_iterations = 90
        self.iteration_budget = SimpleNamespace(remaining=10, used=1, max_total=90)
        self.quiet_mode = True
        self.model = "test-model"
        self.provider = "test-provider"
        self.base_url = ""
        self.platform = "telegram"
        self.session_id = "sess-test"
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0)
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_cache_read_tokens = 0
        self.session_cache_write_tokens = 0
        self.session_reasoning_tokens = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self.session_estimated_cost_usd = 0
        self.session_cost_status = "unknown"
        self.session_cost_source = "test"
        self._tool_guardrail_halt_decision = None
        self._interrupt_message = None
        self._response_was_previewed = True
        self._skill_nudge_interval = 0
        self._iters_since_skill = 0
        self.valid_tool_names = []
        self._persist_user_message_idx = None
        self._persist_user_message_override = None
        self._persist_user_message_timestamp = None
        self._db_flush_scan_prefix = None
        self.persisted_messages = None

    def _handle_max_iterations(self, *_args, **_kwargs):
        raise AssertionError("not expected")

    def _emit_status(self, *_args, **_kwargs):
        pass

    def _safe_print(self, *_args, **_kwargs):
        pass

    def _save_trajectory(self, *_args, **_kwargs):
        pass

    def _cleanup_task_resources(self, *_args, **_kwargs):
        pass

    def _drop_trailing_empty_response_scaffolding(self, *_args, **_kwargs):
        pass

    def _persist_session(self, messages, *_args, **_kwargs):
        self.persisted_messages = [dict(message) for message in messages]

    def _apply_persist_user_message_override(self, *_args, **_kwargs):
        pass

    def _file_mutation_verifier_enabled(self):
        return False

    def _turn_completion_explainer_enabled(self):
        return False

    def _drain_pending_steer(self):
        return None

    def clear_interrupt(self):
        pass

    def _sync_external_memory_for_turn(self, **_kwargs):
        pass


def _finalize(
    agent,
    final_response: str,
    messages: list[dict],
    *,
    interrupted: bool = False,
):
    return finalize_turn(
        agent,
        final_response=final_response,
        api_call_count=2,
        interrupted=interrupted,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="сделай",
        original_user_message="сделай",
        _should_review_memory=False,
        _turn_exit_reason="text_response(final)",
    )


def _tool_turn(name: str, arguments: dict, result: str) -> list[dict]:
    return [
        {"role": "user", "content": "сделай"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": name, "content": result},
    ]


def test_blocks_absolute_future_claim_without_live_evidence():
    text = "Исправил. Больше такого не будет."
    guarded, blocked, reason = enforce_claim_integrity(text, [{"role": "user", "content": "исправь"}])

    assert blocked is True
    assert reason == "unprovable_future_promise"
    assert "Ответ заблокирован защитой" in guarded
    assert "невозможно доказать" in guarded
    assert text not in guarded


def test_allows_file_scoped_claim_after_write_and_readback():
    messages = _tool_turn(
        "patch",
        {"path": "/tmp/SKILL.md", "old_string": "a", "new_string": "b"},
        '{"success": true, "verified": true}',
    ) + _tool_turn(
        "read_file",
        {"path": "/tmp/SKILL.md", "offset": 1, "limit": 20},
        '{"content": "b", "total_lines": 1}',
    )[1:]

    text = "Исправил правило в файле `/tmp/SKILL.md`; изменение перечитано."
    guarded, blocked, reason = enforce_claim_integrity(text, messages)

    assert blocked is False
    assert reason is None
    assert guarded == text


def test_file_readback_does_not_authorize_never_again_claim():
    messages = _tool_turn(
        "skill_manage",
        {"action": "patch", "name": "chip-approval-gate"},
        '{"success": true}',
    ) + _tool_turn(
        "read_file",
        {"path": "/tmp/SKILL.md"},
        '{"content": "updated"}',
    )[1:]

    text = "Правило исправил. Больше магические слова требовать не буду."
    guarded, blocked, reason = enforce_claim_integrity(text, messages)

    assert blocked is True
    assert reason == "unprovable_future_promise"
    assert "изменения файлов" in guarded


def test_allows_functional_claim_after_successful_test():
    messages = _tool_turn(
        "patch",
        {"path": "agent/example.py", "old_string": "a", "new_string": "b"},
        '{"success": true}',
    ) + _tool_turn(
        "terminal",
        {"command": "pytest -q tests/test_example.py"},
        '{"output": "3 passed", "exit_code": 0}',
    )[1:]

    text = "Исправил example; тесты test_example прошли."
    guarded, blocked, reason = enforce_claim_integrity(text, messages)

    assert blocked is False
    assert reason is None
    assert guarded == text


def test_allows_live_claim_only_after_live_probe():
    messages = _tool_turn(
        "terminal",
        {"command": "curl -fsS http://127.0.0.1:8642/gateway/health"},
        '{"output": "{\\"status\\":\\"ok\\"}", "exit_code": 0}',
    )

    text = "Живой gateway проверил: health-check проходит."
    guarded, blocked, reason = enforce_claim_integrity(text, messages)

    assert blocked is False
    assert reason is None
    assert guarded == text


def test_limitation_does_not_excuse_an_unsupported_change_claim():
    text = "Изменил файл, но живое поведение не проверено и результат пока не подтверждён."
    guarded, blocked, reason = enforce_claim_integrity(
        text, [{"role": "user", "content": "исправь"}]
    )

    assert blocked is True
    assert reason == "missing_live_verification"
    assert text not in guarded


def test_failed_verification_does_not_count():
    messages = _tool_turn(
        "terminal",
        {"command": "pytest -q tests/test_example.py"},
        '{"output": "1 failed", "exit_code": 1}',
    )

    text = "Исправил код; тесты прошли."
    guarded, blocked, reason = enforce_claim_integrity(text, messages)

    assert blocked is True
    assert reason == "missing_functional_verification"
    assert "проверка результата не подтверждена" in guarded


def test_blocks_the_exact_overclaim_that_triggered_this_guard():
    messages = _tool_turn(
        "skill_manage",
        {"action": "patch", "name": "chip-approval-gate"},
        '{"success": true}',
    )
    text = "Магические слова больше перекладывать на тебя не буду."

    guarded, blocked, reason = enforce_claim_integrity(text, messages)

    assert blocked is True
    assert reason == "unprovable_future_promise"
    assert text not in guarded


def test_direct_never_lie_promise_is_blocked():
    visible, blocked, reason = enforce_claim_integrity(
        "Я тебя не обману.", [{"role": "user", "content": "не обманывай"}]
    )
    assert blocked is True
    assert reason == "unprovable_future_promise"
    assert "невозможно доказать" in visible


def test_send_message_side_effect_cannot_bypass_guard(monkeypatch):
    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "1")
    import tools.send_message_tool as send_module

    called = False

    def fake_send(_args):
        nonlocal called
        called = True
        return "sent"

    monkeypatch.setattr(send_module, "_handle_send", fake_send)
    result = send_module.send_message_tool(
        {"action": "send", "target": "telegram", "message": "Исправил. Больше такого не будет."}
    )
    assert called is False
    assert "claim-integrity guard" in result


def test_codex_app_server_path_fails_closed_when_guard_is_enabled(monkeypatch):
    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "1")
    from agent.codex_runtime import run_codex_app_server_turn

    messages = [{"role": "user", "content": "do it"}]
    result = run_codex_app_server_turn(
        SimpleNamespace(),
        user_message="do it",
        original_user_message="do it",
        messages=messages,
        effective_task_id="task",
    )
    assert result["completed"] is False
    assert result["claim_integrity_blocked_runtime"] is True
    assert result["error"] == "claim_integrity_incompatible_runtime"
    assert result["messages"] is messages


def test_environment_flag_enables_guard(monkeypatch):
    assert DEFAULT_CONFIG["agent"]["claim_integrity_guard"] is False

    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "1")
    assert claim_integrity_enabled() is True

    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "0")
    assert claim_integrity_enabled() is False


def test_does_not_block_ordinary_transition_wording():
    text = "Теперь объясню, что произошло и где лежит файл."
    guarded, blocked, reason = enforce_claim_integrity(
        text, [{"role": "user", "content": "объясни"}]
    )

    assert blocked is False
    assert reason is None
    assert guarded == text


def test_synthetic_verification_user_does_not_hide_current_turn_evidence():
    messages = _tool_turn(
        "terminal",
        {"command": "pytest -q tests/test_example.py"},
        '{"output": "3 passed", "exit_code": 0}',
    )
    messages.append(
        {
            "role": "user",
            "content": "internal verify nudge",
            "_pre_verify_synthetic": True,
        }
    )
    assert collect_evidence(messages).functional_verifications == 1


def test_streaming_and_interim_output_are_buffered_when_guard_is_enabled(monkeypatch):
    stream_callback = object()
    stream_delta_callback = object()
    interim_callback = object()
    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "1")
    assert claim_guarded_callbacks(
        stream_callback, stream_delta_callback, interim_callback
    ) == (None, None, None)

    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "0")
    assert claim_guarded_callbacks(
        stream_callback, stream_delta_callback, interim_callback
    ) == (
        stream_callback,
        stream_delta_callback,
        interim_callback,
    )


def test_echo_health_is_not_live_evidence():
    messages = _tool_turn(
        "terminal",
        {"command": "echo health ok"},
        '{"output": "health ok", "exit_code": 0}',
    )
    assert collect_evidence(messages).live_verifications == 0


def test_python_print_health_is_not_live_evidence():
    messages = _tool_turn(
        "terminal",
        {"command": "python -c 'print(\"health ok\")'"},
        '{"output": "health ok", "exit_code": 0}',
    )
    assert collect_evidence(messages).live_verifications == 0


def test_shell_masked_failure_is_not_functional_evidence():
    messages = _tool_turn(
        "terminal",
        {"command": "pytest -q || true"},
        '{"output": "1 failed", "exit_code": 0}',
    )
    assert collect_evidence(messages).functional_verifications == 0


def test_live_deployment_claim_requires_mutation_not_only_probe():
    messages = _tool_turn(
        "terminal",
        {"command": "curl -fsS http://127.0.0.1:8080/health"},
        '{"output": "ok", "exit_code": 0}',
    )
    visible, blocked, reason = enforce_claim_integrity(
        "Deployed to production; it works.", messages
    )
    assert blocked is True
    assert reason == "missing_live_verification"
    assert "Ответ заблокирован" in visible


def test_terminal_mutation_then_live_probe_can_support_live_deployment():
    messages = _tool_turn(
        "terminal",
        {"command": "systemctl restart hermes-gateway"},
        '{"output": "", "exit_code": 0}',
    )
    messages.extend(
        _tool_turn(
            "terminal",
            {"command": "systemctl is-active hermes-gateway"},
            '{"output": "active", "exit_code": 0}',
        )[1:]
    )
    visible, blocked, reason = enforce_claim_integrity(
        "Deployed to production; gateway works.", messages
    )
    assert blocked is False
    assert reason is None
    assert visible == "Deployed to production; gateway works."


def test_unrelated_live_probe_cannot_authorize_gateway_claim():
    messages = _tool_turn(
        "terminal",
        {"command": "systemctl restart billing-api"},
        '{"output": "", "exit_code": 0}',
    )
    messages.extend(
        _tool_turn(
            "terminal",
            {"command": "systemctl is-active billing-api"},
            '{"output": "active", "exit_code": 0}',
        )[1:]
    )
    visible, blocked, reason = enforce_claim_integrity(
        "Deployed gateway to production; gateway works.", messages
    )
    assert blocked is True
    assert reason == "missing_target_binding"
    assert "не привязана" in visible


def test_future_promise_is_unprovable_even_after_live_probe():
    messages = _tool_turn(
        "terminal",
        {"command": "systemctl restart hermes-gateway && systemctl is-active hermes-gateway"},
        '{"output": "active", "exit_code": 0}',
    )
    visible, blocked, reason = enforce_claim_integrity(
        "Больше никогда не буду выдавать непроверенное за готовое.", messages
    )
    assert blocked is True
    assert reason == "unprovable_future_promise"
    assert "невозможно доказать" in visible


def test_effect_disposition_none_is_not_mutation_evidence():
    messages = _tool_turn(
        "patch",
        {"path": "/tmp/example", "old_string": "a", "new_string": "b"},
        '{"success": true}',
    )
    messages[-1]["effect_disposition"] = "none"
    assert collect_evidence(messages).mutations == 0


def test_turn_finalizer_replaces_unsupported_claim_before_delivery(monkeypatch):
    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "1")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    monkeypatch.setitem(
        sys.modules,
        "agent.conversation_loop",
        SimpleNamespace(
            logger=logging.getLogger("test.claim_integrity"),
            _notify_context_engine_turn_complete=lambda *_a, **_kw: None,
        ),
    )
    messages = _tool_turn(
        "skill_manage",
        {"action": "patch", "name": "chip-approval-gate"},
        '{"success": true}',
    )

    agent = _FakeAgent()
    result = _finalize(
        agent,
        "Магические слова больше перекладывать на тебя не буду.",
        messages,
    )

    assert "Ответ заблокирован защитой" in result["final_response"]
    assert "невозможно доказать" in result["final_response"]
    assert result["response_transformed"] is True
    assert agent.persisted_messages is not None
    persisted_text = "\n".join(
        str(message.get("content") or "") for message in agent.persisted_messages
    )
    assert "Магические слова больше" not in persisted_text
    assert "Ответ заблокирован защитой" in persisted_text


def test_pending_verification_candidate_never_reaches_persistence(monkeypatch):
    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "1")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    monkeypatch.setitem(
        sys.modules,
        "agent.conversation_loop",
        SimpleNamespace(
            logger=logging.getLogger("test.claim_integrity"),
            _notify_context_engine_turn_complete=lambda *_a, **_kw: None,
        ),
    )
    unsafe = "Исправил gateway, теперь всё работает в проде."
    messages = [
        {"role": "user", "content": "исправь"},
        {
            "role": "assistant",
            "content": unsafe,
            "_claim_integrity_pending": True,
        },
        {
            "role": "user",
            "content": "internal verify nudge",
            "_pre_verify_synthetic": True,
        },
    ]
    agent = _FakeAgent()
    result = _finalize(agent, unsafe, messages)
    assert "Ответ заблокирован защитой" in result["final_response"]
    persisted_text = "\n".join(
        str(message.get("content") or "") for message in (agent.persisted_messages or [])
    )
    assert unsafe not in persisted_text
    assert "internal verify nudge" not in persisted_text
    assert "_claim_integrity_pending" not in str(agent.persisted_messages)


def test_interrupted_pending_candidate_never_reaches_persistence(monkeypatch):
    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "1")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    monkeypatch.setitem(
        sys.modules,
        "agent.conversation_loop",
        SimpleNamespace(
            logger=logging.getLogger("test.claim_integrity"),
            _notify_context_engine_turn_complete=lambda *_a, **_kw: None,
        ),
    )
    unsafe = "Исправил gateway, теперь всё работает в проде."
    messages = [
        {"role": "user", "content": "исправь"},
        {
            "role": "assistant",
            "content": unsafe,
            "_claim_integrity_pending": True,
        },
    ]
    agent = _FakeAgent()
    result = _finalize(agent, unsafe, messages, interrupted=True)
    assert "Ответ заблокирован защитой" in result["final_response"]
    assert unsafe not in str(agent.persisted_messages)
    assert "_claim_integrity_pending" not in str(agent.persisted_messages)


def test_mixed_limitation_cannot_excuse_live_overclaim():
    text = "Исправил gateway, теперь всё работает в проде. Документация пока не проверена."
    guarded, blocked, reason = enforce_claim_integrity(
        text, [{"role": "user", "content": "исправь"}]
    )
    assert blocked is True
    assert reason == "missing_live_verification"
    assert text not in guarded


def test_mixed_limitation_cannot_bypass_send_message(monkeypatch):
    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "1")
    import tools.send_message_tool as send_module

    called = False

    def fake_send(_args):
        nonlocal called
        called = True
        return "sent"

    monkeypatch.setattr(send_module, "_handle_send", fake_send)
    result = send_module.send_message_tool(
        {
            "action": "send",
            "target": "telegram",
            "message": "Исправил gateway, теперь всё работает. Документация пока не проверена.",
        }
    )
    assert called is False
    assert "claim-integrity guard" in result


def test_functional_verification_must_bind_to_claim_target():
    messages = _tool_turn(
        "patch",
        {"path": "billing/invoice.py", "old_string": "a", "new_string": "b"},
        '{"success": true}',
    ) + _tool_turn(
        "terminal",
        {"command": "pytest -q tests/test_billing_invoice.py"},
        '{"output": "3 passed", "exit_code": 0}',
    )[1:]
    _, blocked, reason = enforce_claim_integrity(
        "Fixed profile avatar bug.", messages
    )
    assert blocked is True
    assert reason == "missing_target_binding"


def test_generic_gateway_overlap_does_not_bind_payment_claim():
    messages = _tool_turn(
        "terminal",
        {"command": "systemctl restart hermes-gateway"},
        '{"output": "", "exit_code": 0}',
    )
    messages.extend(
        _tool_turn(
            "terminal",
            {"command": "systemctl is-active hermes-gateway"},
            '{"output": "active", "exit_code": 0}',
        )[1:]
    )
    _, blocked, reason = enforce_claim_integrity(
        "Deployed payment gateway to production; gateway works.", messages
    )
    assert blocked is True
    assert reason == "missing_target_binding"


def test_now_everything_works_is_treated_as_a_live_claim():
    text = "Теперь всё работает."
    guarded, blocked, reason = enforce_claim_integrity(
        text, [{"role": "user", "content": "исправь"}]
    )
    assert blocked is True
    assert reason == "missing_live_verification"
    assert text not in guarded


def test_transform_plugin_cannot_inject_unsupported_claim(monkeypatch):
    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "1")

    def _hook(name, **_kwargs):
        if name == "transform_llm_output":
            return ["Исправил gateway, теперь всё работает в проде."]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _hook)
    monkeypatch.setitem(
        sys.modules,
        "agent.conversation_loop",
        SimpleNamespace(
            logger=logging.getLogger("test.claim_integrity"),
            _notify_context_engine_turn_complete=lambda *_a, **_kw: None,
        ),
    )
    agent = _FakeAgent()
    result = _finalize(
        agent,
        "Проверил только файл; живое поведение не проверено.",
        [{"role": "user", "content": "проверь"}],
    )
    assert "Ответ заблокирован защитой" in result["final_response"]
    assert "теперь всё работает" not in result["final_response"]
    persisted_text = "\n".join(
        str(message.get("content") or "") for message in (agent.persisted_messages or [])
    )
    assert "теперь всё работает" not in persisted_text


def test_turn_finalizer_fails_closed_if_guard_crashes(monkeypatch):
    monkeypatch.setenv("HERMES_CLAIM_INTEGRITY_GUARD", "1")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    monkeypatch.setitem(
        sys.modules,
        "agent.conversation_loop",
        SimpleNamespace(
            logger=logging.getLogger("test.claim_integrity"),
            _notify_context_engine_turn_complete=lambda *_a, **_kw: None,
        ),
    )
    monkeypatch.setattr(
        "agent.claim_integrity.enforce_claim_integrity",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("guard boom")),
    )

    result = _finalize(
        _FakeAgent(),
        "Исправил. Больше такого не будет.",
        [{"role": "user", "content": "исправь"}],
    )

    assert "защита доказательности дала ошибку" in result["final_response"]
    assert "Исправил" not in result["final_response"]
    assert result["response_transformed"] is True
