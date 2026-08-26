"""Generation-bound agent hooks for durable gateway corrections."""

import threading

from unittest.mock import Mock


def test_agent_receipt_transitions_follow_request_fence_then_provider_result():
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent._pending_steer = None
    agent._pending_steer_lock = threading.Lock()
    transitions = []

    assert agent.steer(
        "change course",
        receipt_id="r1",
        receipt_transition=lambda receipt_id, state: transitions.append(
            (receipt_id, state)
        ),
    )
    assert agent._drain_pending_steer() == "change course"
    assert transitions == []
    agent._mark_drained_steer_request_fenced()
    assert transitions == [("r1", "REQUEST_FENCED")]
    agent._mark_fenced_steer_provider_result(accepted=True)
    assert transitions == [
        ("r1", "REQUEST_FENCED"),
        ("r1", "CONSUMED_CURRENT"),
    ]


def test_redirect_during_tools_retains_redirect_receipt_identity():
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent._pending_steer = None
    agent._pending_steer_lock = threading.Lock()
    agent._executing_tools = True
    transitions = []

    assert agent.redirect(
        "use the other path",
        receipt_id="redirect-1",
        receipt_transition=lambda receipt_id, state: transitions.append(
            (receipt_id, state)
        ),
    )
    assert agent._drain_pending_steer() == "use the other path"
    agent._mark_drained_steer_request_fenced()
    agent._mark_fenced_steer_provider_result(accepted=False)
    assert transitions == [
        ("redirect-1", "REQUEST_FENCED"),
        ("redirect-1", "AMBIGUOUS_PROVIDER_REQUEST"),
    ]


def test_active_redirect_receipt_follows_correction_into_next_request():
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent.api_mode = "chat_completions"
    agent._executing_tools = False
    agent._model_request_active = Mock()
    agent._model_request_active.is_set.return_value = True
    agent._pending_redirect_lock = threading.Lock()
    agent._pending_redirect = None
    agent._pending_redirect_receipts = [("old-redirect", lambda *_: None)]
    agent._interrupt_requested = False
    agent._interrupt_message = None
    agent._execution_thread_id = None
    agent._active_request_abort = None
    transitions = []

    assert agent.redirect(
        "correct this",
        receipt_id="redirect-2",
        receipt_transition=lambda receipt_id, state: transitions.append(
            (receipt_id, state)
        ),
    )
    assert agent._drain_pending_redirect() == "correct this"
    agent._mark_drained_steer_request_fenced()
    agent._mark_fenced_steer_provider_result(accepted=True)
    assert transitions == [
        ("redirect-2", "REQUEST_FENCED"),
        ("redirect-2", "CONSUMED_CURRENT"),
    ]


def test_production_redirect_then_steer_drain_order_preserves_redirect_receipt():
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent._pending_redirect = "redirect"
    agent._pending_redirect_lock = threading.Lock()
    agent._pending_redirect_receipts = [("redirect-3", lambda *_: None)]
    agent._pending_steer = None
    agent._pending_steer_lock = threading.Lock()

    assert agent._drain_pending_redirect() == "redirect"
    assert agent._drain_pending_steer() is None
    assert [receipt_id for receipt_id, _ in agent._drained_steer_receipts] == [
        "redirect-3"
    ]


def test_hard_interrupt_drops_unfenced_receipt_from_successor_agent_state():
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent._pending_steer = None
    agent._pending_steer_lock = threading.Lock()
    agent._pending_redirect_lock = threading.Lock()
    agent._pending_redirect = None
    agent._interrupt_requested = True
    agent._interrupt_message = None
    agent._tool_interrupt_reason = None
    agent._interrupt_thread_signal_pending = False
    agent._execution_thread_id = None
    agent._tool_worker_threads = None
    agent._tool_worker_threads_lock = None

    assert agent.steer("old generation", receipt_id="stale", receipt_transition=lambda *_: None)
    assert agent._pending_steer_receipts
    assert agent.clear_interrupt()
    assert agent._pending_steer_receipts == []
    assert agent._pending_redirect_receipts == []
