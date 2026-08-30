# Strategic live-meeting preflight

Use when the user wants an AI agent to create and actively participate in a strategic meeting immediately.

## Order of operations

1. Resolve the direct sources the user named: current Kanban/API, working chat/reply chain, CRM/payment facts, and live time.
2. Analyze read-only before changing boards or sending messages. Produce a compact private brief with:
   - verified commercial baseline;
   - work-in-progress and delivery hygiene;
   - product candidates and evidence;
   - role recommendations with confidence/gates rather than unsupported firing claims;
   - one proposed 20-day outcome and a 48/72-hour checkpoint.
3. Set up the room in parallel, but do not publish the link until joining and speech have been proven.
4. Enter muted/camera-off, verify joined state, turn captions on, and post the final agenda to in-call chat.
5. Test one harmless sentence through the actual audio path. Accept only if Meet/Teams/Zoom captions or an independent listener reproduces it. Mute again immediately.
6. Disclose the visible browser identity if it differs from the agent identity.
7. Start bounded watchers for admission, participant arrival, meeting end, and optional owner-addressed wake phrases. Capture raw captions only to a private `0600` file and remove them after the protocol.
8. Publish the exact join URL and state what is already proven: room joined, identity, mic/camera state, agenda visible, speech test, captions path.

## Voice-path probe

Do not infer voice readiness from a configured source name. Discover the active audio server and sources/sinks, then prove the chain:

```text
TTS artifact
  → decoded PCM/WAV with the meeting-compatible sample rate
  → playback into the selected virtual microphone sink/source
  → meeting microphone briefly unmuted
  → caption text observed in the remote meeting DOM
  → microphone muted and UI state read back
```

For Chromium/CDP, useful evidence includes:

- URL is the concrete meeting room, not `/new` or a pre-join route;
- `Leave call` is present;
- mic control changes from `Turn off microphone` to `Turn on microphone` after muting;
- caption nodes contain the spoken test phrase;
- participant count/admission state comes from a specific DOM marker, not arbitrary body digits.

## Strategic-analysis guardrails

- Current Kanban state is not enough to prove long-term employee performance; pair it with delivery evidence from chat, activity/audit history, and accepted artifacts.
- Define the Kanban denominator before quoting metrics. Distinguish a daily-report scope such as `Current Tasks`/active execution lanes from **all non-completed cards across every board**. If both are useful, report both with explicit labels; never mix their percentages or silently switch from 18 operationally active cards to 68 total open cards.
- Separate team activity from accepted outcomes. PRs, drafts, HTTP 200, generated assets, and card movement are not revenue or live acceptance.
- Prefer short delivery gates (`48h`/`72h`) when evidence supports role narrowing but not immediate dismissal.
- A 20-day goal needs one product, one monetary number, one accountable owner, one funnel, and an explicit freeze list.
- For private team meetings, put only decision-relevant synthesis in the room; never paste raw private transcripts.

## Failure modes

- **Link-first:** publishing the room before source analysis, then improvising the agenda.
- **Observer masquerading as participant:** promising to speak when only captions/listening work.
- **Unverified virtual mic:** local playback succeeds but the meeting receives silence.
- **Identity ambiguity:** the agent joins under the owner’s visible account without disclosure.
- **Activity-based firing:** using task counts alone without accepted-output and time-window evidence.
- **Infinite UI polling:** no bounded watcher or state-change notification for admission and participant arrival.
