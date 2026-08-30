# Changelog

All notable changes to the canonical Human20 meeting skill are recorded here.

## 4.1.3 — 2026-08-30

- Reused an existing recording-integrity receipt on automatic resume only when path, size, nanosecond mtime, device, and inode still match, avoiding repeated full hashing/decoding after a deferred finalization.

## 4.1.2 — 2026-08-30

- Kept the canonical package path absent while finalization is deferred; only the recording receipt and durable state exist until package completion.

## 4.1.1 — 2026-08-30

- Included the preflight/live capture receipt in the canonical private package whenever the state machine has one.

## 4.1.0 — 2026-08-30

- Added a receipt-backed Telegram source resolver for plain URLs, UTF-16-safe `MessageEntityTextUrl` values, inline URL buttons, and validated redirect chains.
- Added durable live-capture receipts that bind source message/type/final domain to provider media readiness, file growth, and ffprobe metadata without storing tokens.
- Added two-phase idempotent finalization: recording integrity is proven before synthesis locking; lock contention becomes `FINALIZATION_DEFERRED` and is retried by a no-agent gate.
- Added canonical private meeting packages with checksummed manifests, speaker transcripts, summaries/decisions/ideas/source links, and separate event/capture/package/replay dates.
- Added regression tests for hidden Telegram URLs, private redirect blocking, safe receipts, deferred resume, missing-recording classification, and package integrity.

## 4.0.2 — 2026-08-30

- Fixed the restricted SSH stream gate to accept the canonical `PULSE_SERVER=… exec parec|paplay …` transport emitted by the runtime.

## 4.0.1 — 2026-08-30

- Fixed the restricted SSH gate to bind `pactl` preflights to the persistent meeting PulseAudio server.
- Added reproducible persistent PulseAudio and virtual sink/source topology assets.
- Made cloud-recording activation best-effort for attendee bots; recording remains a separate host/artifact gate instead of blocking voice participation.
- Corrected Zoom host-room detection for `End` controls and active microphone controls.

## 4.0.0 — 2026-08-30

- Added the canonical provider-neutral Realtime bridge and routed its WebSocket exclusively through server-side H20 Keys URL/key configuration.
- Removed Codex/OpenAI auth-file loading and direct upstream fallback; invalid or direct-upstream Realtime targets now fail closed.
- Added optional validated `MEETING_SSH_IDENTITY` routing to every runtime and systemd SSH lane with `IdentitiesOnly=yes`.
- Added a portable runtime installer, rendered per-account systemd templates, the restricted remote SSH command gate, and the `sigurd-voice` control CLI.
- Added focused regression tests for H20 Keys URL/auth, forbidden direct auth/upstream routing, Zoom/Google service templates, SSH identity validation, and preserved meeting context/privacy behavior.

## 3.0.1 — 2026-08-30

- Preserved the deployed Human20Bot Zoom helper ABI while keeping the newer share-URL/topic API.
- Restored exact-meeting-id fail-closed selection, deterministic safe filenames, and legacy VTT conversion compatibility.
- Added the compatibility recovery reference and exact regression-guard markers used by Human20Bot.

## 3.0.0 — 2026-08-30

- Established `human20team/human20-meeting-operations` as the canonical exact-commit source for all Human20 meeting agents and bots.
- Unified Zoom and Google Meet calls, webinars, Realtime speech-to-speech, authorized context, recording retrieval, transcript/summary, decisions/owners/deadlines, and Team20 handoff.
- Added Zoom API-first cloud artifact retrieval and explicit cross-account `NO_MATCH` handling.
- Added webinar roles, rehearsal, moderation, recording, entitlement, and post-event lifecycle.
- Added version/change governance, downstream source receipts, drift detection, and deterministic contract validation.
- Preserved `live-meeting-operations` as the compatibility-stable skill name; legacy meeting/capture workflows must delegate to it.
