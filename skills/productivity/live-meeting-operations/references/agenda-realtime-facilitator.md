# Agenda-driven Realtime facilitator

## Mandatory operating contract

A meeting started through this skill is not complete when a link exists or the agent appears in the participant list. The agent must be an active, disclosed AI participant connected to the configured Realtime voice session.

`READY` requires all of these receipts:

```text
room_created_verified
agent_joined_verified
recording_or_capture_ready
transcript_ingress_verified
realtime_session_ready
voice_egress_verified
agenda_loaded
```

If any receipt is missing, report the exact blocker immediately. Do not degrade silently to “observer”, browser tab only, or text-only notes unless the owner accepts that fallback.

## Realtime lane

Use a low-latency speech-to-speech session for turn-taking, interruption, and short answers. The session must receive:

- verified platform/room identity;
- explicit AI-participant identity;
- agenda items and desired outputs;
- current agenda state;
- rolling transcript and structured meeting notes;
- owner-gated fast memory only when the participant boundary allows it;
- a read-only deep Hermes consultation tool for questions outside the fast context.

Never pass raw credentials, hidden host links, or unrestricted owner memory into the meeting model. A third participant removes private owner memory and forces a clean session boundary.

## Agenda state machine

Each item has one state:

```text
open -> discussing -> decided
                   -> deferred
                   -> blocked
```

Persist every transition with:

```json
{
  "item": "Choose launch channel",
  "status": "decided",
  "evidence": "Telegram approved by Chip",
  "next_step": "Chip publishes Friday"
}
```

The live model uses `update_agenda_item` for these transitions. Keep agenda state separate from raw transcript and ordinary notes so it survives reconnects and can drive the closeout.

## Facilitator behavior

- At the start, confirm the meeting objective and desired final output in one sentence.
- If no agenda was supplied, ask the owner for one primary objective at the first natural pause. Do not invent it.
- Track the current item, missing decision, owner, and deadline.
- After three substantive turns that do not advance an open item, intervene briefly: name the open question and offer a concrete choice or next step.
- Do not interrupt useful discussion merely to perform process. Wait for a natural pause.
- When a decision appears implicit, ask for confirmation rather than recording a guess.
- When an action has no owner or deadline, ask for the missing field.
- Preserve full barge-in for substantive speech. If the agent's first sentence is cut off by a short acknowledgment/noise, resume from the beginning instead of abandoning the answer.
- Before the room closes, read back decisions, actions, owners, deadlines, and unresolved items. Ask for one correction pass.

## Anti-chatter test

The facilitator fails if it:

- repeats generic summaries without changing agenda state;
- answers only when called and never redirects drift;
- stores every sentence as a note;
- claims completion with an open/blocked item and no explicit defer decision;
- closes without named owners/deadlines for accepted actions;
- confuses an interrupted response with a completed one.

## End condition

The agenda is complete only when every item is `decided`, explicitly `deferred`, or `blocked` with an owner and next step. The closeout must be reconstructable from structured events after a Realtime reconnect.
