---
name: live-meeting-operations
description: "Use for every Zoom or Google Meet call/webinar lifecycle: plan, create, join, host, speak, facilitate, record, retrieve, transcribe, summarize, assign owners, and hand off to Kanban. This is the single canonical meeting root for all Human20 agents and bots; related meeting skills are subordinate adapters, never parallel lifecycles."
version: 3.0.0
author: Human20Team
license: Proprietary
metadata:
  hermes:
    tags: [human20, meetings, webinars, zoom, google-meet, realtime-voice, transcription, facilitation, kanban]
    related_skills: [calendar-and-task-operations, computer-use, chip-browser-relay, teams-meeting-pipeline, team20-ops]
    canonical_repo: human20team/human20-meeting-operations
    canonical_branch: main
    governance: exact-commit
  compatibility:
    replaces: [live-meeting-capture-operations, meeting-action-items-for-human20, zoom-cloud-recording-transcription]
---

# Live Meeting Operations

Use when any Human20 agent or bot must plan, create, join, host, facilitate, record, retrieve, transcribe, summarize, publish follow-up from, or create Team20 actions for a Zoom/Google Meet call or webinar. This skill owns the full lifecycle between source context, Calendar/provider APIs, browser attendance, Realtime voice, recordings, protocol, and Kanban.

## Canonical Human20 governance

This repository is the **single source of truth** for meeting behavior across Human20 Hermes agents and bots. Load this root first for every meeting/webinar request. Provider plugins and specialist skills may supply adapters, but they never own a second lifecycle or a conflicting completion state.

1. Canonical source: `human20team/human20-meeting-operations`, branch `main`, exact commit SHA.
2. Consumers install an exact canonical archive and persist a source receipt containing repository, commit, tree SHA, and per-file hashes. Floating/manual copies are non-compliant.
3. Changes land here first with `VERSION`, `CHANGELOG.md`, contract validation, realistic trigger tests, and secret/privacy scan. Downstream bots update only after the canonical commit exists.
4. Every bot reports the canonical commit in its release/health receipt. A skill file present without matching receipt is drift, not a healthy install.
5. Compatibility aliases (`live-meeting-capture-operations`, narrow Zoom transcript workflows, provider-specific meeting skills) must delegate here. They may not fork policy, start competing recorders, or declare completion independently.
6. This root governs Zoom calls, Zoom webinars, Google Meet calls, Google Meet livestream/webinar-style sessions, pre-call preparation, live voice participation, recording retrieval, transcript/summary, decisions/owners/deadlines, and requested Team20 handoff.

Read [`references/canonical-governance-and-rollout.md`](references/canonical-governance-and-rollout.md) before changing or distributing the skill. For webinars, load [`references/webinar-lifecycle.md`](references/webinar-lifecycle.md).

## Core invariant

A meeting is not ready because an event exists or a URL was generated. Ready means:

1. the event and conference link were created through a supported provider API and read back;
2. the intended agent identity joined without an unresolved host/admission blocker;
3. recording/capture and live transcript ingress were independently verified;
4. the GPT Realtime speech-to-speech session reported ready and audible egress reached the room;
5. the agenda was loaded into structured state and the agent is actively facilitating it.

For an unspecified provider, default to **Zoom REST API**. Use Google Meet when the user explicitly requests it. A URL, browser tab, participant-list entry, or captions-only observer never satisfies this invariant. Read `references/api-first-room-creation.md` and `references/agenda-realtime-facilitator.md` before execution.

### Human20 role and context mandate

For every Human20 meeting agent, unless the authorized organizer explicitly narrows the role:

