# Pipeline skill refactor pattern

Use this when a bloated skill is not just a style/procedure note, but a full operating pipeline: intake → source/state recovery → generation → deterministic gates → delivery/verify → revision/publish → artifact retention.

## Session-derived trigger

A root `SKILL.md` has drifted into a giant hot-path file with:
- router and trigger rules;
- delivery or publish safety rails;
- runtime scripts and verification commands;
- historical incident logs;
- duplicate embedded modules;
- generated artifacts/media inside the skill tree;
- many one-off corrections that are valuable but rare.

Do not treat this as a simple “make the file shorter” cleanup. The real task is separating layers while preserving safety.

## Correct sequence

1. **Snapshot first**
   - Copy current roots or skill dirs to `~/.hermes/skill-backups/...` before destructive edits.
   - Record root sizes and important paths.

2. **Separate artifacts from skills**
   - Generated outputs, previews, and cache files do not belong in `~/.hermes/skills/...`.
   - Move them to `~/.hermes/cache/...`, `/tmp/hermes-media`, or an archive path.
   - Keep compatibility symlinks only when scripts still expect old paths.

3. **Protect real templates before deleting media**
   - Classify reusable templates/assets before cleanup.
   - Add an explicit retention policy under the governing skill.
   - Cleanup scripts must refuse to touch protected template paths.
   - Example from tg: old `nanobana_*.png` generated outputs were safe to delete, but HLRU background pool and Human20 brand/cover templates were protected.

4. **Canonicalize duplicate modules before root split**
   - If two copies claim the same `name:` in frontmatter, discovery ambiguity and drift are the primary bug.
   - Pick one canonical source of truth.
   - Convert embedded copies into shims or rename/remove their `SKILL.md` so the canonical skill loads unambiguously.
   - Keep an archive of the embedded corpus for rule recovery.

5. **Split hot root into contract + cold archive**
   - Root `SKILL.md` should be the hot-path router/contract: triggers, non-negotiable safety rails, lane selector, output contract, quick tests, done criteria, and pointers.
   - Move long historical rules into `references/root-full-archive-YYYY-MM-DD.md` or topical references.
   - The archive is for rule recovery, not routine loading.

6. **Use lanes instead of loading everything**
   - `fast`: low-risk local edits; minimal required gates.
   - `strict`: source/factual/high-stakes/publish flows; full source/intent/review gates.
   - `recovery`: wrong/missing delivery, stale state, reply/source mismatch; fetch real state before resend.

7. **Validate hard, not theatrically**
   - Run skill workflow guard on edited skills.
   - Verify skill discovery/loading, especially ambiguity removal.
   - Run script compile checks and the governing regression suite.
   - Smoke-test protected renderers/assets if media/template cleanup happened.

## Red flags

- Shorter root but missing publish/delete/delivery safety rails.
- A compatibility shim still has the old `name:` and keeps discovery ambiguity alive.
- Generated media cleanup deletes reusable templates, brand assets, manifests, or golden samples.
- Fast lane becomes a loophole around source verification.
- Global `/tmp` state is treated as source of truth instead of Telegram history/reply/fetch-back.

## Good done criteria

- Root skill is under a practical hot-path size while hard safety rails remain visible.
- Full old root is archived in a cold reference.
- Duplicate canonical module ambiguity is gone.
- Protected asset policy exists before media cleanup.
- Guards/tests/regressions/render smoke checks pass.
- Final report names exact files changed, sizes before/after, and remaining caveats.
