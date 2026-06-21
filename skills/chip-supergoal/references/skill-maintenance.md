# Skill maintenance

Use when a SuperGoal edits, validates, packages, or publishes Hermes skills.

## Live path first

Resolve the live `skill_dir` before trusting old paths. User-local skills may be flat or category-backed; repo-backed copies may be stale. Do not create duplicate skills because a hard-coded path failed.

## Validation

For changed skills:

- parse frontmatter
- keep root concise and directive
- move heavy detail to references
- verify linked files load
- run helper guards when available
- run focused tests/probes
- scan changed files for secrets/private runtime residue
- verify `skill_view` loads the edited skill

## Principal+ rule

New incident lessons should update canonical references and tests. Root `SKILL.md` only changes for new invariants, public markers, or dispatch rules.


## Repo/private delivery gate

If the user says `git push`, `private repo`, `publish skill`, or names a GitHub target, done requires:

1. identify the canonical working tree and whether the live skill dir is itself a git repo;
2. commit relevant changes when repo-backed;
3. push to the requested private remote/branch;
4. verify remote HEAD matches local HEAD (`git ls-remote` / fresh clone / `gh repo view` as applicable);
5. report clean `git status` or explicitly say local-only/uncommitted and why.

Never report a repo-backed skill as delivered from a non-git live skill dir without either syncing to the canonical repo or declaring the boundary.