- join with the complete audio path ready: remote-audio ingress, live transcription, wake/turn detection, virtual-mic egress, and interruption handling;
- identify the bot/agent transparently and enter muted but remain immediately answer-ready; do not equate a visible browser participant with operational attendance;
- load the meeting's authorized context envelope before joining: agenda, Team20 cards, approved documents, relevant project state, prior decisions, and participant-safe Human20 knowledge;
- actively keep the stated agenda moving: surface the next decision, unanswered question, owner, deadline, or blocker without monopolizing the floor;
- ensure provider recording is running when authorized and available, while keeping a local live transcript/decision log for the meeting protocol;
- verify each lane from live evidence at join time. If recording, audio, transcription, or agenda context is blocked, disclose that exact gap immediately instead of claiming the meeting operator is ready;
- stay attached until the room ends, then stop audio listeners, finalize the requested protocol, reconcile actions with Team20, and clean up raw temporary data under the retention rules.

Context is capability-scoped, not "everything the process can read." Shared Human20 rooms receive only public or explicitly authorized project/team context. Owner-private memory is available only in a verified owner-only room and must be removed immediately when participant composition changes. Never expose private chats, credentials, payment data, personal memory, or unrelated customer records to a shared room.

### Human20 GPT Realtime runtime

Use one durable GPT Realtime speech-to-speech service behind provider adapters. Zoom is the current default transport; Google Meet must satisfy the same contract before it may be called ready. Do not restore the old chunked Groq STT → Hermes → ElevenLabs response loop for either platform.

```bash
~/.local/bin/sigurd-voice start zoom <zoom-meeting-id>
~/.local/bin/sigurd-voice status zoom <zoom-meeting-id>
~/.local/bin/sigurd-voice start google <meet-code>
~/.local/bin/sigurd-voice status google <meet-code>
~/.local/bin/sigurd-voice logs <provider> <meeting-key>
~/.local/bin/sigurd-voice stop <provider> <meeting-key>
```

Runtime contract:

- `gpt-realtime-2.1` speech-to-speech over one persistent WebSocket;
- Zoom ingress `meet_output.monitor`; egress `agent_mic` / `agent_mic_source`;
- server VAD interrupts output; verify a `barge_in` event;
- `gpt-live-transcribe` writes compact mode-`0600` JSONL; raw audio is never persisted;
- service preflight proves in-meeting state and unmutes the virtual microphone; recording preflight must separately verify an active recording control/API state rather than trusting an `ensure-recording` click or feature-enabled text;
- the watchdog exits when the target room ends; service cleanup remutes Zoom;
- readiness evidence is `SESSION_READY`; response latency is `first_audio.latency_ms`;
- every Realtime session/reconnect must rebuild instructions from the room's persisted JSONL: recent participant/agent transcript plus structured `meeting_note` decisions, actions, owners, deadlines, blockers, and open questions. A live transcript file that is not injected back into the voice session is not memory;
- keep agenda state separate from transcript and ordinary notes. Persist `update_agenda_item` transitions (`open`, `discussing`, `decided`, `deferred`, `blocked`) with evidence and next step; restore them on every reconnect;
- if three substantive turns do not advance an open item, intervene at the next natural pause with the exact unresolved choice or next step; before closing, read back decisions, owners, deadlines, and unresolved items for correction;
- direct questions to the agent outrank silent note-taking. `record_meeting_note` must never replace an audible answer to “what did we decide?”;
- keep barge-in enabled for substantive speech, but if an answer is cancelled before its first complete sentence by a filler such as “угу/ага/да”, the next turn must resume that answer rather than route the filler to silent wait;
- prove room-memory persistence before promotion: inject one harmless decision, observe a structured note, fully restart the Realtime service, ask about that decision, and require the correct in-room answer. Do not claim meeting memory from a transcript-write receipt alone;
- expose Hermes-wide memory only through an owner boundary: verify the room has exactly the named owner plus the agent, inject only a compact sanitized MEMORY/USER profile for fast answers (redact emails, phones, IPs, long IDs, credentials, tokens, and secret-bearing lines), and keep full session-history lookup behind a read-only `consult_hermes` tool restricted to `memory,session_search`;
- Human20Bot uses the same meeting operating contract, not Chip's private persona or memory. In Human20/shared rooms, keep owner memory disabled and use only the room agenda/transcript plus public or explicitly authorized Human20 context;
- poll participant composition while the session is active. If the room stops being owner-only, restart the Realtime session without durable private memory; restore it only after the exact owner-only composition returns;
- remove synthetic memory probes from model continuity after testing while retaining them in the append-only audit log (for example with a `continuity_exclude` range), otherwise the model may anchor on and repeat the test fact;
- persist the complete `response.done.usage` payload from the first production response. If historical receipts are missing, label cost as partial and separate actual billing path, subscription quota, and API list-price equivalent.

