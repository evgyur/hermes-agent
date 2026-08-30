# Zoom browser live operator: full-duplex sandbox runbook

Use this only for an authorized, visible browser-participant proof or emergency live operation. It is not evidence that browser automation is an approved production transport.

## 1. Exact-room rule

When the owner supplies a concrete Zoom join URL, join **that exact room first**. Do not create a parallel room because it is easier to automate. Confirm the meeting ID/title and participant names from the live UI.

If the authenticated account is already represented by another client and Zoom offers `Stay Co-Host` versus `Reclaim Host`, choose `Stay Co-Host` unless the owner explicitly asks to take over. Reclaiming host can disrupt shares, polls, and breakout rooms.

Rename the browser participant to an explicit AI identity before speaking. A duplicate owner name is misleading even if the account is authorized.

## 2. Zoom PWA frame boundary

The top-level `app.zoom.us/wc/...` document may show only the PWA shell while the actual meeting UI lives in a child frame.

CDP fallback:

1. `Page.getFrameTree` on the page target.
2. Find the child frame whose URL contains `/wc/<meeting-id>/join` or `/start`.
3. Call `Page.createIsolatedWorld` with that frame ID.
4. Run all meeting DOM inspection and clicks through `Runtime.evaluate` with the returned `contextId`.
5. Verify an in-meeting control such as `Leave`, participant count, meeting title, and the explicit AI participant identity.
6. Before declaring the agent absent or reopening the room, probe every matching live child frame. A top-level shell showing `Home` / `Meetings`, a hidden tab, or an apparently idle provider page does **not** outweigh a child frame that exposes `Leave` and live participants.
7. If the agent is already listed in the room, reuse that participant rather than creating a duplicate. For a requested short acknowledgement, execute a bounded `unmute → playback → mute` turn and read back the final `unmute my microphone` state.

A launch page is not joined-state evidence, but an empty top-level PWA shell is not failed-join evidence either. Joined state comes from the live child frame.

Avoid brittle selectors when accessible names exist. Read buttons and labels first, then click exact semantic controls such as `unmute my microphone`, `mute my microphone`, `Participants`, `Stay Co-Host`, and `Rename`.

## 3. Full-duplex proof before facilitation

A prerecorded opening is not interactive participation. Do not say “I hear you” until all four paths are live:

1. **Ingress:** remote participant audio reaches a dedicated speaker sink/monitor.
2. **Recognition:** STT produces a fresh utterance from that room.
3. **Egress:** generated speech reaches a virtual microphone selected by Zoom.
4. **Turn control:** active playback stops when another participant starts speaking.

The proof must include a fresh phrase spoken by someone else after the listener starts. Echo that phrase back or answer it. An outbound caption test proves only egress.

## 4. Audio topology

Use separate configurable devices; never assume the current default names:

- `meeting_output` (or configured equivalent): Zoom speaker sink.
- `meeting_output.monitor`: remote-audio capture source.
- `agent_mic`: playback sink for generated voice.
- `agent_mic_source`: remapped mono source selected by Zoom as microphone.

Verify defaults and active streams with `pactl info`, `pactl list short sinks`, and `pactl list short sources` before the call.

When a listener crosses `sudo`, `setpriv`, SSH, or a service boundary, restore the desktop user's audio-session environment before dropping privileges:

```bash
export HOME=/home/<desktop-user>
export USER=<desktop-user>
export LOGNAME=<desktop-user>
export XDG_RUNTIME_DIR=/run/user/<uid>
export PULSE_SERVER=unix:/run/user/<uid>/pulse/native
```

Then run the listener as that desktop user. A live `parec` process is not ingress proof: a broken session can loop while writing only a header-sized WAV (roughly 44 bytes). Before claiming the agent hears the room, capture a bounded 3–5 second probe and verify all three layers:

1. the WAV is materially larger than its header;
2. RMS/peak show non-silent remote audio;
3. STT returns a fresh phrase spoken in the meeting.

