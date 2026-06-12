# chip-supergoal execution protocol

This file is read by the executing agent at the start of the single `/goal` session and followed throughout. It is the operating manual for the autonomous run.

## The loop

Repeat across `/goal` continuations until `SUPERGOAL_RUN_COMPLETE` is printed. **Hard turn budget rule: run at most one numbered phase per assistant turn.** This is the Supergoal→Goal pipeline fix: phase work yields back to `/goal` after every `SUPERGOAL_PHASE_DONE`, so long runs do not burn the whole tool-call/context budget in one turn.

1. Read `.supergoal/STATE.md`. Find `Current phase: N`.
   - If it says `AUDIT`, skip numbered phases and run the **Final audit** below.
2. Read `.supergoal/phases/phase-N.md`. This is your full work spec.
3. Print `SUPERGOAL_PHASE_START` with the spec's metadata (phase number, name, task, mandatory commands, acceptance count, evidence types, dependencies).
4. Print `SUPERGOAL_STATUS` for human readability: current phase, percent, status, current action, check summary, latest evidence, and next step. Follow `references/supergoal-status-snapshots.md`. This is not completion proof and does not replace formal markers.
5. Do the work described in the spec. Run mandatory commands. Surface evidence into the transcript (command output last ~10 lines + exit code; file listings; key diff excerpts).
6. Print `SUPERGOAL_PHASE_VERIFY`: each acceptance criterion `pass|fail` with evidence; engineering checks (build/typecheck/lint/tests); **cleanliness checks** — run `bash .supergoal/repo-state.sh added-lines <Baseline ref>` (the complete set of added/new lines since baseline, **including uncommitted and untracked work**) and grep it for stack-specific debug patterns — `console.log`/`console.error` for JS/TS; `print(`/`pprint(` for Python; `print(`/`dump(` for Swift; `fmt.Println`/`log.Println` for Go; session TODO/FIXME added this phase; dead imports added; files changed count via `bash .supergoal/repo-state.sh changed-files <Baseline ref> | wc -l`; notable diff one-liners. Any non-zero cleanliness count triggers the same 3-strike treatment as a failed criterion unless the phase spec explicitly declares a `Cleanliness override:` line (e.g., a debug-tooling phase legitimately ships logs). The complete-working-tree comparison is documented once in `references/repo-state-comparison.md`. Include an updated `SUPERGOAL_STATUS` snapshot showing check state and latest evidence.
7. **RPD phase review.** If the phase spec declares `RPD required: yes` or the phase touches a risky area, run `RPD_PHASE_REVIEW`. Fix any gap before `SUPERGOAL_PHASE_DONE`, or mark `checked-holds` with evidence.
8. **Memory writeback check.** Anything non-obvious learned this phase? If yes, write a memory file under the detected MEM_DIR (frontmatter: `name`, `description`, `metadata.type` of `feedback`/`project`/`reference`/`user`); link it from `MEMORY.md`. Print `MEMORY_SAVED: <name>` or `MEMORY_SAVED: none`.
9. Print `SUPERGOAL_PHASE_DONE`. Update `STATE.md`: mark phase N completed; if N < total, set `Current phase: N+1`; if N == total, set `Current phase: AUDIT`; bump `Last update` timestamp; append a one-line event.
10. **Yield instead of chaining phases.** Print `SUPERGOAL_TURN_YIELD` with the next state (`phase N+1` or `AUDIT`) and a final `SUPERGOAL_STATUS` snapshot, then stop the current assistant turn. Do not start the next phase in the same turn. The `/goal` judge will see that `SUPERGOAL_RUN_COMPLETE` is still missing and will enqueue the next continuation automatically.
11. On the next `/goal` continuation, repeat from step 1.
12. When `Current phase: AUDIT`, run the **Final audit** below. Only after `AUDIT_COMPLETE`, print `SUPERGOAL_RUN_COMPLETE` with a 5-line summary. The `/goal` condition is satisfied at that point.

## Embedded RPD gates

chip-supergoal embeds RPD directly. Do not load or invoke an external `/rpd` skill. Use this protocol.

RPD is a mutation gate, not a commentary layer. Every finding must either mutate work/specs/commands/criteria/audit-fix specs, or be marked `checked-holds` with evidence.

### RPD_PHASE_REVIEW

