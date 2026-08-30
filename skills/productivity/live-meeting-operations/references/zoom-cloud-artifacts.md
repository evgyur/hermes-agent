# Zoom Cloud Recording → Transcript, Summary, Decisions, Tasks

Use for any post-call request such as:

- «там есть аудио?»;
- «вытащи последнюю транскрибацию»;
- «дай саммари, ключевые решения и ответственных»;
- «создай карточки по созвону»;
- a Zoom `/rec/share/...` URL.

This is the canonical post-call branch of `live-meeting-operations`.

## Non-negotiable routing

1. **Zoom API before browser/passcode.** If `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, and `ZOOM_CLIENT_SECRET` exist, inspect the connected account before asking the user for a recording passcode.
2. For a supplied share URL, compare the canonical URL against the bounded API recording list. If it does not match, say exactly: `the supplied share URL is not in the connected Zoom account`; do not pretend the API can unlock another account.
3. If the user asks for the **latest call**, select the newest completed cloud recording after filtering by a bounded date range and optional topic. Do not select the newest scheduled meeting.
4. Retrieve the requested artifact, not the first available file. For transcript requests, prefer completed `audio_transcript`; if absent, use completed `audio_only` and ASR with explicit provenance.
5. Download and user delivery are separate completion stages. A local VTT is not completion when the user asked for transcript + summary + decisions + owners or Kanban cards.

## Deterministic helper

Use:

```bash
python scripts/zoom_cloud_artifacts.py \
  --from YYYY-MM-DD \
  --to YYYY-MM-DD \
  --topic 'optional topic fragment' \
  --out /private/0700/output \
  --download transcript,summary,next_steps
```

Exact share URL:

```bash
python scripts/zoom_cloud_artifacts.py \
  --from YYYY-MM-DD \
  --to YYYY-MM-DD \
  --share-url 'https://.../rec/share/...' \
  --out /private/0700/output \
  --download transcript,summary,next_steps,audio
```

The helper:

- gets a Server-to-Server OAuth token without printing it;
- lists active account users and their bounded recordings;
- matches an exact share URL or topic/latest recording;
- downloads native transcript, Zoom summary, Zoom next steps, audio, or one video;
- converts VTT to a readable timestamped `transcript.txt`;
- writes directories `0700`, files `0600`;
- emits only safe metadata, byte counts, hashes, artifact types, and paths.

## Retrieval sequence

1. Resolve live system date/time and requested range.
2. Run the helper with `--download none` or requested artifacts.
3. Verify meeting topic, UTC/local start time, duration, completed recording types, artifact byte size, checksum, transcript cue count, and first/last cue.
4. Read the native `zoom-summary.json` and `zoom-next-steps.json`, but treat them as aids—not final truth.
5. Read the transcript in deterministic time ranges. Separate:
   - decisions actually made;
   - proposals/brainstorms;
   - explicit commitments;
   - owners;
   - stated deadlines/cadence;
   - blockers/dependencies;
   - unresolved items.
6. Do not invent owners or dates. A named addressee is not automatically the owner. Later corrections override earlier discussion.
7. Deliver the requested output into the originating chat:
   - full transcript file only when explicitly requested or too large for inline delivery;
   - concise summary;
   - key decisions with timecodes;
   - tasks with owners and explicit deadlines/cadence;
   - unresolved owner/date fields shown as unresolved.
8. For Human20 Kanban mutation, load `team20-ops`, search all relevant boards for duplicates, create/update cards idempotently, assign real member IDs, set native due dates, and read every card back before claiming success.

## ASR fallback

If native transcript is absent but completed `audio_only` exists:

- verify remote file status/size and local bytes/hash/codec/duration;
- create a private mono 16 kHz speech copy only when provider upload size requires it;
- for Russian use `whisper-large-v3`, `language=ru`, timestamped `verbose_json`, `temperature=0`;
- state: `native Zoom transcript absent; text generated from Zoom API audio`;
- never invent diarization or speaker names.

## Privacy and cleanup

- Never expose OAuth tokens, secrets, passcodes, host `start_url`, or recording download URLs.
- Keep raw audio/transcripts in a private temporary directory.
- In shared chats, exclude unrelated personal discussion and secrets while preserving business evidence.
- Delete temporary audio and raw provider JSON after verified delivery unless retention was requested. Keep only the user-requested artifact and safe receipt.

## Done criteria

- Correct recording selected from the connected account or exact account mismatch proven.
- Requested native/fallback artifacts verified by type, size, duration/cues, and checksum.
- Summary, decisions, owners, deadlines, and uncertainties are grounded in transcript timestamps.
- Requested files were actually delivered to the originating chat.
- Requested Kanban writes were duplicate-checked, assigned, deadline-set, and read back.
