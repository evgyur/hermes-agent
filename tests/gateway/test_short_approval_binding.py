"""Maintained regression tests for bounded short-approval context binding."""

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource


async def _prepare(text, history):
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")}
    )
    runner.adapters = {}
    runner._model = "test"
    runner._base_url = None
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        thread_id="77",
        chat_type="group",
        user_id="owner",
    )
    return await runner._prepare_inbound_message_text(
        event=MessageEvent(text=text, source=source, message_id="1"),
        source=source,
        history=history,
    )


@pytest.mark.asyncio
async def test_latest_user_correction_supersedes_old_assistant_effect():
    old = "Deploy candidate A now."
    correction = "Нет, не публикуй. Сначала сравни кандидата с upstream."
    prepared = await _prepare(
        "Го",
        [{"role": "assistant", "content": old}, {"role": "user", "content": correction}],
    )
    assert correction in prepared
    assert old not in prepared


@pytest.mark.asyncio
async def test_status_candidate_is_fail_closed_and_fuzzy_token_is_plain_text():
    status = "I can see the gateway is healthy. Next maintenance is Tuesday."
    prepared = await _prepare(
        "Go",
        [
            {"role": "user", "content": "How is the gateway?"},
            {"role": "assistant", "content": status, "finish_reason": "stop"},
        ],
    )
    assert status in prepared
    assert "clarifying" in prepared.lower()

    plain = await _prepare("g.o", [{"role": "assistant", "content": "Deploy now."}])
    assert "Short approval" not in plain


def _nonsemantic_noise(count=40):
    rows = []
    for index in range(count):
        if index % 3 == 0:
            rows.append({"role": "tool", "content": f"tool result {index}"})
        elif index % 3 == 1:
            rows.append({"role": "system", "content": f"callback {index}"})
        else:
            rows.append(
                {
                    "role": "assistant",
                    "content": f"hidden callback {index}",
                    "display_kind": "internal_notification",
                }
            )
    return rows


@pytest.mark.asyncio
async def test_nonsemantic_flood_does_not_evict_visible_approval_antecedent():
    latest = "Compare the candidate with upstream, then report the differences."
    prepared = await _prepare(
        "Go",
        [
            {"role": "user", "content": "What is the next action?"},
            {"role": "assistant", "content": latest, "finish_reason": "stop"},
            *_nonsemantic_noise(),
        ],
    )
    assert latest in prepared
    assert "Short approval" in prepared


@pytest.mark.asyncio
async def test_nonsemantic_flood_does_not_evict_latest_user_correction():
    correction = "Do not deploy it. Compare it against upstream only."
    prepared = await _prepare(
        "Го",
        [
            {"role": "assistant", "content": "Deploy candidate A now."},
            {"role": "user", "content": correction},
            *_nonsemantic_noise(),
        ],
    )
    assert correction in prepared
    assert "Deploy candidate A now." not in prepared


@pytest.mark.asyncio
async def test_approval_without_visible_antecedent_is_explicitly_fail_closed():
    prepared = await _prepare("Го", _nonsemantic_noise())
    assert "clarifying" in prepared.lower()
    assert "no effect" in prepared.lower()


@pytest.mark.asyncio
async def test_short_approval_uses_successful_visible_final_not_codex_commentary():
    action = "Compare candidate A with upstream and report the delta."
    prepared = await _prepare(
        "continue",
        [
            {"role": "user", "content": "What should we do?"},
            {"role": "assistant", "content": action, "finish_reason": "stop"},
            {
                "role": "assistant",
                "content": "Internal progress commentary",
                "finish_reason": "stop",
                "codex_message_items": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "phase": "commentary",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "still working"}
                        ],
                    }
                ],
            },
        ],
    )
    assert action in prepared
    assert "Internal progress commentary" not in prepared
    assert "ambiguous" not in prepared.lower()


@pytest.mark.asyncio
async def test_prodolzhai_is_not_a_short_approval_token():
    prepared = await _prepare(
        "продолжай",
        [
            {"role": "user", "content": "What should we do?"},
            {
                "role": "assistant",
                "content": "Deploy candidate A.",
                "finish_reason": "stop",
            },
        ],
    )
    assert prepared == "продолжай"


@pytest.mark.asyncio
async def test_visible_final_limit_keeps_the_newest_candidates():
    history = [{"role": "user", "content": "Choose the next action."}]
    for index in range(25):
        history.append(
            {
                "role": "assistant",
                "content": f"Candidate {index}",
                "finish_reason": "stop",
            }
        )
        history.append({"role": "user", "content": f"Follow-up {index}"})
    history.append(
        {
            "role": "assistant",
            "content": "Newest candidate",
            "finish_reason": "stop",
        }
    )

    prepared = await _prepare("Го", history)
    assert "Newest candidate" in prepared
    assert "Candidate 0" not in prepared