Run after `SUPERGOAL_PHASE_VERIFY` and before `MEMORY_SAVED` when the phase spec declares `RPD required: yes` or touches a risky area: auth, payments, secrets, private data, database migrations, destructive data changes, production infra, gateway/routing/cron, recurring bugs, baseline-red recovery, or public launch.

Print:

```text
RPD_PHASE_REVIEW
Phase: <N>
Focus: <focus>
Pattern: <finding + evidence + mutation|checked-holds>
Assumption: <claim + true|false|unverified + mutation|checked-holds>
Stress test: <failure mode + mitigation mutation|checked-holds>
Integration: <touchpoints + split-brain risk + mutation|checked-holds>
Mutations applied before DONE: <list or none — checked-holds>
```

If the review finds a gap, fix it before `SUPERGOAL_PHASE_DONE` and re-run affected mandatory commands or criteria.

### RPD_FINAL_REVIEW

Run after `AUDIT_VERIFY` and before `AUDIT_COMPLETE` in every final audit round.

Print:

```text
RPD_FINAL_REVIEW
Pattern: <known/repeat failure class or checked-holds>
Assumption: <completion claim still unverified, or checked-holds>
Stress test: <path that can still break, or checked-holds>
Integration: <unchecked downstream touchpoint, or checked-holds>
Decision: complete | audit-fix-needed | handoff
```

If decision is `audit-fix-needed`, write `.supergoal/phases/audit-rpd-fix-<round>.md`, execute it inline, and then rerun the audit round. If decision is `handoff`, print `AUDIT_HANDOFF`, update `STATE.md` to `BLOCKED`, and do not print `SUPERGOAL_RUN_COMPLETE`.

## Final audit (Stage 10 — runs after the last phase, before completion)

Per-phase VERIFY blocks are self-reports. The audit closes that loophole by re-validating against the **original** `ROADMAP.md`, not against this run's own self-reports. The audit runs up to 3 rounds; on the 3rd round's failure, `AUDIT_HANDOFF`.

### Audit steps (one round)

