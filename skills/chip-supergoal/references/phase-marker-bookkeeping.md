# Phase marker bookkeeping after interrupted SuperGoal turns

Use this when a SuperGoal phase was actually verified on disk, `STATE.md` advanced, but the visible chat transcript missed one or more required markers because the assistant turn was interrupted, compacted, or delivered incompletely.

## Contract

The `/goal` evaluator reads the visible transcript, not only files. A phase is not visibly complete unless the assistant prints the required marker blocks:

- `SUPERGOAL_PHASE_START`
- `SUPERGOAL_PHASE_VERIFY`
- `MEMORY_SAVED: <name|none>`
- `SUPERGOAL_PHASE_DONE`
- `SUPERGOAL_TURN_YIELD`

## Recovery pattern

1. Read `STATE.md` first.
2. If `STATE.md` has already advanced past phase N and the report/evidence files exist, do not redo phase N work and do not start phase N+1 in the same turn.
3. Print a bookkeeping-only marker block for phase N using real evidence from the report, command logs, and `STATE.md`.
4. Include `MEMORY_SAVED: none` unless a real durable learning was saved.
5. Print `SUPERGOAL_TURN_YIELD` and stop. The next continuation can start the next phase from `STATE.md`.

## What not to do

- Do not mark the phase complete a second time in `STATE.md` unless the missing bookkeeping needs an event line.
- Do not invent command output. Quote only evidence already captured in report/logs or rerun a safe verifier if evidence is missing.
- Do not chain into the next numbered phase after emitting missing markers. One phase/bookkeeping unit per turn still holds.
- Do not ask Chip to send `/goal` again if the standing-goal wrapper is active.

## Good visible wording

`SUPERGOAL_PHASE_VERIFY` should say explicitly that this is transcript bookkeeping for an already-recorded phase, then list the evidence paths and pass/fail criteria. This makes the evaluator and Chip see why no new product edits happened in this turn.
