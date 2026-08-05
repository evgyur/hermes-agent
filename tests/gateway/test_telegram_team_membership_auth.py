"""Contract tests for Telegram authority-supergroup membership policy."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.telegram_team_membership import TelegramTeamMembershipPolicy


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_policy(
    *,
    status="member",
    positive_ttl=30.0,
    negative_ttl=5.0,
    allowed_group_chat_ids=None,
):
    clock = Clock()
    get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=status, user=SimpleNamespace(is_bot=False))
    )
    policy = TelegramTeamMembershipPolicy(
        authority_chat_id="authority-chat",
        get_chat_member=get_chat_member,
        positive_ttl_seconds=positive_ttl,
        negative_ttl_seconds=negative_ttl,
        allowed_group_chat_ids=allowed_group_chat_ids,
        clock=clock,
    )
    return policy, get_chat_member, clock


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["member", "administrator", "creator", "owner"])
async def test_current_human_members_are_admitted(status):
    policy, get_chat_member, _ = make_policy(status=status)

    decision = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )

    assert decision.allowed is True
    assert decision.reason == "current_member"
    get_chat_member.assert_awaited_once_with("authority-chat", "actor-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["left", "kicked", "banned", "unknown"])
async def test_non_members_are_denied(status):
    policy, _, _ = make_policy(status=status)

    decision = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )

    assert decision.allowed is False
    assert decision.reason == "not_current_member"


@pytest.mark.asyncio
async def test_restricted_user_is_allowed_only_when_still_a_member():
    lookup = AsyncMock(
        side_effect=[
            SimpleNamespace(
                status="restricted",
                is_member=True,
                user=SimpleNamespace(is_bot=False),
            ),
            SimpleNamespace(
                status="restricted",
                is_member=False,
                user=SimpleNamespace(is_bot=False),
            ),
        ]
    )
    policy = TelegramTeamMembershipPolicy(
        authority_chat_id="authority-chat",
        get_chat_member=lookup,
    )

    current = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )
    removed = await policy.authorize(
        user_id="actor-2",
        source_chat_id="actor-2",
        source_chat_type="private",
        sender_is_bot=False,
    )

    assert current.allowed is True
    assert removed.allowed is False


@pytest.mark.asyncio
async def test_anonymous_and_bot_senders_are_denied_without_api_lookup():
    policy, get_chat_member, _ = make_policy()

    anonymous = await policy.authorize(
        user_id=None,
        source_chat_id="authority-chat",
        source_chat_type="group",
        sender_is_bot=False,
    )
    bot = await policy.authorize(
        user_id="bot-1",
        source_chat_id="authority-chat",
        source_chat_type="group",
        sender_is_bot=True,
    )

    assert (anonymous.allowed, anonymous.reason) == (False, "anonymous_sender")
    assert (bot.allowed, bot.reason) == (False, "bot_sender")
    get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_authority_group_event_proves_presence_for_that_turn():
    policy, get_chat_member, _ = make_policy(status="left")

    decision = await policy.authorize(
        user_id="actor-1",
        source_chat_id="authority-chat",
        source_chat_type="group",
        sender_is_bot=False,
    )

    assert decision.allowed is True
    assert decision.reason == "authority_group_turn"
    get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_configured_shared_group_requires_current_authority_membership():
    policy, get_chat_member, _ = make_policy(
        allowed_group_chat_ids=["shared-chat"]
    )

    decision = await policy.authorize(
        user_id="actor-1",
        source_chat_id="shared-chat",
        source_chat_type="supergroup",
        sender_is_bot=False,
    )

    assert decision.allowed is True
    assert decision.reason == "current_member"
    get_chat_member.assert_awaited_once_with("authority-chat", "actor-1")


@pytest.mark.asyncio
async def test_unconfigured_shared_group_remains_fail_closed():
    policy, get_chat_member, _ = make_policy(
        allowed_group_chat_ids=["shared-chat"]
    )

    decision = await policy.authorize(
        user_id="actor-1",
        source_chat_id="other-chat",
        source_chat_type="supergroup",
        sender_is_bot=False,
    )

    assert (decision.allowed, decision.reason) == (
        False,
        "chat_scope_not_authorized",
    )
    get_chat_member.assert_not_awaited()


def test_core_and_plugin_adapters_wire_allowed_shared_group_scope():
    from gateway.config import PlatformConfig
    from gateway.platforms.telegram import TelegramAdapter as CoreTelegramAdapter
    from plugins.platforms.telegram.adapter import (
        TelegramAdapter as PluginTelegramAdapter,
    )

    for adapter_type in (CoreTelegramAdapter, PluginTelegramAdapter):
        adapter = adapter_type(
            PlatformConfig(
                enabled=True,
                token="test",
                typing_indicator=False,
                extra={
                    "team_authority_chat_id": "authority-chat",
                    "team_allowed_group_chat_ids": ["shared-chat"],
                },
            )
        )
        assert adapter._team_membership_policy is not None
        assert adapter._team_membership_policy.allowed_group_chat_ids == frozenset(
            {"shared-chat"}
        )


@pytest.mark.asyncio
async def test_positive_and_negative_results_use_separate_bounded_ttls():
    policy, get_chat_member, clock = make_policy(positive_ttl=30.0, negative_ttl=5.0)

    first = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )
    second = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )
    assert first.allowed and second.allowed
    assert get_chat_member.await_count == 1

    policy.invalidate("actor-1")
    get_chat_member.return_value.status = "left"
    denied = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )
    assert denied.allowed is False
    assert get_chat_member.await_count == 2

    get_chat_member.return_value.status = "member"
    clock.advance(4.0)
    still_denied = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )
    assert still_denied.allowed is False
    assert get_chat_member.await_count == 2

    clock.advance(2.0)
    admitted = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )
    assert admitted.allowed is True
    assert get_chat_member.await_count == 3


@pytest.mark.asyncio
async def test_bot_api_outage_is_fail_closed_and_negative_cached():
    policy, get_chat_member, _ = make_policy()
    get_chat_member.side_effect = RuntimeError("network unavailable")

    first = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )
    second = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )

    assert (first.allowed, first.reason) == (False, "membership_lookup_failed")
    assert second.allowed is False
    assert get_chat_member.await_count == 1


@pytest.mark.asyncio
async def test_invalidation_forces_fresh_membership_lookup():
    policy, get_chat_member, _ = make_policy()

    await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )
    policy.invalidate("actor-1")
    await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )

    assert get_chat_member.await_count == 2


def test_membership_stamp_is_local_and_wire_invisible():
    from gateway.config import Platform
    from gateway.session import SessionSource

    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="actor-1",
        user_id="actor-1",
        telegram_team_membership_required=True,
        telegram_team_membership_authorized=True,
        telegram_team_membership_reason="current_member",
    )

    wire = source.to_dict()
    assert "telegram_team_membership_required" not in wire
    assert "telegram_team_membership_authorized" not in wire
    assert "telegram_team_membership_reason" not in wire

    restored = SessionSource.from_dict(wire)
    assert restored.telegram_team_membership_required is False
    assert restored.telegram_team_membership_authorized is False
    assert restored.telegram_team_membership_reason is None


def test_enabled_team_policy_precedes_static_allowlists_and_pairing(monkeypatch):
    from gateway.authz_mixin import GatewayAuthorizationMixin
    from gateway.config import Platform
    from gateway.session import SessionSource

    class Runner(GatewayAuthorizationMixin):
        pass

    runner = Runner()
    runner.adapters = {
        Platform.TELEGRAM: SimpleNamespace(_team_membership_policy=object())
    }
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "*")
    monkeypatch.setenv("TELEGRAM_GROUP_ALLOWED_CHATS", "*")
    monkeypatch.setenv("TELEGRAM_ALLOW_ALL_USERS", "true")
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")

    denied = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="actor-1",
        user_id="actor-1",
        telegram_team_membership_required=True,
        telegram_team_membership_authorized=False,
        telegram_team_membership_reason="not_current_member",
    )
    admitted = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="actor-1",
        user_id="actor-1",
        telegram_team_membership_required=True,
        telegram_team_membership_authorized=True,
        telegram_team_membership_reason="current_member",
    )
    unstamped = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="actor-1",
        user_id="actor-1",
    )

    assert runner._is_user_authorized(denied) is False
    assert runner._is_user_authorized(admitted) is True
    assert runner._is_user_authorized(unstamped) is False


@pytest.mark.asyncio
async def test_authority_group_observation_populates_bounded_positive_cache():
    policy, get_chat_member, _ = make_policy()

    group_turn = await policy.authorize(
        user_id="actor-1",
        source_chat_id="authority-chat",
        source_chat_type="forum",
        sender_is_bot=False,
    )
    dm_turn = await policy.authorize(
        user_id="actor-1",
        source_chat_id="actor-1",
        source_chat_type="private",
        sender_is_bot=False,
    )

    assert group_turn.allowed is True
    assert dm_turn.allowed is True
    get_chat_member.assert_not_awaited()


def test_chat_member_updates_share_the_same_human_status_rules():
    allowed = SimpleNamespace(
        status="administrator",
        user=SimpleNamespace(is_bot=False),
    )
    removed = SimpleNamespace(status="left", user=SimpleNamespace(is_bot=False))
    bot = SimpleNamespace(status="member", user=SimpleNamespace(is_bot=True))

    assert TelegramTeamMembershipPolicy.member_is_allowed(allowed) is True
    assert TelegramTeamMembershipPolicy.member_is_allowed(removed) is False
    assert TelegramTeamMembershipPolicy.member_is_allowed(bot) is False


@pytest.mark.asyncio
async def test_raw_text_denial_precedes_observe_batch_and_event_creation():
    from unittest.mock import Mock

    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    msg = SimpleNamespace(text="hello")
    adapter._effective_update_message = Mock(return_value=msg)
    adapter._team_membership_allows_message = AsyncMock(return_value=False)
    adapter._is_native_voice_transcript_followup = Mock()
    update = SimpleNamespace(update_id=1)

    await TelegramAdapter._handle_text_message(adapter, update, SimpleNamespace())

    adapter._team_membership_allows_message.assert_awaited_once_with(msg)
    adapter._is_native_voice_transcript_followup.assert_not_called()


@pytest.mark.asyncio
async def test_negative_source_decision_stops_before_base_session_state(monkeypatch):
    from unittest.mock import AsyncMock

    from gateway.config import Platform
    from gateway.platforms.base import BasePlatformAdapter
    from gateway.platforms.telegram import TelegramAdapter
    from gateway.session import SessionSource

    adapter = object.__new__(TelegramAdapter)
    adapter._team_membership_policy = SimpleNamespace(
        authorize=AsyncMock(
            return_value=SimpleNamespace(
                allowed=False,
                reason="not_current_member",
            )
        )
    )
    base_handle = AsyncMock()
    monkeypatch.setattr(BasePlatformAdapter, "handle_message", base_handle)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="actor-1",
        user_id="actor-1",
    )
    event = SimpleNamespace(source=source)

    result = await TelegramAdapter.handle_message(adapter, event)

    assert result is None
    assert source.telegram_team_membership_required is True
    assert source.telegram_team_membership_authorized is False
    assert source.telegram_team_membership_reason == "not_current_member"
    base_handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidation_during_lookup_cannot_restore_stale_positive():
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_lookup(chat_id, user_id):
        assert (chat_id, user_id) == ("authority-chat", "actor-1")
        started.set()
        await release.wait()
        return SimpleNamespace(status="member", user=SimpleNamespace(is_bot=False))

    policy = TelegramTeamMembershipPolicy(
        authority_chat_id="authority-chat",
        get_chat_member=delayed_lookup,
    )
    pending = asyncio.create_task(
        policy.authorize(
            user_id="actor-1",
            source_chat_id="actor-1",
            source_chat_type="private",
            sender_is_bot=False,
        )
    )

    await started.wait()
    policy.invalidate("actor-1")
    release.set()
    decision = await pending

    assert (decision.allowed, decision.reason) == (
        False,
        "membership_invalidated_during_lookup",
    )
    assert policy.cached_decision("actor-1") is None


@pytest.mark.asyncio
async def test_current_member_is_denied_outside_dm_and_authority_group():
    policy, get_chat_member, _ = make_policy(status="member")

    decision = await policy.authorize(
        user_id="actor-1",
        source_chat_id="external-group",
        source_chat_type="group",
        sender_is_bot=False,
    )

    assert (decision.allowed, decision.reason) == (False, "chat_scope_not_authorized")
    get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_cancels_only_removed_actors_shared_session_task():
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    actor_task = object()
    other_pending = SimpleNamespace(source=SimpleNamespace(user_id="actor-b"))
    adapter._team_actor_active_tasks = {"actor-a": {"shared": actor_task}}
    adapter._session_tasks = {"shared": actor_task}
    adapter._pending_messages = {"shared": other_pending}
    adapter._text_debounce = {}
    adapter._pending_text_batches = {}
    adapter._pending_text_batch_tasks = {}
    adapter._pending_photo_batches = {}
    adapter._pending_photo_batch_tasks = {}
    adapter._media_group_events = {}
    adapter._media_group_tasks = {}
    adapter.cancel_session_processing = AsyncMock()
    adapter._discard_text_debounce = Mock()

    await TelegramAdapter._cancel_team_actor_sessions(adapter, "actor-a")

    adapter.cancel_session_processing.assert_awaited_once_with(
        "shared", release_guard=False, discard_pending=False
    )
    assert adapter._pending_messages["shared"] is other_pending


@pytest.mark.asyncio
async def test_revocation_purges_removed_actor_pending_without_cancelling_other_actor():
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    other_task = object()
    removed_pending = SimpleNamespace(source=SimpleNamespace(user_id="actor-a"))
    adapter._team_actor_active_tasks = {"actor-b": {"shared": other_task}}
    adapter._session_tasks = {"shared": other_task}
    adapter._pending_messages = {"shared": removed_pending}
    adapter._text_debounce = {}
    adapter._pending_text_batches = {}
    adapter._pending_text_batch_tasks = {}
    adapter._pending_photo_batches = {}
    adapter._pending_photo_batch_tasks = {}
    adapter._media_group_events = {}
    adapter._media_group_tasks = {}
    adapter.cancel_session_processing = AsyncMock()
    adapter._discard_text_debounce = Mock()

    await TelegramAdapter._cancel_team_actor_sessions(adapter, "actor-a")

    adapter.cancel_session_processing.assert_not_awaited()
    assert "shared" not in adapter._pending_messages
    adapter._discard_text_debounce.assert_called_once_with("shared")


@pytest.mark.asyncio
async def test_external_group_text_is_denied_before_ingress_side_effects():
    from gateway.platforms.telegram import TelegramAdapter

    policy, get_chat_member, _ = make_policy(status="member")
    adapter = object.__new__(TelegramAdapter)
    adapter._team_membership_policy = policy
    msg = SimpleNamespace(
        text="hello",
        from_user=SimpleNamespace(id="actor-1", is_bot=False),
        chat=SimpleNamespace(id="external-group", type="supergroup"),
    )
    adapter._effective_update_message = Mock(return_value=msg)
    adapter._is_native_voice_transcript_followup = Mock()

    await TelegramAdapter._handle_text_message(
        adapter, SimpleNamespace(update_id=1), SimpleNamespace()
    )

    adapter._is_native_voice_transcript_followup.assert_not_called()
    get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_task_owner_is_actual_actor_and_cleans_up(monkeypatch):
    from gateway.platforms.base import BasePlatformAdapter
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter._team_membership_policy = object()
    adapter._team_actor_active_tasks = {}
    adapter._team_session_task_owner = {}
    event = SimpleNamespace(source=SimpleNamespace(user_id="actor-a"))

    async def base_process(self, received_event, session_key):
        task = asyncio.current_task()
        assert self._team_session_task_owner[session_key] == ("actor-a", task)
        assert self._team_actor_active_tasks["actor-a"][session_key] is task

    monkeypatch.setattr(
        BasePlatformAdapter, "_process_message_background", base_process
    )

    await TelegramAdapter._process_message_background(adapter, event, "shared")

    assert adapter._team_session_task_owner == {}
    assert adapter._team_actor_active_tasks == {}


@pytest.mark.asyncio
async def test_shared_session_revocation_hands_guard_to_other_actor_drain():
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import MessageEvent
    from gateway.platforms.telegram import TelegramAdapter
    from gateway.session import SessionSource, build_session_key

    adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="test", typing_indicator=False)
    )
    adapter._team_membership_policy = object()
    started = {actor: asyncio.Event() for actor in ("actor-a", "actor-b")}
    release = {actor: asyncio.Event() for actor in ("actor-a", "actor-b")}

    async def handler(event):
        actor = str(event.source.user_id)
        started[actor].set()
        await release[actor].wait()
        return None

    adapter.set_message_handler(handler)

    def make_event(actor):
        return MessageEvent(
            text=actor,
            source=SessionSource(
                platform=Platform.TELEGRAM,
                chat_id="authority",
                chat_type="group",
                user_id=actor,
            ),
        )

    event_a = make_event("actor-a")
    event_b = make_event("actor-b")
    session_key = build_session_key(
        event_a.source, group_sessions_per_user=False
    )
    assert adapter._start_session_processing(event_a, session_key)
    await asyncio.wait_for(started["actor-a"].wait(), 1)
    adapter._pending_messages[session_key] = event_b

    await adapter._cancel_team_actor_sessions("actor-a")
    await asyncio.wait_for(started["actor-b"].wait(), 1)

    owner = adapter._team_session_task_owner[session_key]
    assert owner[0] == "actor-b"
    assert adapter._session_tasks[session_key] is owner[1]
    assert session_key in adapter._active_sessions
    assert not adapter._active_sessions[session_key].is_set()

    release["actor-b"].set()
    await asyncio.wait_for(owner[1], 1)
    assert session_key not in adapter._active_sessions
