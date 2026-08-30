# API-first room creation

## Provider selection

- Default to **Zoom REST API** whenever the user asks to create a meeting/room/link without naming a provider.
- Use Google Meet only when the user explicitly asks for Meet, a Google Calendar event is required, or Zoom is unavailable and the user accepts the fallback.
- Do not silently switch providers after publishing a link.

## One command

```bash
python scripts/create_meeting.py \
  --title "Launch decision" \
  --agenda "1. Offer 2. Owner 3. Deadline" \
  --duration 45 \
  --timezone Europe/Moscow
```

The default provider is `zoom`. Google Meet:

```bash
python scripts/create_meeting.py \
  --provider google \
  --title "Launch decision" \
  --agenda "1. Offer 2. Owner 3. Deadline" \
  --attendee owner@example.com
```

Use an explicit ISO-8601 `--start` for scheduled meetings. The script otherwise schedules two minutes ahead.

## Credentials

Zoom Server-to-Server OAuth:

```text
ZOOM_ACCOUNT_ID
ZOOM_CLIENT_ID
ZOOM_CLIENT_SECRET
```

Required app scopes:

```text
meeting:write:meeting:admin
meeting:read:meeting:admin
```

The read scope is the preferred production path because creation is not complete until an independent GET confirms the meeting and returns the canonical join URL. The script checks scopes before POST by default.

A bounded compatibility lane exists for an already-deployed write-only Zoom app: `--allow-receipt-verification`. It emits `verified=false` and `verification_level=create_receipt_requires_join_probe`; the workflow must immediately auto-join that exact URL and prove joined-state, recording, audio ingress, GPT Realtime readiness, and audio egress before publishing `READY`. Without that live probe the room remains blocked.

Google Calendar OAuth:

```text
GOOGLE_CALENDAR_CLIENT_ID
GOOGLE_CALENDAR_CLIENT_SECRET
GOOGLE_CALENDAR_REFRESH_TOKEN
```

A short-lived `GOOGLE_OAUTH_ACCESS_TOKEN` can be used for local debugging, but a refresh token is the durable path. Prefer the dedicated narrow token at `${HERMES_HOME:-~/.hermes}/google_meeting_token.json`; the creator falls back to the standard `google_token.json` only when the dedicated file is absent. Calendar scope must permit event creation. The script requests `conferenceDataVersion=1`, then reads the event back and resolves the `hangoutLink` or video entry point.

If the token is absent/revoked, run:

```bash
python scripts/setup_google_meeting_oauth.py --auth-url
# user approves the Calendar-events-only link and returns the full localhost redirect
python scripts/setup_google_meeting_oauth.py --auth-code 'FULL_REDIRECT_URL'
```

This requests only `calendar.events`, stores the refresh token with mode `0600`, and does not grant Gmail/Drive access.

Never place credentials in the skill directory, command line, chat, output JSON, or meeting notes. Do not return Zoom `start_url`; it is host-authority material. Return only the participant `join_url`, provider ID, title/start time, and the truthful verification level.

## Creation is not readiness

A valid link is only the first receipt. The orchestration flow must then:

1. create and read back the exact room;
2. start/join the agent participant on the correct transport;
3. prove joined state from provider UI/API;
4. prove audio ingress;
5. prove Realtime session ready;
6. prove audio egress into the room;
7. load agenda and owner-gated context;
8. only then publish `READY` plus the join URL.

If any component is missing, report `BLOCKED` with the failed layer. Never call a room ready merely because URL creation succeeded.

## Google account binding

OAuth `login_hint` is only a UI hint, not proof of account identity. When the user names an account:

1. generate consent with `--account expected@example.com`;
2. set `GOOGLE_MEETING_ACCOUNT=expected@example.com` in the protected runtime;
3. after event creation, compare the Calendar readback `organizer.email` to the expected account;
4. on mismatch, immediately delete the just-created event and fail closed;
5. only report the account after a live create/read/delete canary confirms it.

```bash
python scripts/setup_google_meeting_oauth.py --auth-url --account expected@example.com
python scripts/setup_google_meeting_oauth.py --auth-code 'FULL_REDIRECT_URL'
GOOGLE_MEETING_ACCOUNT=expected@example.com python scripts/create_meeting.py \
  --provider google --title "Account canary" --agenda "Create, verify, delete"
```

Do not infer the authorized account from the browser selection or token filename.

## Human20Bot protected broker

On the Human20Bot profile, do not copy owner credentials into `/home/human20team`. Use the root-owned capability broker:

```bash
sudo -n /usr/local/bin/human20-create-meeting \
  --title "Decision room" \
  --agenda "1. Decision 2. Owner 3. Deadline"
```

The broker reads credentials in the protected owner context, accepts only the meeting-creation argument allowlist, never returns Zoom `start_url`, and emits a participant join URL. Human20Bot still must auto-join and complete the readiness probe before publishing `READY`.

## Google Meet caveat

A Google Calendar conference can be valid while the agent remains behind `Ask to join`. Add the exact authorized operator identity as attendee when appropriate, preserve the same event/link, and require the live `Join now`/in-call proof described in `google-meet-live-operator.md`.
