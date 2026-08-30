# Rescheduling a live or pre-armed meeting safely

Use when the owner changes the date/time after a room, browser host, AI participant, recording, capture, voice bridge, or scheduled startup has already been created.

## Core rule

A calendar/API time update is not a complete reschedule. Move the entire operational envelope:

- provider meeting object and participant-safe URL;
- secret host authority;
- active browser room and cloud recording;
- AI participant and Realtime bridge;
- independent capture;
- startup/notification automation;
- cleanup of obsolete host links and tabs.

## Sequence

1. Resolve the new absolute date, time, and timezone from the live clock. Read back the exact timestamp.
2. Freeze substantive capture while rescheduling:
   - stop the AI voice bridge;
   - stop the exact local recorder unit/PID;
   - do not delete a prior artifact until you know whether participants spoke.
3. If the old room is already live, use the host tab to **End meeting for all** and verify the room left in-call state. Merely closing the AI tab leaves the host/cloud recording alive.
4. Prefer updating the existing provider object when its host authority remains usable and the participant URL should stay stable. Replace the meeting only when a fresh host `start_url`, provider limitation, or failed update makes replacement strictly necessary.
5. For replacement:
   - create the new room first and retain its secret host URL in a mode-0600 file;
   - verify the participant join URL/receipt;
   - only then delete or retire the old meeting;
   - close old meeting tabs/contexts and remove obsolete host-authority files.
6. Rebind every future action to the new meeting ID and time. Disable/remove stale jobs before creating the replacement startup job.
7. Schedule preparation before the meeting (normally 10 minutes), not at the exact start. The startup job must perform the full READY gate: host start, cloud recording, separate disclosed AI participant, local capture, Realtime session, audio loopback, agenda.
8. Return only the new participant URL and exact local time. Never expose `start_url`, OAuth tokens, or separate passcodes.

## Proof

A reschedule is complete only when:

```text
old_voice_stopped
old_capture_stopped
old_live_instance_ended_or_never_started
new_provider_receipt
new_time_timezone_verified
new_participant_url_reachable
new_startup_job_enabled
old_host_authority_retired_if_replaced
```

For a meeting moved to a future day, do not keep the host or AI participant connected overnight. Start them during the bounded preflight window.

## Pitfalls

- Updating `start_time` while the old meeting is already in progress does not stop that live instance or its cloud recording.
- Deleting an old local capture may destroy real speech if anyone joined before the reschedule; preserve first, classify later.
- A one-shot job bound to the old meeting ID silently leaves the new room unattended.
- `auto_recording: cloud` is only configuration until the host starts the future meeting and UI/API proves active cloud recording.
- Creating a replacement before confirming the old room ended can leave two valid links and duplicate recordings.
