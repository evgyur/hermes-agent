# Telegram button → `/goal` session-key pitfall

## Trigger

Use this when Supergoal approval buttons appear to work in Telegram (`Start now` clicked, callback logs show GoalManager started) but the user does not see a visible `/goal` start or continuation in the topic.

## Durable lesson

Telegram callback events must build the exact same `SessionSource` shape as normal inbound Telegram messages for that chat/topic.

For supergroup topics, normal message ingestion in Hermes uses:

```text
chat_type = "group"
thread_id = <topic id>
session_key = agent:main:telegram:group:<chat_id>:<thread_id>
```

A callback path that maps the same supergroup topic to `chat_type="forum"` creates a sibling key:

```text
agent:main:telegram:forum:<chat_id>:<thread_id>
```

That can make the button genuinely start `GoalManager`, but in an invisible sibling session. The user sees no goal start in the active topic and correctly reports that `/goal` did not start.

## Repro clues

Check logs for a mismatch like:

```text
Supergoal callback: started official /goal from button press
Telegram clarify button resolved ... choice='Start now'
```

Then inspect session keys / `sessions.json` / goal state for both:

```text
agent:main:telegram:group:<chat_id>:<thread_id>
agent:main:telegram:forum:<chat_id>:<thread_id>
```

If the goal exists only on the `forum:` key while the user conversation is on `group:`, the callback source builder is wrong.

## Fix pattern

Patch Telegram callback event construction so `supergroup` callbacks align with normal message ingestion:

```python
if chat_type_value == "private":
    chat_type = "dm"
elif chat_type_value == "supergroup":
    chat_type = "group"  # keep topic identity in thread_id, not chat_type="forum"
elif chat_type_value in {"group", "channel"}:
    chat_type = chat_type_value
```

Keep `thread_id` from `query.message.message_thread_id`.

## Regression test

Add a focused test for a supergroup topic callback:

```python
event = adapter._build_supergoal_callback_event(query, "Run `.supergoal/demo`.")
assert event.source.chat_type == "group"
assert event.source.thread_id == "1858"
assert build_session_key(event.source) == "agent:main:telegram:group:<chat_id>:1858"
```

Also keep the live button test that asserts the queued kickoff text is the raw goal body, not `/goal ...`, and the session reasoning override is `xhigh`.

## User-facing rule

If the user reports “button worked but goal did not start,” do not argue from logs. Treat it as a session-key visibility bug until proven otherwise. Verify where the goal state was written before claiming success.

For the preferred non-button flow, the contract is:

1. Assistant creates/updates `.supergoal/` files and sends a human handoff message containing `SUPERGOAL_GOAL_BODY:`.
2. Assistant does **not** auto-start merely because it printed that marker.
3. the user replies to that exact assistant message with bare `/goal`.
4. Gateway extracts only the marker payload from the replied message, sets official GoalManager state in the visible `group:<chat_id>:<thread_id>` session, queues the first kickoff without a leading `/goal`, and sets xhigh reasoning.
5. Supergoal terminal markers must be standalone non-fenced lines. Marker mentions inside fallback commands or prose are examples, not completion.
6. If execution reaches `FAILURE_HANDOFF` / `AUDIT_HANDOFF`, the status should be blocked/stopped, not `Goal achieved`.
