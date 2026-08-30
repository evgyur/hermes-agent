# Zoom: separate host authority from the AI participant

Use this pattern when the agent must create, host, cloud-record, attend, listen, and speak in the same Zoom meeting.

## Why two browser participants are required

A Server-to-Server OAuth `start_url` carries host authority and must remain secret. Opening it in the signed-in host browser may join under the owner's Zoom identity. Reusing that tab as the AI participant creates three problems:

- the visible participant is the owner, not a disclosed AI;
- mic/state automation can target the wrong identity;
- closing or renaming the host can end or destabilize cloud recording.

Use two isolated tabs/contexts instead:

1. **Host tab** — open the secret `start_url`; retain host authority and cloud recording.
2. **AI participant tab** — open the public `join_url` in a fresh CDP browser context; join as `Sigurd AI — ассистент` (or another explicitly disclosed AI name).

Never print, send, log, or return the `start_url`. Store it in a mode-0600 file only for the bounded setup, then remove it after the host tab is established.

## Proven setup sequence

1. Create the meeting with `auto_recording: cloud`, `waiting_room: false`, and the intended start/timezone. Capture the API receipt privately.
2. Open `start_url` in the approved host browser and use **Join from browser**. Verify live host controls and the visible `Recording to the cloud` state.
3. Create a separate CDP browser context with `Target.createBrowserContext`, then `Target.createTarget` using the participant `join_url`.
4. In the participant context, select **Join from browser**, set the disclosed AI name, and join muted/video-off.
5. Keep the host tab open. Verify the room shows both the owner/host and the AI participant.
6. Start the Realtime voice bridge against the **AI participant tab**, not the first matching meeting tab.
7. Start an independent bounded local audio capture from the browser output monitor.
8. Play one short disclosed sound-check through the AI mic and require all of:
   - meeting state reports joined + recording;
   - Realtime session reports ready;
   - the sound-check returns through meeting audio and appears in live transcription;
   - local FLAC grows, decodes, and is non-silent;
   - Zoom UI/ARIA says cloud recording, not merely a generic recording banner.
9. Load agenda items before the substantive discussion. Restart the Realtime bridge only after persisting agenda state if it reads continuity at session startup.

## Multi-tab state-selection rule

When more than one Zoom tab matches the same meeting ID, a selector that takes the first tab is unsafe. Prefer the participant URL containing `/join`; fall back to the first matching tab only if no participant tab exists. Name detection must accept the approved Latin and Cyrillic forms, e.g. `/Сигурд AI|Sigurd AI/i`.

The host tab remains authoritative for host/cloud-recording checks. The participant tab remains authoritative for AI mic, joined state, identity, and voice egress.

## Capture proof

A complete readiness receipt is:

```text
room_created_verified
host_tab_started_verified
cloud_recording_verified
ai_participant_joined_verified
local_capture_armed
realtime_session_ready
speech_loopback_transcribed
local_audio_non_silent
agenda_loaded
```

Do not call the room ready from a create receipt, open tab, running recorder process, or `RUNNING` PulseAudio source alone.

## Pitfalls

- `auto_recording: cloud` does not itself start a scheduled room; a host still has to start it.
- `join_before_host` is not a substitute for host authority and may not start cloud recording.
- A cloud-recording banner is useful, but the participant ARIA state `Recording to the cloud` is stronger proof.
- Open FLAC files may report unknown duration until closed; during the live gate use file growth plus decodability/volume, then verify final duration after close.
- If the host and AI share one browser profile, use a separate CDP browser context for the AI to avoid inheriting the host identity.
