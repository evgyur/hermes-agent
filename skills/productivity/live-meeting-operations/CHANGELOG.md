# Changelog

All notable changes to the canonical Human20 meeting skill are recorded here.

## 3.0.0 — 2026-08-30

- Established `human20team/human20-meeting-operations` as the canonical exact-commit source for all Human20 meeting agents and bots.
- Unified Zoom and Google Meet calls, webinars, Realtime speech-to-speech, authorized context, recording retrieval, transcript/summary, decisions/owners/deadlines, and Team20 handoff.
- Added Zoom API-first cloud artifact retrieval and explicit cross-account `NO_MATCH` handling.
- Added webinar roles, rehearsal, moderation, recording, entitlement, and post-event lifecycle.
- Added version/change governance, downstream source receipts, drift detection, and deterministic contract validation.
- Preserved `live-meeting-operations` as the compatibility-stable skill name; legacy meeting/capture workflows must delegate to it.
