# SuperGoal skill-phase validation for legacy/class-level skills

Use when a SuperGoal phase edits or validates an existing Hermes skill, especially older hub/user-local skills that predate `create-skill`'s modern section contract.

## Problem pattern

`create-skill`'s helper guard is strict for newly authored skills. Older/class-level skills may load correctly and have valid frontmatter/references, while still failing the helper because the root `SKILL.md` lacks exact modern headings such as:

- `## Output Contract`
- `## Quick Test Checklist`
- `## Done Criteria`

Do not turn that into fake remediation by bloating the legacy root with decorative sections.

## SuperGoal phase rule

If a phase improves an existing class-level skill and the helper guard fails only on missing modern-section headings:

1. Classify the guard result as `non-blocking legacy-format caveat`.
2. Keep the useful skill patch/reference file.
3. Verify through structural and behavioral evidence instead:
   - `skill_view(<skill>)` load succeeds;
   - linked file appears in `linked_files` or direct readback;
   - exact content checks prove the new rule/reference is present;
   - changed-file secret scan passes;
   - phase-specific preflight passes;
   - no generated residue such as `__pycache__/` remains.
4. Record the guard output and classification in the phase report.
5. Do not add fake `Output Contract` / `Quick Test Checklist` / `Done Criteria` blocks unless the phase is explicitly refactoring that root skill into modern create-skill format.

## Path targeting rule

Always resolve the live `skill_dir` with `skill_view` before editing. User-local skills can be category-backed (`~/.hermes/skills/chip/<name>`) or hub-installed at a non-obvious path. Do not create duplicate skills because a hard-coded path or `skill_manage` short name is stale.

## Evidence shape

A good phase report includes:

```text
Helper guard: exit 1, non-blocking legacy-format caveat
Reason: missing modern create-skill sections only
Loadability: skill_view(<skill>) ok
Linked file: references/<file>.md present
Content checks: ok=true
Secret scan: ok=true, violations=[]
Preflight: ok=true
```

This preserves the useful update without training future agents to satisfy validators theatrically.