Detailed implementation and verification pattern: [`references/realtime-memory-owner-gate-and-cost-telemetry.md`](references/realtime-memory-owner-gate-and-cost-telemetry.md).

Durable artifacts:

- `~/.config/systemd/user/sigurd-gpt-voice@.service` (Zoom)
- `~/.config/systemd/user/sigurd-gpt-meet@.service` (Google Meet)
- `~/.local/lib/sigurd-meeting/zoom_gpt_voice.py` (provider-neutral Realtime bridge; legacy filename)
- remote `/home/chip/.local/lib/sigurd-meeting/meeting_state.py` (Zoom/Meet CDP adapter)

## Canonical Zoom lifecycle

This is the single canonical Zoom root. It owns the whole lifecycle:

1. create/read back the participant room;
2. join and verify live audio/recording;
3. retrieve cloud recording artifacts through Zoom API;
4. deliver transcript, summary, decisions, owners, and deadlines;
5. hand approved tasks to Team20 Kanban with duplicate search and readback.

Do not route a post-call request to a browser password form before checking configured Zoom Server-to-Server OAuth. Do not stop after downloading a file when the user asked for synthesis or Kanban actions. For the exact API workflow and deterministic helper, load [`references/zoom-cloud-artifacts.md`](references/zoom-cloud-artifacts.md).

## Workflow

### 1. Resolve time, scope, and preparation order

- Use live system time and the stated timezone.
- When the user says “in 15 minutes,” calculate from the current clock and report the exact start time.
- If duration is absent, default a short team call to 60 minutes unless context suggests otherwise.
- Preserve the requested audience and chat/thread for the link and eventual protocol.
- If the user asks to prepare from Kanban, team chat, CRM, files, or prior decisions, inspect those direct sources **before publishing the join link or starting facilitation**. Do not substitute a generic agenda for source-backed preparation.
- Convert the preparation into a decision brief: verified facts, conflicting evidence, product/revenue signal, people/role evidence, a recommended decision, and the questions the meeting must close. Keep raw private chat out of shared artifacts.
- For an immediate meeting, parallelize read-only source analysis and room setup, but keep the microphone muted and do not invite participants until both the brief and requested speaking path are ready.

### 2. Create and read back

- If the user did not name a provider, create Zoom through Zoom REST API. Use `scripts/create_meeting.py`; do not create an ad-hoc browser room.
- Use Google Meet only when explicitly requested or when a Google Calendar conference is the actual requirement.
- Check the Calendar window for conflicts before writing when a scheduled time/audience was supplied.
- Put the meeting purpose and 3–5 concrete agenda items in the event description.
- Create conferencing through the provider's supported API, then read the exact object back. For Zoom, preflight both write and meeting-read scopes before POST. For Google, use `conferenceDataVersion=1` and resolve the video entry point from readback.
- Never publish a Zoom `start_url`; return only the participant `join_url`.
- Require confirmed status, intended start/end, and a non-empty video entry point.
- If the owner supplies a concrete meeting URL, treat it as the room of record and join that exact room. Do not create a parallel room merely because its automation path is easier.

### 3. Preflight before start

- Validate the exact browser rail, profile path, visible account, network geography, and conference page before the scheduled minute.
- Do not rely only on a one-shot cron at the exact start; use it as backup/continuation after an early preflight.
- If the preferred rail fails identity or routing checks, use a verified safe alternate rail. Never weaken profile checks or violate geography policy to make the meeting work.
- Stop at login, 2FA, legal consent, or permission gates that were not authorized.

### 4. Join, speak, and prove it

