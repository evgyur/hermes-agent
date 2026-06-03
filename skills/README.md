# Powerpack skills

Powerpack can carry skills in two forms:

1. **In-tree bundled skills** — normal directories committed directly under `skills/`.
2. **Skill submodules** — private or separately maintained skill repos mounted under `skills/<name>`.

Use submodules when a skill should have its own release cadence, privacy boundary, or standalone install path. The parent repo pins a commit; updates are explicit and reviewable.

Current submodule skills:

- `skills/chip-travel-agent` → `human20team/chip-travel-agent`
- `skills/chip-browser-relay` → `human20team/chip-browser-relay`
- `skills/shaw` → `evgyur/shaw-hermes`
- `skills/server-doctor-public` → `evgyur/server-doctor-public`

Current direct bundled skills added from local/private-safe packages:

- `skills/create-skill`
- `skills/perplex`
- `skills/bird`

## Add a skill submodule

```bash
git submodule add https://github.com/<owner>/<skill-repo>.git skills/<skill-name>
python3 ~/.hermes/skills/create-skill/scripts/skill_workflow_guard.py skills/<skill-name>
python3 -m py_compile skills/<skill-name>/scripts/*.py  # when scripts exist
git diff --check
git add .gitmodules skills/<skill-name>
git commit -m "feat(skills): add <skill-name> submodule"
```

## Update a skill submodule

```bash
git submodule update --init --recursive skills/<skill-name>
cd skills/<skill-name>
git fetch origin --prune
git checkout origin/main
cd ../..
python3 ~/.hermes/skills/create-skill/scripts/skill_workflow_guard.py skills/<skill-name>
git add skills/<skill-name>
git commit -m "chore(skills): update <skill-name>"
```

## Clone with submodules

```bash
git clone --recurse-submodules https://github.com/human20team/hermes-agent-powerpack.git
# or after a normal clone:
git submodule update --init --recursive
```

## Hygiene

Do not submodule public repos that contain private operational assumptions. Do not commit secrets, booking data, local paths, cookies, generated media, screenshots, PDFs, caches, or runtime artifacts into either the parent repo or the child skill repo.
