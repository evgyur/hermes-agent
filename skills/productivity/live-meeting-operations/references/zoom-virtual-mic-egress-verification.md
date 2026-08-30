# Zoom virtual-microphone egress verification

Use this after changing a live meeting agent's output route, gain, mute state, or realtime voice runtime.

## Evidence ladder

1. **Provider evidence:** the realtime provider emitted non-empty audio deltas and a transcript.
2. **Process evidence:** the player accepted PCM and exited cleanly.
3. **Pulse evidence:** capture the exact remapped source Zoom consumes while the probe plays; record RMS/peak, mute, corked state, source ID, and source-output volume.
4. **Browser evidence:** confirm the active meeting tab is unmuted and its Chromium input stream consumes the intended virtual source. Beware stale meeting tabs in the same browser.
5. **Remote-receipt evidence:** a participant acknowledges a short phrase, meeting captions capture it, or an independent second meeting client receives it after Zoom transport.

Only step 5 proves that meeting participants can hear the agent. Provider transcripts, `paplay` exit `0`, sink volume `100%`, non-zero monitor RMS, a populated source-output, and an unmuted Zoom button are diagnostics, not end-to-end proof.

## Deterministic independent-listener probe

Use this when the human reports silence or cannot provide a reliable acknowledgement.

1. Mute or stop the production bridge while preparing the probe.
2. Launch an isolated browser profile as a clearly named temporary participant such as `Audio Probe`.
3. Route that browser's speaker output to a dedicated null sink such as `probe_output`; keep its microphone and camera disabled.
4. Join computer audio and verify the probe creates sink inputs on `probe_output`.
5. Unmute the real agent participant and send one short deterministic phrase through the exact production provider/model and virtual-microphone path.
6. Capture `probe_output.monitor` only in memory. Compare half-second RMS windows and peak amplitude: a near-silent baseline followed by time-aligned speech windows is a transport receipt. When local transcription is available and privacy permits, read back the phrase as an additional check.
7. Remove the temporary participant, isolated profile, invite-link file, null-sink module, and synthetic PCM fixtures.
8. Start the production bridge only after the probe passes.

Do not persist meeting audio merely to prove transport. A short synthetic phrase plus transient in-memory capture is sufficient for the egress gate.

## Gain and WebRTC pitfalls

- Sink/output volume, virtual-source/input volume, source-output volume, and PCM amplitude are different controls.
- Zoom/WebRTC may rewrite source gain after `pactl set-source-volume`. Re-read the live value; do not trust the command that set it.
- Noise suppression or automatic gain control may attenuate synthetic speech even when the Pulse route is correct.
- If applying software PCM gain, keep it bounded and inspect clipping/peak amplitude. Treat the gain as provisional until the remote-listener probe confirms intelligibility.
- Repeated loud playback is not a substitute for proof.

## Failure classification

- Provider audio exists but no virtual-source energy: player or routing defect.
- Virtual-source energy exists but no independent-listener energy: Zoom microphone selection, mute state, browser media track, permission, stale-tab, or transport defect.
- Independent listener receives clear speech but one participant does not: agent egress is proven; isolate that participant's speaker/Bluetooth/output routing or local participant volume.

## Failure behavior and reporting

- Use one brief probe such as: `If you hear me, say: heard.`
- Bound retries and stop repetitive playback when no acknowledgement arrives; repetition is noise, not verification.
- Until remote receipt passes, report: `The model generates audio, but Zoom egress is not yet proven.`
- Do not blame the participant's client before remote receipt is proven.
- Do not encode an unresolved amplification value as a universal default.