- Enter with microphone and camera off unless the user explicitly requests otherwise.
- Do not claim attendance from a pre-join page. Require an in-call indicator such as `Leave call`, joined-state text, or participant presence.
- **Zoom PWA false-negative guard:** before reporting that the agent is absent or attempting a duplicate rejoin, inspect the live `/wc/<meeting-id>/(join|start)` child frame through CDP. The top-level PWA shell may show only `Home` / `Meetings` while the child frame is already in the call with participants and a `Leave` control. Process lists, open tabs, audio streams, and top-level body text are supporting evidence, not authoritative joined-state proof.
- If the owner says “join and briefly say you connected,” treat the requested acknowledgement as **in-room speech**, not merely a chat reply. If the AI participant is already present, do not rejoin: verify its participant identity and `Leave` control in the child frame, then run the bounded `unmute → short playback → mute` wrapper and verify both final mute state and playback exit before sending a terse completion message.
- If the user requires the agent to **speak**, observer/caption capability is not enough. Before inviting participants, prove the full path `generated speech → selected virtual microphone/source → meeting uplink → live captions or remote listener` with a short harmless test phrase.
- After the speech test, mute again until a participant joins. Verify mute state from the meeting UI, not only from local audio configuration.
- Disclose the visible meeting identity whenever the browser account name differs from the agent identity; never let a host account silently impersonate the agent.
- Separate prerecorded briefing from interactive participation. Do not promise live conversational facilitation unless captions/ingress, wake or turn detection, response generation, and egress are all connected and monitored.
- Start ingress **before** the first substantive speech. Prove it with a fresh phrase spoken by another participant and an appropriate response; an outbound TTS/caption test proves only egress.
- Implement interruption below STT latency: use low-latency VAD/RMS on the remote-speaker monitor to stop tagged agent playback, then use STT for the semantic response. Long prepared material must be split into short interruptible turns.
- Calibrate output volume before the briefing. Begin conservatively and obtain a live human level check; successful transcription does not prove comfortable loudness. PulseAudio sink percentage is not an audibility receipt, and the browser may independently change virtual-source/input gain through WebRTC processing. Re-read sink volume, source volume, source-output mute/cork state, and waveform RMS/peak while the exact probe is playing.
- Never report “said in Zoom” from `paplay`/`pacat` exit code, an unmuted UI, non-zero monitor RMS, a generated model transcript, or a populated source-output alone. Those prove stages of local injection, not remote audibility. Require one independent egress receipt: a participant confirms hearing the phrase, meeting captions/transcript capture it, or an independent remote listener hears it. If the participant says they heard nothing, keep the state `audio egress not verified`, inspect the active tab and exact virtual source it consumes, bound any gain change with clipping checks, and replay one short probe before repeating long content. Stop repeated playback when no acknowledgement arrives; repetition is not verification.
- Do not infer active recording from body text containing `recording`, `Smart Recording enabled`, a visible `Record` button/menu, or Zoom AI’s temporary meeting-summary transcript. Require an active-state control such as `Pause Recording` / `Stop Recording`, a recording timer/icon, provider API state, or a recording artifact. “Recording feature enabled” and “recording currently running” are different claims.
- For authenticated Zoom PWA sessions, do not reclaim host when the human host is already active unless explicitly directed. Stay co-host, rename the browser participant to an explicit AI identity, and verify the live participant list.
- Use a bounded participant/admission watcher for immediate meetings so a waiting participant can be admitted without polling the UI manually. Keep any wake-word notification rare and rate-limited; raw captions remain private.
- Put the agreed agenda in the in-call chat when late joiners need it.
- Enable captions when available.
- Stay until the meeting ends or the agreed time budget expires; leave the room afterward.

### 5. Webinar operation

