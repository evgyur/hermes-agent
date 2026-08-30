# Webinar lifecycle: Zoom and Google Meet

Use for one-to-many sessions, registered events, streams, workshops, demos, or calls where host/panelist/attendee roles and publication matter.

## Mode resolution

Choose the provider mode from the actual entitlement/account capability, not only the user's noun “webinar.”

- **Zoom Meeting:** interactive group room; participants may be promoted/muted and can usually see each other.
- **Zoom Webinar:** host/co-host/panelist/attendee model, registration, Q&A, attendee controls, webinar-specific cloud recording.
- **Google Meet:** interactive room attached to Calendar.
- **Google Meet webinar-style/livestream:** use supported Workspace livestream/large-meeting features when available; otherwise disclose that it is an ordinary Meet and do not claim webinar controls.

Do not silently replace an explicitly requested Zoom Webinar with Zoom Meeting or a Meet livestream with an ordinary Meet.

## Pre-event contract

Verify and read back:

1. provider object ID and participant-safe URL;
2. organizer/host account and fallback host/co-host;
3. exact start/end/timezone plus rehearsal window;
4. registration, approval, capacity, admission/waiting-room policy;
5. panelists/speakers and visible agent identity;
6. attendee microphone/camera/chat/Q&A permissions;
7. screen-share source and presenter switching;
8. captions, spoken language, interpretation requirements;
9. cloud-recording layout and transcript/summary availability;
10. entitlement/privacy rules for join and post-event artifacts.

Never publish host `start_url`, OAuth credentials, registration exports, or attendee personal data.

## Rehearsal

Run through the same browser profile, account, network, audio devices, Realtime runtime, and presentation source planned for production. Prove:

- host authority and fallback-host recovery;
- agent joins under its disclosed identity;
- remote speech reaches ingress/transcription;
- generated speech reaches a remote listener or captions;
- barge-in stops agent speech;
- screen share and presenter switch are visible to an independent viewer;
- Q&A/chat moderation path works;
- recording shows active state, not merely enabled settings;
- reconnect restores agenda, transcript context, decisions, and current speaker state.

A local playback exit code, visible tab, or enabled setting is not rehearsal proof.

## Live operation

- Join before attendees, muted and camera-off unless assigned otherwise.
- Keep a separate run-of-show state: segment, speaker, planned end, next transition, open question, blocker.
- Answer only from the authorized event context envelope. Say when the evidence is missing.
- Moderate without impersonating a human: identify the agent when speaking.
- Surface time warnings and the exact next transition at natural pauses.
- Capture decisions, commitments, owners, deadlines, unanswered questions, and promised follow-up.
- Do not expose attendee lists, private chat, payments, customer records, owner memory, or unrelated project data.
- If recording/voice/context fails, state the affected lane and continue only within the remaining verified role.

## Post-event

1. Wait for provider artifacts to reach completed state.
2. Retrieve native transcript/summary/audio first; use ASR only when native transcript is absent and label provenance.
3. Produce requested deliverables: recording link, transcript, summary, decisions, action ledger, unanswered Q&A, and publication draft.
4. For Team20 actions, dedupe against live cards, resolve real members/dates, mutate only when requested, then read back.
5. Protect paid/member recordings and transcripts with entitlement checks.
6. Remove temporary raw audio/captions according to retention policy.

## Failure classifications

- `NOT_CREATED` — provider object absent or write/readback failed.
- `CREATED_NOT_REHEARSED` — valid object/link, no full rehearsal evidence.
- `REHEARSED_WITH_GAPS` — exact failed lanes listed.
- `LIVE_ACTIVE` — in-room state plus verified ingress/egress/recording/context.
- `ARTIFACTS_PROCESSING` — event ended; provider files not complete.
- `POST_EVENT_COMPLETE` — requested artifacts delivered and requested Team20 handoff verified.