After the persistent listener starts, inject a harmless wake phrase into the local meeting-output null sink and require its wake event. This validates wake matching without transmitting the probe into the meeting; it complements, but does not replace, the fresh remote-speech proof.

Start output conservatively, but calibrate from actual sink dB and a live human check rather than hard-coding a percentage. Pulse percentages are nonlinear: one verified null-sink topology mapped `25%` to `-36.12 dB` and was inaudible, while `60%` (`-13.31 dB`) restored audibility. Use a short test phrase, raise in bounded steps, and require the participant or an independent meeting transcript to confirm hearing it before delivering a long briefing. A successful local playback or caption is not sufficient proof of comfortable remote volume.

## 5. Listener and wake path

For a bounded browser proof:

- Capture short 2–3 second chunks from the remote-speaker monitor.
- Transcribe continuously with the authorized STT provider.
- Append only compact text records to a mode-`0600` private file.
- Notify the controlling Hermes session on the agent name, an explicit request to answer, or a stop phrase.
- Deduplicate repeated chunks and rate-limit notifications.
- Do not retain raw audio by default; delete chunks immediately after transcription.
- External STT requires the meeting’s applicable consent/privacy basis. If that basis is absent, use local STT or stay non-listening.

Semantic STT latency is acceptable for answering but too slow for interruption.

## 6. Sub-second barge-in

Run a second low-latency VAD/RMS loop directly on the remote-speaker monitor while the agent is speaking:

1. The playback wrapper creates an ephemeral `agent-speaking` marker.
2. The VAD loop reads 100–250 ms PCM frames.
3. Two consecutive frames above a calibrated threshold mean a participant has started speaking.
4. Kill only the tagged agent playback process.
5. The wrapper catches the exit, removes the marker, and mutes Zoom in `finally`.
6. Emit one bounded interruption event so the reasoning lane can read the latest transcript and respond.

Do not rely on wake-word STT to stop speech; by the time a three-second chunk is transcribed, the agent has already talked over the participant.

Avoid self-barge: ingress must monitor the Zoom speaker sink, not the virtual microphone sink. Test for echo/sidetone before the meeting.

## 7. Playback wrapper contract

Every spoken response should:

1. ensure the participant identity is the AI identity;
2. unmute through the meeting UI;
3. create the speaking marker;
4. play one bounded response through the virtual-mic sink;
5. tolerate VAD-triggered termination;
6. remove the marker and mute in `finally`;
7. return local evidence: unmute result, playback exit reason, and mute result;
8. obtain an independent egress receipt: participant acknowledgement, meeting transcript/caption containing the exact phrase, or an independent remote listener.

`paplay`/`pacat` exit `0`, an unmuted Zoom control, and an active Pulse source-output prove only local injection. Never report “said it in Zoom” until the independent egress receipt exists. If the participant reports silence, inspect sink dB/mute/source routing and repeat a short test after bounded volume adjustment.

Do not run a long monologue as one uninterruptible file. Split prepared material into short turns and pause for discussion.

## 8. Admission and room state

Zoom and Meet admission controls may be multi-stage. Clicks are not proof. After admitting or joining, read back the participant list/count and names. If a device remains on “connecting” while a stale guest is still listed as waiting after confirmed admission, report a client/network connection failure rather than claiming the host is still blocking it.

## 9. Recording-state proof

Do not treat `Smart Recording enabled`, Zoom AI meeting-summary text, a visible `Record` button/menu, or generic body text containing `recording` as proof that recording is actively running. Require `Pause Recording` / `Stop Recording`, an active timer/icon, provider API state, or a finalized recording artifact. Keep live transcript evidence separate from recording evidence.

## 10. Cleanup

- Stop listener/VAD processes when the meeting ends.
- Leave any superseded room so the AI is not present in two calls.
- Remove temporary PCM/audio and speaking-marker files.
- Delete raw transcript after producing the requested protocol.
- Verify the browser participant left and audio streams stopped.
