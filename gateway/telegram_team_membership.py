"""Generic fail-closed Telegram authority-supergroup membership policy.

The policy contains no deployment identifiers.  A caller supplies the authority
chat and Bot API lookup function from profile configuration.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any, Awaitable, Callable


_ALLOWED_STATUSES = frozenset({"member", "administrator", "creator", "owner"})
_GROUP_CHAT_TYPES = frozenset({"group", "supergroup", "forum"})
_PRIVATE_CHAT_TYPES = frozenset({"private", "dm"})


@dataclass(frozen=True)
class TeamMembershipDecision:
    """One explicit authorization result safe to stamp on an incoming event."""

    allowed: bool
    reason: str


@dataclass(frozen=True)
class _CacheEntry:
    decision: TeamMembershipDecision
    expires_at: float


class TelegramTeamMembershipPolicy:
    """Authorize Telegram actors from current authority-supergroup membership."""

    def __init__(
        self,
        *,
        authority_chat_id: str,
        get_chat_member: Callable[[str, str], Awaitable[Any]],
        allowed_group_chat_ids: Any = None,
        positive_ttl_seconds: float = 30.0,
        negative_ttl_seconds: float = 5.0,
        max_cache_entries: int = 2048,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        authority = str(authority_chat_id).strip()
        if not authority:
            raise ValueError("authority_chat_id must be nonempty")
        if positive_ttl_seconds <= 0 or negative_ttl_seconds <= 0:
            raise ValueError("membership cache TTLs must be positive")
        if max_cache_entries < 1:
            raise ValueError("max_cache_entries must be positive")
        self.authority_chat_id = authority
        raw_allowed_groups = allowed_group_chat_ids or ()
        if isinstance(raw_allowed_groups, str):
            raw_allowed_groups = raw_allowed_groups.split(",")
        self.allowed_group_chat_ids = frozenset(
            str(chat_id).strip()
            for chat_id in raw_allowed_groups
            if str(chat_id).strip()
        )
        self._get_chat_member = get_chat_member
        self._positive_ttl_seconds = float(positive_ttl_seconds)
        self._negative_ttl_seconds = float(negative_ttl_seconds)
        self._max_cache_entries = int(max_cache_entries)
        self._clock = clock
        self._cache: dict[str, _CacheEntry] = {}
        self._lookup_lock = asyncio.Lock()
        # Only in-flight users are tracked, so revocation epochs stay bounded by
        # current Bot API concurrency rather than accumulating for every member.
        self._inflight_generation: dict[str, int] = {}

    def invalidate(self, user_id: str | int | None) -> None:
        """Invalidate one actor immediately after a membership update."""

        if user_id is not None:
            key = str(user_id)
            self._cache.pop(key, None)
            if key in self._inflight_generation:
                self._inflight_generation[key] += 1

    def cached_decision(self, user_id: str | int | None) -> TeamMembershipDecision | None:
        """Return a live cached decision without performing Bot API I/O."""

        if user_id is None:
            return None
        key = str(user_id)
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._cache.pop(key, None)
            return None
        return entry.decision

    @staticmethod
    def member_is_allowed(member: Any) -> bool:
        """Return whether a Bot API ChatMember object is a current human member."""

        status_value = getattr(member, "status", "")
        status = str(getattr(status_value, "value", status_value)).lower()
        member_user = getattr(member, "user", None)
        if bool(getattr(member_user, "is_bot", False)):
            return False
        if status == "restricted":
            return bool(getattr(member, "is_member", False))
        return status in _ALLOWED_STATUSES

    async def authorize(
        self,
        *,
        user_id: str | int | None,
        source_chat_id: str | int | None,
        source_chat_type: str | None,
        sender_is_bot: bool,
    ) -> TeamMembershipDecision:
        """Resolve current membership without falling back to legacy grants."""

        if user_id is None or not str(user_id).strip():
            return TeamMembershipDecision(False, "anonymous_sender")
        if sender_is_bot:
            return TeamMembershipDecision(False, "bot_sender")

        key = str(user_id)
        source_type = str(source_chat_type or "").lower()
        source_chat = str(source_chat_id) if source_chat_id is not None else ""
        if source_type in _GROUP_CHAT_TYPES:
            if source_chat == self.authority_chat_id:
                # Telegram delivering a user-authored event from the authority
                # group is a fresh positive membership observation. Cache it
                # only for the same bounded positive TTL used by getChatMember.
                decision = TeamMembershipDecision(True, "authority_group_turn")
                self._store(key, decision)
                return decision
            if source_chat not in self.allowed_group_chat_ids:
                return TeamMembershipDecision(False, "chat_scope_not_authorized")
            # An explicitly configured shared group is a valid source scope, but
            # the actor must still be a current member of the authority group.
            # Continue through the same cached/fresh getChatMember path as DMs.
        elif source_type not in _PRIVATE_CHAT_TYPES:
            return TeamMembershipDecision(False, "chat_scope_not_authorized")

        cached = self.cached_decision(key)
        if cached is not None:
            return cached

        # Serialize cold lookups so concurrent updates for one actor cannot fan
        # out into duplicate Bot API calls.  Recheck after acquiring the lock.
        async with self._lookup_lock:
            cached = self.cached_decision(key)
            if cached is not None:
                return cached
            generation = self._inflight_generation.get(key, 0)
            self._inflight_generation[key] = generation
            try:
                decision = await self._lookup(key)
                if self._inflight_generation.get(key) != generation:
                    return TeamMembershipDecision(
                        False, "membership_invalidated_during_lookup"
                    )
                self._store(key, decision)
                return decision
            finally:
                self._inflight_generation.pop(key, None)

    async def _lookup(self, user_id: str) -> TeamMembershipDecision:
        try:
            member = await self._get_chat_member(self.authority_chat_id, user_id)
        except Exception:
            return TeamMembershipDecision(False, "membership_lookup_failed")

        member_user = getattr(member, "user", None)
        if bool(getattr(member_user, "is_bot", False)):
            return TeamMembershipDecision(False, "bot_sender")
        if self.member_is_allowed(member):
            return TeamMembershipDecision(True, "current_member")
        return TeamMembershipDecision(False, "not_current_member")

    def _store(self, user_id: str, decision: TeamMembershipDecision) -> None:
        ttl = (
            self._positive_ttl_seconds
            if decision.allowed
            else self._negative_ttl_seconds
        )
        now = self._clock()
        self._cache[user_id] = _CacheEntry(decision, now + ttl)
        if len(self._cache) <= self._max_cache_entries:
            return
        # Deterministic bounded eviction: remove the earliest-expiring entry.
        victim = min(self._cache, key=lambda key: self._cache[key].expires_at)
        self._cache.pop(victim, None)
