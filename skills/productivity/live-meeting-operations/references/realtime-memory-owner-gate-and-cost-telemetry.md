# Realtime meeting memory, owner gates, and cost telemetry

Use this reference when a low-latency voice participant must remember the room, consult broader agent memory, and report spend without leaking private context.

## Memory architecture

Use three layers; do not collapse them into one prompt.

1. **Realtime turn state** — the active WebSocket conversation for low-latency speech and barge-in.
2. **Room continuity** — append-only private JSONL containing participant transcript, agent transcript, structured notes, session lifecycle, and exclusions. Rebuild Realtime instructions from this file on every connect/reconnect.
3. **General agent memory** — split into:
   - a compact sanitized MEMORY/USER projection for immediate answers in a verified owner-only room;
   - a read-only deep lookup tool for prior sessions when the answer is not in the compact projection or room log.

A transcript written to disk but not re-injected after reconnect is storage, not working memory.

## Structured room notes

Expose a silent note tool with fields such as:

```json
{
  "category": "decision|action|open_question|fact",
  "summary": "...",
  "owner": "...|not assigned",
  "deadline": "...|not assigned",
  "evidence": "short source wording"
}
```

Direct questions to the agent outrank note-taking. The note tool must not replace an audible answer. Do not record a question addressed to the agent as an open meeting question.

## Owner-gated general memory

Fast projection requirements:

- enable only when room state proves exactly the named owner plus the named agent;
- sanitize emails, phone numbers, IPs, long IDs, credentials, tokens, secret-bearing lines, payment identifiers, and similar private fields before prompt injection;
- monitor participant composition for the lifetime of the session;
- when the boundary changes, discard the Realtime session and recreate it with or without the projection;
- never rely only on the model prompt to preserve this boundary.

Deep lookup requirements:

- re-check the room gate on every tool call;
- restrict the child agent/CLI to read-only memory and session-search capabilities;
- prohibit external actions and secret retrieval in the child prompt;
- return the result to Realtime as a function output, then explicitly create the audible response;
- treat a blocked gate or timeout as a short user-visible limitation, not permission to guess.

## Probe contamination guard

Synthetic tests can become the model's strongest remembered “decision” and make it repeat the probe on every broad question.

- Mark probes at creation time with a test/run ID whenever possible.
- After validation, exclude probe transcript, notes, and generated replies from model continuity.
- Retain them in the append-only audit log using an exclusion/tombstone record rather than deleting evidence.
- Restart the Realtime session after adding exclusions so the old prompt state is actually purged.
- Verify the rebuilt context does not contain the probe phrase before returning the agent to the room.

## Release verification

Minimum checks:

1. Inject one harmless decision; require a structured note.
2. Fully restart Realtime; ask about the decision; require the correct audible answer.
3. Ask one durable-profile fact absent from the room transcript; require an immediate answer from the sanitized projection.
4. Ask one prior-session question absent from both local layers; require a read-only deep-memory lookup and audible result.
5. Unit-test exact owner-room acceptance and rejection for extra/missing participants.
6. Test or inspect the boundary watcher so a composition change causes a session rebuild.
7. Confirm raw audio is not persisted, service is active, recording state is real, and the voice path reaches the meeting.

## Cost telemetry and claim discipline

Realtime pricing is modality-specific. Persist the complete `response.done.usage` object for every response from the first production session. Aggregate by model and day.

If historical usage was not logged:

- label the result **partial/estimated**;
- output PCM bytes can ground audio-output duration (`bytes / sample_rate / channels / bytes_per_sample`);
- transcript word counts can estimate speech duration only as a range;
- include text input/output and cached-context uncertainty;
- repeated reconnects increase uncached prompt cost;
- never turn list-price equivalence into a claim of actual cash debit.

Separate these statements:

- **actual billing path** — API key/pay-as-you-go, prepaid credits, or subscription OAuth;
- **quota consumption** — subscription/model limits;
- **list-price equivalent** — what the observed usage would cost at the official API tariff;
- **precision** — exact receipt, mapped estimate, or partial estimate.

For subscription OAuth, report incremental cash as zero only when the runtime is demonstrably using the subscription credential and no metered API billing route is attached. Still report the list-price equivalent separately when useful.

## Common failure patterns

- Fresh Realtime session with no room-log injection: claims it has no memory.
- General memory dumped into every meeting: privacy leak.
- Deep tool exposed without a per-call owner check: delayed privacy leak.
- Note tool invoked instead of answering: the agent appears evasive or mute.
- Synthetic persistence probe left in continuity: repetitive fixation.
- Retrospective “exact cost” from bytes and transcripts: false precision.
