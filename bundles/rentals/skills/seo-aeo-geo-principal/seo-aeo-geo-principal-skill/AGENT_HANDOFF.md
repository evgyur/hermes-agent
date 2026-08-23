# Agent handoff — SEO/AEO/GEO Principal+ skill

This is a **public-clean** Hermes skill bundle. It contains no customer-specific case study, no private Git history, no SuperGoal state, no local paths, and no credentialed analytics data.

## Install

Copy the folder into a Hermes skills directory, for example:

```bash
mkdir -p ~/.hermes/skills
cp -R seo-aeo-geo-principal-skill ~/.hermes/skills/seo-aeo-geo-principal
```

Then reload/list skills in the host agent environment.

## First files to read

1. `SKILL.md` — trigger, workflow, output contract.
2. `references/principal-plus-rubric.md` — what “Principal+” means.
3. `references/scoring-model.md` — scorecard row semantics.
4. `references/measurement-adapters.md` — safe analytics boundaries.
5. `scripts/seo_audit_cli.py` — CLI entrypoint.

## Verification

```bash
pip install -r requirements.txt
bash scripts/test.sh
python3 scripts/seo_audit_cli.py fixture-report --fixture fixtures/clean-site.json
```

## Runtime boundary

This bundle is an audit and methodology skill. It does not deploy websites, mutate production, or connect to private analytics by itself.
