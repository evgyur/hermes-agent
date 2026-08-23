# Public multi-file skill hardening

Use this when packaging or reviewing a skill intended for a public repo or external install.

## Blockers to actively check

- **Single-file install trap:** if `SKILL.md` references `scripts/`, `templates/`, `references/`, or assets, a raw `SKILL.md` URL install is not enough. Document a full-directory install/clone path, or add a bootstrap that fetches required assets and fails closed when missing.
- **Privacy scanner self-blindness:** scanners that use `git ls-files` miss new untracked files before commit. Scan `git ls-files --cached --others --exclude-standard` while reviewing a dirty tree. Avoid literal forbidden tokens inside the scanner source itself; construct sentinel strings dynamically.
- **Local path leaks from recon scripts:** version output can leak `/home`, `/Users`, `/opt`, `/tmp`, `/var`, or venv paths (for example `pip --version`). Redact absolute paths before writing public artifacts.
- **Remote URL leaks:** never persist raw `remote.origin.url` in generated context for a public-safe skill; emit only `configured|not configured`.
- **Template/validator drift:** if a template is supposed to pass a validator after placeholder replacement, add a fixture that fills the template and runs the validator. Exact headings in validators must match template headings.
- **Placeholder false-green:** validators must reject bullet placeholders like `- TBD`, `- none`, `- ...`, `- {{COMMAND}}`, `<placeholder>`, not only empty sections.
- **Deliverable audit false-green:** a final audit helper must distinguish: new/changed present, deleted/missing, invalid baseline, and unchanged pre-existing. Unchanged pre-existing files are not proof that the run delivered anything unless explicitly marked verification-only.
- **Glob deliverables:** deliverable checks for `dir/*.ext` may include deleted baseline files plus new untracked files. Do not fail just because one old match was deleted if a current matching deliverable exists.
- **Invalid baseline fail-closed:** in a git repo, invalid/bogus baseline refs must exit non-zero for `changed-files`/`added-lines`/deliverable checks. Only non-git workspaces should degrade to filesystem existence.
- **Plan-only boundary:** pre-flight in a planner skill may run safe build/test/lint/typecheck checks, but must not run deploy, migration, network-mutating, payment, credential, destructive, or production commands without explicit approval.

## Public-safe derivative from a private class skill

When updating a public-safe skill from a richer private/user-specific skill, do not copy the private skill wholesale. Use this sequence:

1. Back up the public skill directory before edits.
2. Copy only class-level reusable process: evidence ladders, gates, output contracts, helper scripts, templates, and generic references.
3. Rewrite examples to neutral placeholders. Remove private names, routes, chat ids, booking identifiers, account names, hostnames, remotes, and absolute private paths.
4. Keep the root `SKILL.md` as router/contract; put transferred detail into `references/` and add one-line pointers from `SKILL.md`.
5. If copying scripts, scrub default output paths to relative/public-safe locations such as `./evidence/` and replace private sample URLs/entities with examples.
6. Run `python3 -m py_compile scripts/*.py` when scripts exist, then delete generated `__pycache__/` and `*.pyc` before final scan or packaging.
7. Run a privacy scan over every tracked and untracked package file. The scanner may allow the public skill name itself, but should still catch private person/project names, `-100...` chat ids, `/home`/`/opt`/`/srv` paths, token/secret assignments, and booking/document field assignments.
8. Verify `skill_view(<skill>)` loads and linked files are discoverable. If the skill directory is not a git repo, report local-only state instead of implying it was published.

## Minimum public skill regression suite

A no-dependency `scripts/test.sh` should cover:

1. `bash -n scripts/*.sh`.
2. Required multi-file assets exist: `SKILL.md`, key scripts, templates, references.
3. Privacy scan over tracked + untracked files.
4. `.gitignore` protects runtime dirs (`.shaw/`, `.supergoal/`) and secret-like files.
5. Validator positive fixture.
6. Validator negative fixtures: empty sections, prefix headings, missing required metadata, placeholder bullets.
7. Filled template validates.
8. Audit helper regressions: untracked deliverable present, deleted deliverable fails, unchanged pre-existing is not pass, invalid baseline fails closed, glob with new matching file passes.
9. Recon scripts under minimal env do not crash and do not emit absolute local paths.
10. `git diff --check` when inside a git repo.

Add GitHub Actions CI to run the suite on push/PR when publishing the skill.