- Treat webinars as a distinct operating mode with roles: host/co-host/panelist/attendee, rehearsal window, registration/admission, presenter identity, screen-share source, moderated Q&A/chat, recording, and post-event publication.
- Verify capacity, timezone, registration policy, waiting room, panelist invitations, attendee privacy, cloud-recording layout, captions/language, and fallback host before sending invitations.
- Run a rehearsal through the same account/browser/audio path used for the live event. Prove slides/screen share, presenter switching, inbound questions, voice egress, recording-active state, and fallback host recovery.
- The agent may moderate, timekeep, answer authorized questions, summarize, and surface unanswered questions. It must not invent commitments, disclose private context, or impersonate a human speaker.
- For paid/member webinars, never expose join, recording, transcript, or download artifacts outside entitlement checks.
- Follow [`references/webinar-lifecycle.md`](references/webinar-lifecycle.md) for Zoom Webinar and Google Meet webinar-style/livestream differences.

### 6. Capture, retrieve, and summarize

- For a live room, capture only meeting-relevant speech, decisions, owners, deadlines, blockers, and open questions.
- For any Zoom cloud recording/share URL/latest-call request, check the configured Zoom Server-to-Server API **before** asking for a passcode. Run `scripts/zoom_cloud_artifacts.py` and follow `references/zoom-cloud-artifacts.md`.
- Distinguish a share URL from another account (`NO_MATCH`) from missing artifacts in the connected account. API availability does not unlock another Zoom account.
- Prefer completed native `audio_transcript`; when absent, download completed `audio_only`, verify it, and use ASR with explicit provenance.
- Treat retrieval, synthesis, delivery, and requested Kanban mutation as separate completion stages. Do not stop at a local VTT/audio file.
- Keep raw captions/transcripts local in a mode-`0600` temporary file.
- Do not send raw private transcripts to shared chats unless explicitly requested and authorized.
- Separate final output into: summary; decisions with timecodes; action items with owners/dates; blockers; open questions; and Kanban mutation state.
- If Team20 cards are requested, load `team20-ops`, inspect live boards for duplicates, resolve real member IDs, create/update cards with native due dates, then read every card back.
- If the owner asks the meeting agent to “say the summary there,” deliver it as in-room speech, not as a Telegram completion message. Ground the text first, run a short audibility check, then speak the summary through the proven realtime egress and require an independent hearing receipt before reporting success.
- Mark inaudible, ambiguous, or unconfirmed statements honestly.

## Failure handling

- A generated link plus a failed attendance job is not completion. Attempt safe live recovery immediately while the meeting window is still open.
- Treat an instant room's joined state as a lease: verify it again immediately before publishing the link. If a solo room falls back to the provider landing page while waiting, reopen the same URL, rejoin muted, restore captions/chat, and read back joined state before claiming readiness again.
- Admission watchers must match concrete provider UI (`Admit`, an aggregated `Admit N guests` badge, or a localized equivalent), not keywords in page body, chat, or help text. Aggregated admission may be multi-stage: open the badge/People panel, click `Admit all`, then confirm the modal and verify the contributor count increased. Do not confuse the admission badge with the participant counter merely because both reuse the same CSS class. Debounce meeting-end detection across stable URL/state evidence rather than one missing control.
- If a fixed rail drifted between scheduling and execution, re-resolve the current safe rail and visible identity rather than repeating the stale configuration.
- In Zoom PWA, the top-level page may expose only the shell while meeting controls live in a child frame. Resolve the live `/wc/<meeting-id>/(join|start)` frame with CDP frame-tree inspection and evaluate controls in that frame's isolated execution context; an empty shell is not a failed join.
- If an account sees `Ask to join` and nobody can admit it, repair trust/attendee state through the calendar/provider when authorized; do not loop on the same button.
- Report a blocker only after testing the safe recovery path. Include the observed UI state, not a speculative explanation.

## Verification checklist

