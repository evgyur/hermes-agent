# Powerpack skill submodule/direct packaging

Use when creating or refactoring a skill that will be bundled into `human20team/hermes-agent-powerpack`.

## Packaging choices

- **Submodule**: separately maintained repo, separate release cadence, private boundary, standalone install path, large operational skill.
- **Direct bundled copy**: small/stable/core utility skill that should travel inside Powerpack and does not need a separate repo.
- **New scrubbed child repo + submodule**: local skill is useful but contains private runtime state, profiles, cookies, generated artifacts, private paths/accounts/chats, or secrets. Build a portable package in `/tmp`, publish a private child repo, then submodule that repo.

## Scrub before packaging

Do not ship `.git`, `.openclaw-sync-source`, caches, browser profiles, cookies, screenshots, generated media/PDFs, private host paths, account emails, chat IDs, booking/passenger data, or credential files.

For API-backed skills, credentials must be env-only. Examples must use placeholders that cannot be mistaken for real keys.

## Verification loop

Before commit/push:

```bash
python3 ~/.hermes/skills/create-skill/scripts/skill_workflow_guard.py skills/<name>
python3 -m py_compile skills/<name>/scripts/*.py  # if present
bash -n skills/<name>/scripts/*.sh                # if present
git diff --check
```

Then inspect staged diff, run a staged secret/private-data scan, commit, push, fresh-clone Powerpack, initialize touched submodules recursively, rerun guard/compile checks, and check whether CI actually ran for the pushed commit. Report `no CI ran` when GitHub returns no run for the commit.
