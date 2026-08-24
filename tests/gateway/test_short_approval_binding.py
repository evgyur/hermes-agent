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
    prepared = await _prepare("Go", [{"role": "assistant", "content": status}])
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
        [{"role": "assistant", "content": latest}, *_nonsemantic_noise()],
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
