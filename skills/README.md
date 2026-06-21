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

## Clone / install with submodules

A fresh clone contains only git pointers for submodule-backed skills until they are initialized. Do not advertise a submodule skill as ready unless `SKILL.md` exists inside that path.

Default enhanced-user install — initialize the generally useful execution/ops skills:

```bash
git clone https://github.com/human20team/hermes-agent-powerpack.git
cd hermes-agent-powerpack
git submodule update --init skills/shaw skills/server-doctor-public
```

Optional/operator skills — initialize only when the deployment needs these workflows:

```bash
git submodule update --init skills/chip-travel-agent skills/chip-browser-relay
```

Avoid `--recursive` by default: nested submodules can drag large corpora or private/operator-only material.

Verify submodule content:

```bash
for p in skills/shaw skills/server-doctor-public skills/chip-travel-agent skills/chip-browser-relay; do
  if [ -f "$p/SKILL.md" ]; then
    echo "ok: $p"
  else
    echo "missing submodule content: $p"
  fi
done
```

## Hygiene

Do not submodule public repos that contain private operational assumptions. Do not commit secrets, booking data, local paths, cookies, generated media, screenshots, PDFs, caches, or runtime artifacts into either the parent repo or the child skill repo.
