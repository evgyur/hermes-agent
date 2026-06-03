# Hermes migration audit for imported skills

Use this when a skill was copied from OpenClaw/Shaw/Claude-style workspaces or when the user asks whether a skill is Hermes-native.

## Audit checklist

1. **Frontmatter**
   - Required: `name`, `description`.
   - Recommended: `version`, `author`, `license`, `metadata.hermes.tags`, `metadata.hermes.related_skills`.
   - Description should explain trigger contexts, not just capability.

2. **Path coupling**
   - Flag and replace: `/opt/clawd-workspace`, `skills/secret`, `.openclaw-*`, `codex-home`, hardcoded user homes, or repo-specific absolute paths.
   - Prefer: `~/.hermes/skills/`, active Hermes repo paths, `${HERMES_SKILL_DIR}`, and paths returned by `skill_view`.

3. **Workflow coupling**
   - Flag mandatory `/shaw`, `create_skill_guard.py verify/seal`, or OpenClaw watchdog/baseline language.
   - Replace with Hermes primitives:
     - user-local procedural skill: `skill_manage`
     - bundled/in-repo skill: repo edit + validation/tests + git commit
     - discovery/update: `hermes skills list/search/install/check/update/reset`

4. **Supporting files**
   - Keep useful `references/`, `templates/`, `scripts/`, `assets/`.
   - Update commands inside them to Hermes paths and `${HERMES_SKILL_DIR}`.
   - Remove root-level sync markers such as `.openclaw-sync-source` when they are only legacy provenance.

5. **Verification**
   - Run the skill's helper guard if present.
   - Verify `skill_view(name)` can load the skill.
   - For CLI/gateway availability, use `hermes skills list` and a fresh session or `/reload-skills`.

## Compare verdict language

When reporting audit results, separate:

- **valid for Hermes**: passes current validator and can load.
- **Hermes-native**: uses Hermes paths, tools, persistence, and docs-aligned metadata.
- **legacy-compatible only**: works because instructions are generic, but still embeds OpenClaw/Shaw assumptions.
