# Media ingress contract

## Scope
Public contract for a Telegram-based Codex cockpit.

## Voice notes
### Expected behavior
- Telegram/OpenClaw accepts the voice note
- media file is saved locally on the host running the cockpit
- the cockpit turns the voice note into transcript text for Codex
- the bound Codex thread receives that transcript as normal user input

### Practical meaning
Voice messages inside a bound topic should behave like spoken text prompts for Codex.

## File attachments
### Expected behavior
- Telegram/OpenClaw accepts the inbound file
- media file is saved locally on the server/host
- the cockpit passes the file to Codex as a **local path + context**, not as a mandatory Telegram re-upload

### Contract shown to Codex
For text files, Codex should receive:
- file name
- local absolute path
- content type when available
- text preview or excerpt when safe

For non-text files, Codex should receive:
- file name
- local absolute path
- content type when available
- instruction to use the local path directly from the local workspace/runtime

## Preferred design
Preferred path:
- **Telegram → local disk → Codex local-path context**

Not preferred as the main path:
- **Telegram → Codex → Telegram outbound attachment**

Outbound file sending may still exist as a convenience feature, but it should not be the core contract.

## Debug order if media breaks
1. inbound Telegram intake
2. local media save path
3. voice transcription or file-path injection
4. Codex handoff
5. reply back to the topic
