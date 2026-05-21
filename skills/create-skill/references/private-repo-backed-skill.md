# Private repo-backed skill workflow

Use when a user asks for a private GitHub repository that is also a Hermes skill and source-of-truth package.

## Pattern

1. Create or verify the private GitHub repo first.
   ```bash
   gh repo view OWNER/REPO --json nameWithOwner,visibility,url,defaultBranchRef \
     || gh repo create OWNER/REPO --private --description "..." --clone
   ```
2. Build the repo as a normal Hermes skill package:
   - `SKILL.md` with valid frontmatter and concise router/governor contract.
   - `README.md` for human repo entrypoint.
   - `references/*.md` for heavy findings, repo maps, probes, and known gaps.
   - `scripts/*.sh|py` only for deterministic re-runnable probes/actions.
3. Install the local Hermes skill by symlinking the repo into `~/.hermes/skills/<skill-name>` when the repo itself is the desired editable source of truth.
   ```bash
   ln -sfn /home/hermes/workspace/<repo> /home/hermes/.hermes/skills/<skill-name>
   ```
4. Validate before pushing:
   ```bash
   python3 /home/hermes/.hermes/skills/create-skill/scripts/skill_workflow_guard.py /home/hermes/workspace/<repo>
   skill_view(name="<skill-name>")  # via tool, not shell
   ```
5. Run a secret scan before commit. At minimum catch common token prefixes (`ghp_`, `gho_`, `sk-`, `gsk_`, `pplx-`, `Bearer ...`).
6. Commit and push, then verify GitHub visibility is `PRIVATE`:
   ```bash
   git add . && git commit -m "Initial <domain> source-of-truth skill"
   git branch -M main
   git push -u origin main
   gh repo view OWNER/REPO --json nameWithOwner,visibility,url,defaultBranchRef
   ```

## Pitfalls

- Do not create a one-session skill when an umbrella/source-of-truth skill is requested. Keep the root SKILL.md class-level and put dated findings into `references/`.
- Do not store raw secrets, API keys, chat dumps, or private payloads even in a private repo.
- If using a symlinked repo skill, verify `skill_view` resolves the symlink and lists linked files.
- If a validation guard fails for missing `Quick Test Checklist` or `Done Criteria`, patch those sections into `SKILL.md` rather than bypassing the guard.

## Handoff checklist

Return:
- GitHub repo URL and visibility.
- Local repo path.
- Hermes skill path/symlink.
- Files created.
- Validation results: guard, `skill_view`, secret scan, quick probe/tests, git status.