- [ ] Exact timezone and start/end read back
- [ ] Conference URL read back
- [ ] Safe browser identity confirmed and any identity mismatch disclosed
- [ ] Mic/camera off before entry
- [ ] If speaking was requested, end-to-end speech reached meeting captions/listener
- [ ] Independent egress receipt exists; local playback/UI/Pulse state alone was not treated as remote audibility
- [ ] Fresh remote speech reached STT before interactive participation was claimed
- [ ] Barge-in stops tagged playback below semantic-STT latency and cleanup re-mutes in `finally`
- [ ] Output volume was calibrated with a human level check
- [ ] Joined-state evidence observed and rechecked immediately before publishing an instant-room link
- [ ] Admission/end watchers use concrete controls plus debounced state, not body-text substrings; aggregated guest admission is confirmed and followed by a contributor-count readback
- [ ] Source-backed decision brief completed before invitation when requested
- [ ] Agenda visible in event and/or chat
- [ ] Captions/transcript path active or limitation disclosed
- [ ] Recording-active state has a control/API/artifact receipt; feature-enabled text was not used as proof
- [ ] For post-call Zoom requests, Server-to-Server API was checked before any passcode request
- [ ] Requested transcript/audio/summary artifacts were verified and actually delivered
- [ ] Decisions, owners, deadlines, and Kanban mutations have transcript/live-board evidence
- [ ] Final protocol delivered to the requested destination
- [ ] Temporary raw notes removed

## Provider notes

- Zoom cloud recording retrieval, exact-share/latest selection, native transcript/summary/audio download, delivery, and Kanban handoff: `references/zoom-cloud-artifacts.md` + `scripts/zoom_cloud_artifacts.py`.
- API-first Zoom-default and Google Meet room creation, credential contracts, and readback: `references/api-first-room-creation.md`.
- Active agenda state machine, Realtime attachment, drift intervention, and closeout: `references/agenda-realtime-facilitator.md`.
- Google Meet creation, attendee trust repair, joined-state verification, and caption capture: `references/google-meet-live-operator.md`.
- Zoom browser/PWA full-duplex sandbox operation, isolated-frame control, live STT, volume calibration, and sub-second barge-in: `references/zoom-browser-live-operator.md`.
- Zoom meetings that require the owner-host and a disclosed AI participant simultaneously: keep host authority/cloud recording in the secret `start_url` tab and join the AI through a separate CDP browser context; use `references/zoom-dual-context-host-agent.md`.
- When the owner moves a room after it has been created, joined, recorded, or pre-armed, move the full operational envelope—not only the provider timestamp. Follow `references/meeting-reschedule-lifecycle.md` for shutdown, replacement/update, host-secret retirement, automation rebinding, and proof.
- Zoom virtual-microphone egress evidence ladder, deterministic independent-listener probe, WebRTC gain pitfalls, cleanup, and endpoint failure classification: `references/zoom-virtual-mic-egress-verification.md`.
- GPT Realtime ↔ Zoom bridge architecture, Pulse privilege-transition diagnostics, independent audibility receipts, recording-state proof, and durable lifecycle: `references/gpt-realtime-zoom-bridge.md`.
- Immediate source-backed strategy calls with verified spoken participation: `references/strategic-live-meeting-preflight.md`.
- Microsoft Teams transcript/subscription work remains governed by `teams-meeting-pipeline`; use this skill for the live room and handoff around that pipeline.

## Output Contract

Report the meeting state, provider, exact calendar time/timezone, participant join URL, verification level, and any remaining readiness limitation. Never include OAuth credentials, provider secrets, host `start_url`, private transcript text, or unredacted participant data. Distinguish `created`, `receipt-verified`, `joined`, `audio-ready`, and `fully ready` instead of collapsing them into “ready”.

## Quick Test Checklist

- [ ] Dry-run validates provider, timezone, duration, title and agenda without creating a room.
- [ ] Zoom create returns a participant `join_url`; any missing read scope is disclosed rather than treated as full readiness.
- [ ] Corrected title updates the same meeting instead of creating a duplicate.
- [ ] Browser/API proof distinguishes a reachable join page from in-room joined state.
- [ ] Any requested speech has an independent remote audibility receipt before it is reported as delivered.

## Done Criteria

- [ ] Source facts, timezone and duration match the user's request.
- [ ] Meeting creation or update has a provider receipt and participant-safe join URL.
- [ ] Verification level is stated honestly; unsupported readiness claims are absent.
- [ ] Required browser/in-room/audio/capture work is verified when requested.
- [ ] Credentials and private transcript material were not exposed.
- [ ] Temporary private capture artifacts were removed or retained only under the agreed local policy.
