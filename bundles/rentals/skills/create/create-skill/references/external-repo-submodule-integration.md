# External repo as a skill submodule

Use this when the user asks to “include this repo as a submodule”, “подключить репо субмодулем”, or reuse an external skill/repo inside an existing class-level skill.

## Decision rule

1. If the target skill directory is inside a real git repo and the user explicitly wants git-submodule semantics, use normal git submodule workflow.
2. If the target skill directory is a user-local Hermes skill without its own git repo, do not pretend it is a git submodule. Vendor a shallow copy under `references/<repo-slug>/`, remove `.git`, and record the upstream URL.
3. Keep the class-level skill as the hot entry point. The external repo becomes a support reference/submodule, not a new narrow top-level skill unless no umbrella exists.

## Implementation pattern

1. Identify the governing class-level skill already in play.
2. Clone the external repo to a temp dir or directly into `references/<repo-slug>/`.
3. Remove nested `.git` when vendoring into a user-local skill.
4. Add a concise reference file, usually `references/<topic>.md`, that explains:
   - upstream URL
   - local submodule path
   - when to load it
   - which files/references to read before acting
   - verification/QA gate for the parent workflow
5. Patch parent `SKILL.md` with one visible line pointing to the support file and submodule path.
6. Validate:
   - `skill_workflow_guard.py <target-skill-dir>` when available
   - `skill_view(<parent>)` shows the support file
   - `skill_view(<parent>, file_path='references/<repo-slug>/SKILL.md')` can read the vendored submodule

## Updating an existing vendored submodule

Use this when the user asks to update/sync an existing vendored repo submodule inside a skill.

1. Clone/fetch the upstream repo into a temp dir and record `git rev-parse HEAD`.
2. Compare the upstream temp tree against each local `references/<repo-slug>/` copy before overwriting, so additions/removals are visible.
3. Backup the local vendored directory to `/tmp/<skill>-<repo-slug>-backup-<timestamp>` before sync.
4. Sync with delete semantics from upstream into the vendored directory, excluding `.git/`, then remove any nested `.git`.
5. If the same submodule is intentionally duplicated under sibling umbrella skills, update every copy in the same pass or explicitly report which copies were left stale.
6. Patch the parent reference/bridge document with the upstream URL, synced commit, new required reference files, and any bundled assets paths exposed by the upstream repo.
7. Validate both structure and loading:
   - no nested `.git`
   - expected new files/assets exist
   - `skill_workflow_guard.py <target-skill-dir>` passes
   - `skill_view(<parent>, file_path='references/<repo-slug>/SKILL.md')` reads the updated submodule

## Reporting

Be precise about the boundary:

- Say “vendored as a Hermes references submodule” for user-local skill dirs.
- Do not say “git submodule” unless `.gitmodules`/git submodule was actually created.
- Report exact parent skills patched, support files added, and validation results.

## Pitfall

Do not create a long flat one-session skill just because the external repo has its own `SKILL.md`. If the parent task is already governed by a class-level skill, vendor the repo under that umbrella and add a discoverable pointer.
