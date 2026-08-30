# Google Meet live operator

## Conference creation through Calendar API

When a Calendar wrapper creates events but does not expose conferencing, use Google Calendar API directly:

```python
body = {
    "summary": title,
    "description": agenda,
    "start": {"dateTime": start_iso, "timeZone": timezone},
    "end": {"dateTime": end_iso, "timeZone": timezone},
    "conferenceData": {
        "createRequest": {
            "requestId": unique_request_id,
            "conferenceSolutionKey": {"type": "hangoutsMeet"},
        }
    },
}
event = service.events().insert(
    calendarId="primary",
    body=body,
    conferenceDataVersion=1,
    sendUpdates="none",
).execute()
```

Read the event back with `events.get`. Resolve the join URL from `hangoutLink`, or from a `conferenceData.entryPoints` item where `entryPointType == "video"`.

## Why a valid link can still show `Ask to join`

The browser account may be authenticated but not trusted for that Calendar event. If no organizer is already inside, `Ask to join` can deadlock because nobody can admit the agent.

Authorized recovery:

1. Verify the visible Google account in the pre-join page.
2. Add that exact email as an attendee to the existing Calendar event without removing current attendees.
3. Read back the attendee list.
4. Hard-refresh the same Meet URL.
5. Require the button to change to `Join now` before continuing.

This keeps the original conference URL and avoids creating a replacement room. Do not add arbitrary accounts merely to bypass admission; the user’s request to open/attend the meeting must authorize the operator identity.

## CDP joined-state proof

A reliable sequence is:

1. open the Meet URL on a verified non-RF relay;
2. inspect page text and buttons;
3. keep mic and camera off, or use `Continue without microphone and camera`;
4. click `Join now`;
5. wait for navigation/UI stabilization;
6. require an in-call `Leave call` control or equivalent joined-state signal.

Do not report success from `Ready to join?`, `Ask to join`, or an opened tab.

## Agenda in chat

Open `Chat with everyone`, focus the visible composer (`textarea` commonly has `aria-label="Send a message"`), insert the exact agenda, send it, and verify the agenda text appears in the chat panel. Keep it short enough to scan.

## Caption capture

After joining:

1. click `Turn on captions` and verify the control changes to `Turn off captions`;
2. poll the Meet tab through CDP for changed text from `[aria-live]`, `[role=log]`, `[role=alert]`, and a bounded body-text window;
3. write timestamped changed snapshots only, not every poll;
4. store JSONL under a private directory with file and directory mode `0600`/`0700`;
5. reconnect on transient CDP disconnects;
6. stop on loss of joined-state after it was previously observed, or at the hard duration limit;
7. leave the call and summarize semantically; delete raw snapshots afterward.

For long meetings, run the capture as a tracked bounded background process. Avoid a scheduler script timeout shorter than the meeting duration. A completion cron can summarize the private capture, but it must not expose raw captions or recursively schedule jobs.

## Immediate-room watcher hardening

Instant Meet rooms can return the only participant to `/landing` while the operator is waiting. Treat room liveness as a lease, not a one-time fact:

1. verify `Leave call` immediately before publishing the link;
2. keep the operator muted and maintain a bounded watcher;
3. if the room returns to `/landing`, reopen the **same** conference URL, rejoin muted, restore captions/chat state, and verify `Leave call` again;
4. disclose the recovery if the published readiness claim was temporarily false.

Admission detection must come from concrete provider UI, never a body-text substring such as `admit`, `join`, or `permission`; help text, chat messages, and stale overlays create false positives. Auto-admit only when the user authorized that exact room/audience. Otherwise notify the owner and wait.

### Multi-guest admission on current Meet builds

Meet may collapse several requests into a green `Admit N guests` badge instead of exposing an `Admit` button. The badge text can live in `.fs3avc` inside a clickable ancestor such as `.fdZ55`; that same class may also render the participant count, so **never use the first `.fs3avc` node for both purposes**.

A robust sequence is:

1. find a visible leaf whose normalized text matches `^Admit \\d+ guests?$` (or the localized equivalent);
2. click its nearest interactive/badge ancestor to open the People panel;
3. verify the waiting list contains the expected names/count;
4. click the panel's `Admit all` button;
5. if Meet opens an `Admit all?` confirmation modal, click the modal's confirm button as a second distinct action;
6. verify the waiting list cleared and the `IN THE MEETING`/contributors count increased to the expected number.

Do not report success after the first `Admit all` click: on current builds that click can only open the confirmation modal. For participant detection, prefer the People panel contributor count or an explicitly digit-only participant badge after excluding `Admit N guests`; do not parse an arbitrary first matching class.

Debounce meeting-end detection. After joined state has been observed, do not declare the meeting ended from one poll that lacks `Leave call`; UI transitions and overlays can temporarily hide it. Require a stable `/landing` URL, a missing tab, or several consecutive joined-state misses before cleanup.

For caption/wake monitoring, discover the live caption node with the harmless speech probe rather than trusting one permanent selector. On current Meet builds, caption rows may appear under `.nMcdL`, with text in `.ygicle.VbkSUe`. Preserve the speaker label, and suppress wake notifications from rows whose speaker is `You` so the agent does not wake on its own TTS.

Long TTS briefings should be split into bounded segments and decoded to the meeting audio format before participants arrive. A useful operational target for the virtual microphone path is mono 48 kHz WAV; prove the path with one short captioned phrase, then keep the longer segments staged for playback.

## Rail drift pitfall

A rail name or port recorded when scheduling can point to a different profile later. At execution time compare the actual profile path and visible Google identity against the expected profile. If they mismatch, fail that rail closed, then immediately test a known safe alternate non-RF rail while there is still time to join. The lesson is recovery with re-verification, not treating one stale rail as proof that meeting attendance is impossible.
