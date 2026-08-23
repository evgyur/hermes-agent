# Enforce create-skill workflow

Если нужен жёсткий контроль, включи Hermes-native validation gate. Не использовать OpenClaw/Shaw paths as default.

## Canonical Hermes locations

- Runtime/user skills: `~/.hermes/skills/<category>/<name>/SKILL.md`
- Bundled Hermes source: `<hermes-agent-repo>/skills/<category>/<name>/SKILL.md`
- Optional official source: `<hermes-agent-repo>/optional-skills/<category>/<name>/SKILL.md`
- External dirs: configured under `skills.external_dirs`; read-only for discovery; local `~/.hermes/skills/` shadows external copies.

## Frontmatter validation

A Hermes `SKILL.md` must:

- start at byte 0 with `---`
- close frontmatter with `\n---\n`
- parse as YAML mapping
- include `name` and `description`
- keep `description` ≤ 1024 chars
- include non-empty body after frontmatter

Recommended peer-matched metadata:

```yaml
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [short, useful, tags]
    related_skills: [nearby-skill]
```

## Manual check command

For a target skill directory:

```bash
python3 ${HERMES_SKILL_DIR}/scripts/skill_workflow_guard.py <target-skill-dir>
```

If `${HERMES_SKILL_DIR}` is not available in the shell, use the absolute directory shown by `skill_view(name='create-skill')`.

## In-repo gate

For bundled/in-repo skills, additionally run the relevant Hermes tests or docs checks, then commit in the active Hermes repo. `skill_manage(action=create)` targets user-local skills, not bundled repo creation.

## Migration warning

If a skill contains `/shaw`, `/opt/clawd-workspace`, `skills/secret`, `.openclaw-*`, or `create_skill_guard.py`, treat those as legacy OpenClaw coupling. Replace with Hermes-native validation and persistence unless the user explicitly asks for OpenClaw compatibility.

---

Если check падает — skill дорабатывается через `/create-skill extract|interview|refactor`.