1. Print `AUDIT_START` (round number, total phase count, criteria count, deduplicated mandatory commands to re-run).
2. Re-read `.supergoal/ROADMAP.md` and pull every phase's acceptance criteria fresh from the original plan.
3. **Phase completeness:** scan the transcript for one `SUPERGOAL_PHASE_DONE` per phase 1..N. Any missing = an `AUDIT_GAP`.
4. **Re-run aggregated mandatory commands** once each (build / typecheck / lint / full test suite — whatever the union of all phases' mandatory commands is, deduplicated). Surface last ~10 lines + exit code. Non-zero exit = an `AUDIT_GAP`.
5. **Spot-check verifiable acceptance criteria** across all phases:
   - "File X exists" / "Function Y exported" / "Config key Z set" / "No `console.log` in app code" → re-check via `ls`/`grep`/`cat`.
   - "Screenshot showed X" / "Manual smoke test passed" / non-deterministic checks → mark `trust-prior-verify`, don't re-run.
5b. **Deliverable check** — for each phase block in `.supergoal/ROADMAP.md`, parse the `**Deliverables:**` bullets. For every bullet that names a file path or glob:
   - Read `Baseline ref:` from `.supergoal/STATE.md`.
   - Run `bash .supergoal/repo-state.sh deliverable <baseline-ref> "<path>"`. It compares the **complete working tree** (committed + staged + unstaged + deleted) against the baseline and detects untracked new files separately, printing `present — <evidence>` (exit 0), `missing` / `deleted` (exit 1), `invalid baseline` (exit 2), or `unchanged — existed before baseline` (exit 3). In a git repo, invalid baselines fail closed; only non-git workspaces use filesystem existence fallback. Strategy: `references/repo-state-comparison.md`.
   - `missing`/`deleted` (exit 1), `invalid baseline` (exit 2), or `unchanged pre-existing` (exit 3) → `AUDIT_GAP: phase <N> deliverable "<bullet>" not proven as delivered by this run`, unless the roadmap explicitly marks that deliverable as pre-existing / verification-only.
   - This is repository ground truth, not transcript self-report — it catches the "agent said done but didn't ship" case the per-phase VERIFY cannot, even when the run never committed.
6. Print `AUDIT_VERIFY` with each phase's status, each command's exit, each criterion's pass/fail/trust-prior + evidence, and a `Deliverables:` block summarizing the step-5b check (`<deliverable>: present|missing` lines).

7. Run `RPD_FINAL_REVIEW`. If it decides `audit-fix-needed`, treat it like an audit gap and write `.supergoal/phases/audit-rpd-fix-<round>.md`. If it decides `handoff`, print `AUDIT_HANDOFF`, update `STATE.md` to `BLOCKED`, and stop.

### If gaps found

1. Print `AUDIT_GAPS` with the list.
2. Write `.supergoal/phases/audit-fix-<round>.md` — a focused fix spec that targets only the failing criteria. Forbid scope creep. Use the affected phases' original VERIFY as the success gate.
3. Execute the fix spec inline (same agent, same `/goal`, same per-criterion 3-strike protocol from regular phases).
4. On fix success: loop back to step 1 of the audit (round + 1).
5. On 3rd round's audit failure: print `AUDIT_HANDOFF` (full gap history, suggested next move), update `STATE.md` to `BLOCKED`, stop. Do **not** print `SUPERGOAL_RUN_COMPLETE`.

### If zero gaps

1. Compute `audit coverage`: `re_verified / (re_verified + trust_prior)` as a percentage. `re_verified` = criteria with `pass` from step 5 + deliverables marked `present` from step 5b. `trust_prior` = criteria marked `trust-prior-verify`.
2. Print `AUDIT_COMPLETE` (rounds, phases re-verified, commands re-run clean, criteria pass / trust-prior counts, **audit coverage %**).
3. Print `SUPERGOAL_RUN_COMPLETE` with the 5-line summary. If `trust_prior / (re_verified + trust_prior)` > **30%**, prepend a one-line honesty banner: `⚠ Audit coverage: <re_verified> re-verified, <trust_prior> trust-prior (<pct>%). Eyeball UI/UX before merging.` Below 30%, print the same coverage line without the warning prefix.

## Failure recovery (3-strike)

### First failure of any acceptance criterion

1. Print `FAILURE_PROBE` (phase, failed criterion, what was tried, root-cause hypothesis).
2. Append the probe to `.supergoal/STATE.md` failure log.
3. **Auto-retry the same phase once.** Inject the probe as a "Previous attempt failed because: …" preamble. Do not advance.

### Second failure (auto-retry also failed)

1. Print `FAILURE_ESCALATE`.
2. Write a focused **fix spec** at `.supergoal/phases/phase-N.fix.md`. The fix spec:
   - Targets only the failing criterion.
   - Forbids scope creep ("do not touch unrelated files").
   - Ends with the original phase's VERIFY block as the success gate.
3. Execute the fix spec inline (same agent, same `/goal` — no new dispatch).
4. On fix success: re-run the original phase's VERIFY; on pass, advance to N+1.
5. On fix failure: proceed to third-failure handling.

### Third failure (fix spec also failed)

1. Print `FAILURE_HANDOFF`: failing criterion, full probe history (three attempts), suggested next move.
2. Update `STATE.md`: `Status: BLOCKED`.
3. Stop attempting. The user takes the wheel. The `/goal` condition will not be satisfied; surface the handoff clearly so the host evaluator and user both see it.

## Mid-run interruption

If the user sends any message during the run:
- Pause at the next phase boundary (after `SUPERGOAL_PHASE_DONE`, before reading the next spec).
- Address the message.
- Ask whether to resume, revise the next phase spec, or stop.

## Memory writeback rules

See `memory_writeback_rules` section in SKILL.md. Short version:

- Save anything non-obvious a future Supergoal run on a similar task would benefit from.
- Frontmatter: `name`, `description`, `metadata.type` (feedback / project / reference / user).
- Link from `MEMORY.md`.
- Final phase always writes a `project_<slug>.md` memory.
- Never save secrets, transient task details, or ephemeral state.

## Required transcript blocks

See `references/goal-format.md` for the exact format of:
- `SUPERGOAL_PHASE_START`
- `SUPERGOAL_PHASE_VERIFY`
- `RPD_PHASE_REVIEW` when required
- `MEMORY_SAVED`
- `SUPERGOAL_PHASE_DONE`
- `SUPERGOAL_TURN_YIELD`
- `AUDIT_START` / `AUDIT_VERIFY` / `RPD_FINAL_REVIEW` / `AUDIT_GAPS` / `AUDIT_COMPLETE` / `AUDIT_HANDOFF`
- `SUPERGOAL_RUN_COMPLETE`
- `FAILURE_PROBE` / `FAILURE_ESCALATE` / `FAILURE_HANDOFF`
