# Private skill repo sync pattern

Use when a live/runtime skill directory is not the canonical GitHub repository the user wants updated, or when the live checkout still points at a legacy repo name.

## Trigger signals

- User says `git push`, `private repo`, `publish`, or corrects a local-only handoff with “git push конечно”. Treat that as an instruction to finish repo delivery now.
- User names explicit targets such as `owner/skill` and another org mirror.
- Live skill dir has a remote that looks stale or legacy, e.g. a split package name that the current contract retired.
- The task is to publish a refactored skill package, not to change runtime behavior in place.

## Safe workflow

1. Treat the user's named repos as the target unless a hard blocker proves otherwise. Do not default to the live skill dir's existing `origin` if it points at a legacy/split repo.
2. Inspect target repos with `gh repo view` / `git ls-remote`; record default branch and current head.
3. Clone each target into `/tmp` or use one clean clone with multiple remotes when the repos currently share the same head.
4. Sync only the intended skill package files from the live skill dir into the clean clone.
5. Exclude runtime and generated state: `.git/`, `.shaw/`, `.pytest_cache/`, `__pycache__/`, `*.pyc`, local evidence dirs, credentials, logs, and caches.
6. If the refactor intentionally removes heavy reference packs or submodules, let the diff show those deletions, but keep lightweight integration docs and rollback/retention notes in the skill or report.
7. Add/keep a focused repo-level test that proves the skill contract: frontmatter name, slim root limits, no duplicate loadable skill names, required references, heavy packs absent, and core scripts compile or quick-verify.
8. Run verification in the clean clone: `git diff --check`, skill workflow guard, focused tests, core script smoke, and a changed-file secret/privacy scan.
9. Commit once, push to all named remotes/branches, then verify `HEAD == remote/<branch>` for each remote and `git ls-remote` returns the pushed SHA.
10. Fresh-clone each pushed repo and rerun the focused tests/guard. If GitHub Actions does not run for the commit, report `no CI ran` instead of implying hosted CI passed.

## Reporting

Report one compact line per repo:

- repo + branch
- commit SHA + message
- local verification
- fresh-clone verification
- remote-head match
- CI status or `no CI ran`

## Pitfalls

- Do not push to a legacy split repo just because the live runtime checkout still has that remote.
- Do not wholesale copy runtime state into a repo-backed skill.
- Do not delete `.github/` or CI metadata accidentally when syncing from a live skill dir that lacks those files; inspect the diff before staging.
- Do not claim public/package publication if the repos are private mirrors; say exactly which repos were pushed.
