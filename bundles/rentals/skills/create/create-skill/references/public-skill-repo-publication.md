# Public skill repo publication checklist

Use when packaging a Hermes skill into a public GitHub repository.

## Build shape

- Root `SKILL.md` plus `README.md`, `LICENSE`, `.gitignore`, and CI.
- Put operational details in `references/`, copyable config in `templates/`, runnable checks in `scripts/`.
- If a runnable script has no extension (for nice CLI UX), add a tiny `.sh` wrapper too. Hermes `skill_view` linked-files discovery may surface `scripts/*.sh` but not extensionless executables.
- After writing support files, run `skill_view(name)` and inspect `linked_files`. If a template uses an uncommon compound suffix (for example `.env.example`) and does not appear, either duplicate it with a discoverable suffix (`.yaml`, `.json`, `.md`, `.sh`) or add an explicit SKILL.md link so future agents can still find it.

## Public hygiene

- Do not commit `.env`, runtime profiles, cookies, logs, browser cache, downloaded binaries, or screenshots of logged-in sessions.
- Scan for API/token formats and private paths before publishing.
- Avoid hardcoded user home paths and private deployment paths; use `$HOME`, `~`, env vars, and templates.
- CDP/browser-control examples must bind to loopback by default.

## Verification before claiming done

Run locally:

```bash
python3 tests/test_public_hygiene.py
python3 tests/test_shell_syntax.py
python3 /home/hermes/.hermes/skills/create-skill/scripts/skill_workflow_guard.py .
```

If the repo has live scripts, smoke-test both default and alternate paths on throwaway ports/profiles, then kill them and verify ports are down.

After pushing:

```bash
git clone --depth 1 https://github.com/OWNER/REPO.git /tmp/REPO-check
cd /tmp/REPO-check
python3 tests/test_public_hygiene.py
python3 tests/test_shell_syntax.py

# Wait for the CI run for the commit you just pushed, not merely the latest run
# returned immediately after push. GitHub may return the previous completed run
# for a few seconds.
sha=$(git rev-parse HEAD)
run_id=$(gh run list --repo OWNER/REPO --commit "$sha" --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$run_id" --repo OWNER/REPO --exit-status
```

If `run_id` is empty right after push, sleep a few seconds and query again by commit SHA. Do not claim CI passed from an older run.

## GitHub branch hygiene

If the repo existed before, pushing `main` may leave an old default branch such as `master`.

```bash
gh repo edit OWNER/REPO --default-branch main
git ls-remote --heads https://github.com/OWNER/REPO.git
git push origin --delete master   # only after default branch is main and master is stale
```

Do not call publication complete until the public clone and GitHub Actions pass on the final commit.
