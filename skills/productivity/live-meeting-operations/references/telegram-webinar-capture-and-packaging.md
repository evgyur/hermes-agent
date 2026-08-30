# Telegram webinar source, capture, and canonical package

Use when a meeting source arrives as a Telegram message, forwarded bot post, registration link, hidden text URL, or inline URL button and the requested outcome continues through recording, ASR, synthesis, and delivery.

## Required state machine

`SOURCE_RESOLVED → CAPTURE_ACTIVE_VERIFIED → RECORDING_VERIFIED → ARTIFACTS_PROCESSING | FINALIZATION_DEFERRED → PACKAGE_COMPLETE`

`RECORDING_MISSING` is valid only when the integrity phase proves the configured media path is absent or zero bytes. A scheduler/agent lock can never produce `RECORDING_MISSING`.

## 1. Resolve the exact Telegram source

Fetch the exact message through telegram-chip. The exact-message response must include:

- plain URL entities;
- `MessageEntityTextUrl.url` values;
- `inline_buttons[].buttons[].url` values;
- the exact `chat_id` and `message_id` supplied to the resolver.

Run:

```bash
python scripts/telegram_source_resolver.py \
  --message-json /private/input-message.json \
  --chat-id '<chat-id>' \
  --message-id '<message-id>' \
  --safe-receipt /private/event/source-receipt.json \
  --private-output /private/event/source-private.json \
  --resolve
```

The safe receipt is mode `0600` and contains only source identity, link type, redacted URL, URL hash, redirect status/domain chain, and final domain. Query strings, fragments, cookies, callback payloads, and stream tokens stay out of the receipt. The private resolver result is also mode `0600` and is the only artifact allowed to retain full URLs needed by the join adapter.

Fail closed when no candidate exists, more than one top candidate is genuinely ambiguous, a redirect targets loopback/private/link-local space, or the final provider is not compatible with an available join adapter.

## 2. Join and start one detached recorder

The provider adapter owns browser/API login, consent gates, room identity, and extraction of a playable provider stream. The canonical lifecycle owns state and receipts.

Before starting capture:

1. create the event state with semantic dates;
2. join muted and camera-off unless the user requested otherwise;
3. prove the provider media element/manifest is ready;
4. start exactly one detached recorder under systemd or another external supervisor;
5. write the capture receipt only after the file grows and ffprobe sees video.

Initialize:

```bash
python scripts/webinar_pipeline.py init \
  --state /private/event/state.json \
  --title 'Event title' \
  --event-date 2026-08-29 \
  --timezone Europe/Moscow \
  --scheduled-start '2026-08-29T16:00:00+03:00' \
  --capture-started-at '2026-08-29T16:25:00+03:00' \
  --source-receipt /private/event/source-receipt.json \
  --media /private/event/conference.mkv
```

Verify active capture:

```bash
python scripts/webinar_pipeline.py capture-receipt \
  --state /private/event/state.json \
  --receipt-output /private/event/capture-receipt.json \
  --media-ready true \
  --growth-window 20 \
  --require-growth
```

The receipt binds source message, link type, final domain, media readiness, before/after byte counts, growth window, and ffprobe metadata. Never put HLS manifests, cookies, bearer tokens, or registration data into it.

## 3. Two-phase finalization

Phase A is deterministic and runs before the synthesis lease:

- media exists and is not a symlink;
- positive byte size and duration;
- video stream exists;
- first/middle/last samples decode;
- SHA-256 is computed;
- `recording-receipt.json` is durably written.

Only then may Phase B acquire `--finalization-lock`, invoke an optional argv-only ASR command, and package derived artifacts.

If the lock is busy, the state becomes `FINALIZATION_DEFERRED`, the recording remains `VERIFIED`, and exit code is `75`. This is a retry signal, not a user-facing failure.

```bash
python scripts/webinar_pipeline.py finalize \
  --state /private/event/state.json \
  --package-dir /private/event/package \
  --transcript /private/event/transcript.md \
  --speaker-dir /private/event/transcripts-by-speaker \
  --summary /private/event/summary.md \
  --decisions /private/event/decisions.md \
  --ideas /private/event/ideas.md \
  --source-links /private/event/source-links.md \
  --finalization-lock /private/event/finalization.lock
```

If ASR is not complete, state is `ARTIFACTS_PROCESSING`, not missing. An optional `--asr-command-file` is a JSON argv array; shell strings are forbidden. Placeholders are `{media}`, `{transcript}`, and `{package}`.

## 4. Automatic retry without agent run-lock coupling

Schedule `webinar_finalizer_gate.py` as a recurring no-agent job or systemd timer. Its config is a JSON argv array containing the full `webinar_pipeline.py finalize ...` command.

```bash
python scripts/webinar_finalizer_gate.py /private/event/finalizer-command.json
```

Final stdout is always a Hermes no-agent decision:

- deferred/processing: `{"wakeAgent": false, ...}` and the timer retries;
- package complete: `{"wakeAgent": true, ...}` so the agent delivers the verified artifact;
- real blocker: `wakeAgent=true` with exact status/error.

Do not run a one-shot LLM finalizer that can be skipped by the agent `run_lock`. The deterministic gate owns retries; the agent is woken only for completion delivery or a proven blocker.

## 5. Canonical private meeting package

The package directory is mode `0700`; files are mode `0600`. It contains:

- `source-receipt.json`;
- `recording-receipt.json` with video/audio metadata and checksum;
- `semantic-dates.json`;
- `transcript.md`;
- `transcripts-by-speaker/` when produced;
- `summary.md`;
- `decisions.md` and/or `ideas.md` when requested;
- `source-links.md` with only safe publication/join links;
- `manifest.json` with exact byte counts and SHA-256 for every packaged file.

Semantic dates are distinct fields:

- `event_date` — when the event happened;
- `capture_started_at` — when this recorder actually began;
- `package_created_at` — when artifacts were assembled;
- `official_replay_published_at` — nullable until organizers publish a replay.

Do not infer one date from another. A late capture must remain late in the receipt even if the official replay later covers the complete event.

## 6. Completion gate

Report `PACKAGE_COMPLETE` only after:

- source receipt exists and contains a selected candidate/final domain;
- capture receipt proves provider readiness and file growth;
- recording receipt proves decode and checksum;
- transcript and every requested derived artifact exist;
- package manifest hashes exact on-disk bytes;
- a readback verifies state status and package path.

A successful command, completed ASR request, or existing video alone is not completion.
