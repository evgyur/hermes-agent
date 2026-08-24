# Employee runtime rules

## Human20 and Sreda retrieval

For requests about Human20 workshops, lessons, meetings, transcripts, digests,
Pulse, progress, homework, the Human20 skill catalog, or Sreda content and
resources, use the already registered `mcp__seya__*` tools as the authoritative
source.

- Start with the smallest matching MCP read tool and expand only when the
  returned fields are insufficient.
- When MCP returns every field needed for the answer, answer from MCP and do not
  call generic web search, a browser, or Computer Use.
- If an MCP result is incomplete, state which required fact is missing. Use a
  bounded web search only for that missing external or current fact, or when the
  user explicitly requests external/current evidence.
- Do not replace an available Human20 or Sreda MCP answer with a generic web
  result. Do not invent content when an MCP tool or authorization is missing.
- Keep Human20 reads read-only by default. For an outbound Human20 user message,
  call `preview_user_message` first and send only after explicit operator
  confirmation.
- The installed `human20-helper` skill documents the same Human20 surface and
  may be used for its supported flows; it must not store or reveal bearer tokens.

Workshop metadata fast path:

- For a lesson-count question, call mcp__seya__get_workshop_summary once and answer
  immediately from its top-level `lessonCount` field, naming the workshop.
- Do not load human20-helper for workshop metadata already exposed by MCP. Do
  not call get_section or search for a count, and never repeat the same MCP read.

Representative routing:

- a workshop, lesson, meeting, transcript, digest, progress, or homework request
  routes to the matching `mcp__seya__*` read tool;
- a Sreda content, resource, or prompt request routes to the matching
  `mcp__seya__*` read tool;
- a request that MCP fully answers stops after MCP;
- a request for a genuinely missing external/current fact may make one focused
  external-research pass and then answer or report the remaining gap.

## Telegram access

For any Telegram task involving `telegram-chip` or `chipmanager`, the employee
capability is already provisioned. Start with this terminal command:

```bash
python3 ~/.hermes/skills/telegram-chip/scripts/probe_identity.py
```

Do not inspect or reload the skill before or after this command. The command is
the complete required entrypoint. Run it once, then use its result.

For an authorized write with an exact target and text, run:

```bash
python3 ~/.hermes/skills/telegram-chip/scripts/send_and_read.py --chat-id TARGET --message TEXT
```

Pass `TARGET` and `TEXT` as separate shell arguments. This helper performs the
mandatory readback; do not rebuild its HTTP calls by hand.

Proceed only after both OK markers. Use the account and endpoint defined by the
employee skill. Never connect to any personal Telegram runtime or use Computer
Use, Telegram Desktop, Telegram Web, or browser automation for Telegram.
